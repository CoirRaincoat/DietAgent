"""Authored health-preference counterexamples, not clinical or human scores."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.diners import aggregate_constraints, profile_diner
from app.agent.health_preferences import repair_health_preferences
from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.health_evidence import HealthRule, health_evidence, no_goal_regression
from app.domain.models import (
    Constraints,
    Diner,
    DinerUpdate,
    Ingredient,
    Intent,
    Recipe,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine

SESSION_ID = "b9f462d1482e4ff998a7d0d41c5c0a0a"


@pytest.mark.parametrize("word,expected", [("soy", True), ("soybean", False), ("SOY", True)])
def test_latin_preference_tokens_have_boundaries(word: str, expected: bool) -> None:
    evidence = health_evidence(dish("纯配料", (word,)), HealthRule(prefer_terms=("soy",)))
    assert bool(evidence.preferred_foods) is expected


def test_blank_terms_and_invalid_rule_fields_never_award_points() -> None:
    record = dish("白菜", ("白菜",), "vegetable")
    rule = HealthRule.from_mapping(
        {"prefer_terms": ["", None, " "], "prefer_categories": "vegetable", "note": None}
    )
    assert health_evidence(record, rule).score == 0
    assert rule.note == "" and rule.prefer_categories == ()


def test_changed_source_or_rule_cannot_reuse_cached_goal_evidence() -> None:
    record = dish("蒸白菜", ("白菜",), "vegetable")
    rule = HealthRule(prefer_categories=("vegetable",), discourage_terms=("盐",))
    assert not health_evidence(record, rule).has_caution
    record.steps = "蒸熟后加入盐。"
    assert health_evidence(record, rule).has_caution
    assert not health_evidence(record, HealthRule(prefer_categories=("vegetable",))).has_caution
    record.ingredients.append(Ingredient(name="盐", raw="盐"))
    assert health_evidence(record, rule).discouraged_foods == ("盐",)


@pytest.mark.parametrize(
    "food", ["黄豆酱", "味增", "味噌", "味曾", "鸡汁调料", "蒸鱼豉油", "高汤块", "鸡粉", "东北大酱"]
)
def test_reviewed_sodium_sources_remain_cautions(food: str) -> None:
    record = dish("蒸白菜", ("白菜", food), "vegetable")
    for goal in ("降压", "护心"):
        match = analyze_recipe(record, Constraints(health_goals=[goal])).goal_matches[0]
        assert match.status == "caution"
        assert food in match.ingredient_names


def test_muscle_ranking_does_not_rearrange_staples_for_incidental_egg() -> None:
    old = dish("米饭", ("大米",), "staple")
    egg = dish("蛋面卷", ("面粉", "鸡蛋"), "staple")
    rule = HealthRule(prefer_categories=("protein",), positive_roles=("protein",))
    evidence = health_evidence(egg, rule)
    assert evidence.has_preference and evidence.score == 0
    egg.categories = ["protein"]
    assert health_evidence(egg, rule).score == 2  # Role change invalidates the cache.
    egg.categories = ["staple"]
    result = MenuPlanner(RuleEngine()).plan(
        [old, egg], Constraints(dish_count=1, health_goals=["增肌"]), current=[old]
    )
    assert result.recipes == [old]


@pytest.mark.parametrize("first", [(-3, 0), (0, 2), (2, 4)])
@pytest.mark.parametrize("second", [(-2, 1), (0, 2), (2, 3)])
@pytest.mark.parametrize("local", [None, 1, 2])
def test_small_pool_repair_preserves_each_goal_and_is_a_fixed_point(
    first: tuple[int, int], second: tuple[int, int], local: int | None
) -> None:
    old = [dish("蛋白菜", ("鸡蛋",)), dish("豆腐白菜", ("豆腐",))]
    alternatives = [dish("鸡肉白菜", ("鸡肉",)), dish("鱼肉白菜", ("鱼肉",))]
    score_map = {
        old[0].recipe_id: first,
        old[1].recipe_id: second,
        alternatives[0].recipe_id: (4, 4),
        alternatives[1].recipe_id: (4, 4),
    }
    constraints = Constraints(dish_count=2, health_goals=["控糖", "增肌"])
    pool = [*old, *alternatives]
    common_order = {r.recipe_id: i for i, r in enumerate(pool)}
    result = repair_health_preferences(
        old,
        pool,
        constraints,
        scores=score_map,
        order=common_order,
        food_matches=lambda r, t: False,
        replace_slot=local,
    )
    assert len({r.recipe_id for r in result.recipes}) == 2
    for i, recipe in enumerate(result.recipes):
        assert no_goal_regression(score_map[recipe.recipe_id], score_map[old[i].recipe_id])
        if local is not None and i != local - 1:
            assert recipe == old[i]
    retry = repair_health_preferences(
        result.recipes,
        pool,
        constraints,
        scores=score_map,
        order=common_order,
        food_matches=lambda r, t: False,
        replace_slot=local,
    )
    assert retry.recipes == result.recipes and not retry.changed_indices


def test_multi_swap_coverage_repair_converges_without_losing_either_food() -> None:
    old = [dish("旧蛋", ("鸡蛋",)), dish("旧豆腐", ("豆腐",))]
    both = dish("合蒸蛋豆腐", ("鸡蛋", "豆腐"))
    other = dish("蒸鸡肉", ("鸡肉",))
    constraints = Constraints(
        dish_count=2, preferred_ingredients=["鸡蛋", "豆腐"], health_goals=["增肌"]
    )
    pool = [*old, both, other]
    score_map = {r.recipe_id: (0,) if r in old else (4,) for r in pool}
    result = repair_health_preferences(
        old,
        pool,
        constraints,
        scores=score_map,
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, t: any(i.name == t for i in r.ingredients),
    )
    assert result.recipes == [both, other]
    assert result.changed_indices == frozenset({0, 1})


def dish(
    key: str,
    foods: tuple[str, ...],
    role: str = "protein",
    *,
    steps: str = "将食材蒸熟。",
    meal: str = "晚餐",
) -> Recipe:
    """Construct complete developer-owned source declarations."""
    return Recipe(
        recipe_id=key,
        name=key,
        raw_ingredients="；".join(foods),
        ingredients=[Ingredient(raw=food, name=food) for food in foods],
        steps=steps,
        categories=[role],
        methods=["蒸"],
        raw_label=meal,
        meal_types=[meal],
        fingerprint=key,
        source_row=1,
    )


def test_added_muscle_goal_rechecks_a_legal_pork_slot() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    better = dish("蒸鸡胸肉", ("鸡胸肉",))
    rules = RuleEngine()
    constraints = Constraints(dish_count=1, health_goals=["增肌"])
    assert rules.evaluate(better, constraints).score > rules.evaluate(old, constraints).score
    result = MenuPlanner(rules).plan([old, better], constraints, current=[old])
    assert result.recipes == [better]
    assert "健康目标" in result.changes[0]["reason"]


def test_added_sodium_attention_retains_same_role_without_dose_evidence() -> None:
    old = dish("生抽蒸白菜", ("白菜", "生抽"), "vegetable")
    better = dish("清蒸白菜", ("白菜",), "vegetable")
    wrong_role = dish("清蒸鸡肉", ("鸡肉",))
    result = MenuPlanner(RuleEngine()).plan(
        [old, wrong_role, better],
        Constraints(dish_count=1, health_goals=["降压"]),
        current=[old],
    )
    assert result.recipes == [old]
    assert any("含钠来源待核" in reason for reason in analyze_recipe(old, Constraints(health_goals=["降压"])).goal_matches[0].reasons)


def test_heart_goal_has_source_rules_and_cautions_not_a_success_claim() -> None:
    record = dish("护心蒸鸡肉", ("鸡肉", "黄油", "盐"))
    result = analyze_recipe(record, Constraints(health_goals=["护心"]))
    match = result.goal_matches[0]
    assert match.status == "caution"
    assert {"黄油", "盐"} <= set(match.ingredient_names)
    assert match.sources
    assert "治疗效果" in match.limitation


def test_literal_salt_is_a_sodium_goal_caution_even_with_vegetables() -> None:
    result = analyze_recipe(
        dish("蒸白菜", ("白菜", "食盐"), "vegetable"),
        Constraints(health_goals=["降压"]),
    )
    assert result.goal_matches[0].status == "caution"
    assert "食盐" in result.goal_matches[0].ingredient_names


def test_step_only_sodium_cannot_disappear_from_goal_explanation() -> None:
    record = dish("蒸白菜", ("白菜",), "vegetable", steps="蒸熟后加入鱼露，再拌匀。")
    result = analyze_recipe(record, Constraints(health_goals=["降压"]))
    assert result.goal_matches[0].status == "caution"
    assert any("鱼露" in reason and "步骤" in reason for reason in result.goal_matches[0].reasons)


def test_stale_protein_metadata_and_fish_condiment_do_not_earn_muscle_points() -> None:
    record = dish("蒸魚菜", ("蒸鱼豉油", "鸡腿菇"))
    result = RuleEngine().evaluate(record, Constraints(health_goals=["增肌"]))
    assert result.allowed
    assert result.score == 0


def test_source_declared_frying_is_not_hidden_by_stale_steam_metadata() -> None:
    record = dish("蒸鸡肉", ("鸡肉",), steps="放入油锅油炸熟透。")
    result = analyze_recipe(record, Constraints(health_goals=["减脂"]))
    assert result.goal_matches[0].status == "caution"
    assert "炸" in result.goal_matches[0].methods


def test_suggestions_do_not_treat_soy_presence_as_measured_sodium_regression() -> None:
    good = dish("清蒸白菜", ("白菜",), "vegetable")
    worse = dish("酱油蒸白菜", ("白菜", "酱油"), "vegetable")
    assert (
        replacement_candidates(
            [good], [worse], "stable", constraints=Constraints(dish_count=1, health_goals=["降压"])
        )
        == [worse]
    )


def test_suggestions_preserve_a_covered_food_even_when_health_scores_tie() -> None:
    good = dish("蒸鸡蛋", ("鸡蛋",))
    tied = dish("蒸鸡胸肉", ("鸡胸肉",))
    constraints = Constraints(dish_count=1, health_goals=["增肌"], preferred_ingredients=["鸡蛋"])
    assert replacement_candidates([good], [tied], "stable", constraints=constraints) == []


def test_conflicting_goals_cannot_be_hidden_by_a_larger_combined_score() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    conflict = dish("甜蒸鸡肉", ("鸡胸肉", "白糖"))
    constraints = Constraints(dish_count=1, health_goals=["增肌", "控糖"])
    result = MenuPlanner(RuleEngine()).plan([old, conflict], constraints, current=[old])
    assert result.recipes == [old]
    assert result.changes == []


def test_covered_food_preference_is_preserved_before_health_improvement() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    better = dish("蒸鸡胸肉", ("鸡胸肉",))
    constraints = Constraints(dish_count=1, preferred_ingredients=["猪肉"], health_goals=["增肌"])
    result = MenuPlanner(RuleEngine()).plan([old, better], constraints, current=[old])
    assert result.recipes == [old]
    assert any("不表示" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "extra,fields",
    [
        (("花生",), {"allergies": ["花生"]}),
        (("辣椒",), {"no_spicy": True}),
        (("鸡胸肉",), {"excluded_ingredients": ["鸡肉"]}),
        (("盐",), {"inventory": ["猪肉", "鸡胸肉"]}),
    ],
)
def test_health_improvement_never_waives_hard_rules(
    extra: tuple[str, ...], fields: dict[str, object]
) -> None:
    old = dish("蒸猪肉", ("猪肉",))
    better = dish("蒸鸡胸肉", ("鸡胸肉", *extra))
    constraints = Constraints.model_validate({"dish_count": 1, "health_goals": ["增肌"], **fields})
    result = MenuPlanner(RuleEngine()).plan([old, better], constraints, current=[old])
    assert result.recipes == [old]


def test_health_improvement_cannot_worsen_a_known_dinner_tag() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    breakfast = dish("早餐鸡胸肉", ("鸡胸肉",), meal="早餐")
    result = MenuPlanner(RuleEngine()).plan(
        [old, breakfast], Constraints(dish_count=1, health_goals=["增肌"]), current=[old]
    )
    assert result.recipes == [old]


def test_local_edit_does_not_change_a_different_health_improvable_slot() -> None:
    old = [dish("蒸猪肉", ("猪肉",)), dish("蒸白菜", ("白菜",), "vegetable")]
    better = dish("蒸鸡肉", ("鸡胸肉",))
    other_vegetable = dish("蒸菠菜", ("菠菜",), "vegetable")
    result = MenuPlanner(RuleEngine()).plan(
        [*old, better, other_vegetable],
        Constraints(dish_count=2, health_goals=["增肌"]),
        current=old,
        replace_slot=2,
    )
    assert result.recipes == [old[0], other_vegetable]
    assert [change["slot"] for change in result.changes] == [2]


def test_health_update_preserves_total_soups_and_explicit_vegetarian_quota() -> None:
    egg = dish("甜蒸蛋", ("鸡蛋", "白糖"))
    tofu = dish("蒸豆腐", ("豆腐",))
    meat = dish("鸡肉糙米", ("鸡肉", "糙米"))
    soup = dish("冬瓜汤", ("冬瓜", "清水"), "soup", steps="加水煮熟。")
    constraints = Constraints(
        dish_count=2,
        soup_count=1,
        meat_dish_count=0,
        vegetarian_dish_count=1,
        health_goals=["控糖"],
    )
    result = MenuPlanner(RuleEngine()).plan(
        [egg, meat, soup, tofu], constraints, current=[egg, soup]
    )
    assert result.failure is None
    assert result.recipes == [tofu, soup]


@pytest.mark.parametrize("goals", [[], ["未知目标"], ["增肌", "增肌"]])
def test_absent_unknown_or_duplicate_goals_never_create_retry_churn(goals: list[str]) -> None:
    old = dish("蒸鸡肉", ("鸡胸肉",))
    tied = dish("另一份鸡肉", ("鸡胸肉",))
    result = MenuPlanner(RuleEngine()).plan(
        [tied, old], Constraints(dish_count=1, health_goals=goals), current=[old]
    )
    assert result.recipes == [old] and result.changes == []


def test_improved_menu_is_a_fixed_point_even_when_candidate_order_changes() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    better = dish("蒸鸡胸肉", ("鸡胸肉",))
    tied = dish("蒸鸡肉", ("鸡胸肉",))
    constraints = Constraints(dish_count=1, health_goals=["增肌"])
    planner = MenuPlanner(RuleEngine())
    first = planner.plan([old, better, tied], constraints, current=[old])
    second = planner.plan([tied, old, better], constraints, current=first.recipes)
    assert first.recipes == second.recipes == [better]
    assert second.changes == []


def test_added_light_preparation_preference_rechecks_source_steps() -> None:
    old = dish("油炸猪肉", ("猪肉",), steps="将猪肉放入油锅炸熟。")
    steamed = dish("蒸猪肉", ("猪肉",))
    result = MenuPlanner(RuleEngine()).plan(
        [old, steamed], Constraints(dish_count=1, preferences=["清淡"]), current=[old]
    )
    assert result.recipes == [steamed]


@pytest.mark.parametrize(
    "steps,expected",
    [
        ("不加盐，蒸熟。", "preference_match"),
        ("不加盐，蒸熟后加入盐。", "caution"),
        ("蒸熟后可选加入盐。", "caution"),
    ],
)
def test_immediate_omissions_do_not_hide_later_or_optional_additions(
    steps: str, expected: str
) -> None:
    result = analyze_recipe(
        dish("蒸白菜", ("白菜",), "vegetable", steps=steps), Constraints(health_goals=["降压"])
    )
    assert result.goal_matches[0].status == expected


def test_explicit_non_frying_statement_does_not_create_frying_caution() -> None:
    result = analyze_recipe(
        dish("蒸鸡肉", ("鸡肉",), steps="不油炸，改用蒸熟。"), Constraints(health_goals=["减脂"])
    )
    assert result.goal_matches[0].status == "preference_match"
    assert result.goal_matches[0].methods == ["蒸"]


def test_raw_only_salt_is_not_lost_by_incomplete_parsed_ingredient_cache() -> None:
    sample = dish("蒸白菜", ("白菜",), "vegetable")
    sample.raw_ingredients += "；食盐适量"
    match = analyze_recipe(sample, Constraints(health_goals=["降压"])).goal_matches[0]
    assert match.status == "caution"
    assert any("盐" in reason and "原始配料" in reason for reason in match.reasons)


def test_improving_health_cannot_trade_away_a_primary_role_or_soup_position() -> None:
    old = dish("猪肉汤", ("猪肉", "水"), "soup")
    chicken = dish("蒸鸡胸肉", ("鸡胸肉",))
    result = MenuPlanner(RuleEngine()).plan(
        [old, chicken],
        Constraints(dish_count=1, soup_count=1, health_goals=["增肌"]),
        current=[old],
    )
    assert result.recipes == [old]


def test_direct_repair_rejects_bad_scope_and_handles_empty_input() -> None:
    old = dish("蒸猪肉", ("猪肉",))
    assert (
        repair_health_preferences(
            [], [], Constraints(dish_count=1), scores={}, order={}, food_matches=lambda r, t: False
        ).recipes
        == []
    )
    with pytest.raises(ValueError, match="existing"):
        repair_health_preferences(
            [old],
            [],
            Constraints(dish_count=1),
            scores={old.recipe_id: (2,)},
            order={},
            food_matches=lambda r, t: False,
            replace_slot=2,
        )


class GoalLLM(BaseLLM):
    """Script deterministic intents; never call a paid model in an HTTP test."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "只换第二道" in message:
            return Intent(action="replace", replace_slot=2)
        if "解释" in message:
            return Intent(action="explain")
        if "爸爸" in message:
            return Intent(diner_updates=[DinerUpdate(diner="爸爸", health_goals=["降压", "护心"])])
        if "增肌" in message:
            return Intent(health_goals=["增肌"])
        if "护心" in message:
            return Intent(health_goals=["护心", "降压"])
        return Intent()

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def prepared_app(
    tmp_path: Path,
    records: list[Recipe],
    *,
    seed: bool = False,
    father: bool = False,
    seed_menu_size: int = 1,
    seed_goals: tuple[str, ...] = (),
) -> Any:
    """Construct a synthetic user and an already-confirmed one-dish meal."""
    settings = Settings.model_construct(session_db=tmp_path / "goals.db")
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    store = SessionStore(settings.database_path)
    if seed:
        meal = Constraints(dish_count=seed_menu_size, no_spicy=True, people=2 if father else 1)
        diners = [profile_diner(profile)]
        diners[0].health_goals = list(seed_goals)
        if father:
            diners.append(Diner(diner_id="father", display_name="爸爸", aliases=["爸爸"]))
        store.save(
            SessionState(
                session_id=SESSION_ID,
                user_id=900001,
                menu_ids=[record.recipe_id for record in records[:seed_menu_size]],
                constraints=aggregate_constraints(meal, diners),
                meal_constraints=meal,
                diners=diners,
                confirmed_fields=["people", "meal_type", "restrictions"],
                menu_structure_explicit=True,
                menu_valid=True,
            ),
            None,
        )
    catalog = DataCatalog({900001: profile}, {r.recipe_id: r for r in records}, {})
    return create_app(settings, GoalLLM(), catalog, store)


def post_goal(client: TestClient, message: str) -> dict[str, Any]:
    response = client.post(
        "/chat", json={"user_id": 900001, "session_id": SESSION_ID, "message": message}
    )
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    assert result["status"] == "ok"
    return result


def test_http_goal_update_changes_old_slot_and_survives_restart_and_explanation(
    tmp_path: Path,
) -> None:
    old = dish("蒸猪肉", ("猪肉",))
    better = dish("蒸鸡胸肉", ("鸡胸肉",))
    with TestClient(prepared_app(tmp_path, [old, better], seed=True)) as client:
        initial = post_goal(client, "解释当前菜单")
        updated = post_goal(client, "这餐新增增肌要求")
    with TestClient(prepared_app(tmp_path, [old, better])) as client:
        retried = post_goal(client, "继续")
        explained = post_goal(client, "解释刚才菜单")
    assert initial["menu"][0]["recipe_id"] == old.recipe_id
    assert updated["menu"][0]["recipe_id"] == better.recipe_id
    assert updated["menu"] == retried["menu"] == explained["menu"]
    assert "营养达标评分" in updated["reason"]
    assert "未计算蛋白质" in explained["reason"]
    assert "不表示健康目标已经达标" in retried["reason"]
    assert "menu_modify" not in {event["name"] for event in explained["tool_calls"]}


def test_http_local_change_then_plain_continue_preserves_scope_after_restart(
    tmp_path: Path,
) -> None:
    old = dish("蒸猪肉", ("猪肉",))
    vegetable = dish("蒸白菜", ("白菜",), "vegetable")
    chicken = dish("蒸鸡胸肉", ("鸡胸肉",))
    spinach = dish("蒸菠菜", ("菠菜",), "vegetable")
    records = [old, vegetable, chicken, spinach]
    with TestClient(
        prepared_app(tmp_path, records, seed=True, seed_menu_size=2, seed_goals=("增肌",))
    ) as client:
        replaced = post_goal(client, "只换第二道，其余不变")
    with TestClient(prepared_app(tmp_path, records)) as client:
        continued = post_goal(client, "继续")
        explained = post_goal(client, "解释菜单")
    assert [r["recipe_id"] for r in replaced["menu"]] == [old.recipe_id, spinach.recipe_id]
    assert continued["menu"] == explained["menu"] == replaced["menu"]
    with TestClient(prepared_app(tmp_path, records)) as client:
        updated = post_goal(client, "这一餐再新增增肌要求")
    assert updated["menu"][0]["recipe_id"] == chicken.recipe_id


def test_attributed_fathers_goals_remain_with_father_but_recheck_shared_menu(
    tmp_path: Path,
) -> None:
    old = dish("黄油盐蒸白菜", ("白菜", "黄油", "盐"), "vegetable")
    better = dish("蒸白菜", ("白菜",), "vegetable")
    with TestClient(prepared_app(tmp_path, [old, better], seed=True, father=True)) as client:
        result = post_goal(client, "爸爸需要降压护心，这餐一起吃")
    diners = result["conversation_state"]["diners"]
    owner = next(d for d in diners if d["profile_owner"])
    father = next(d for d in diners if d["display_name"] == "爸爸")
    assert owner["health_goals"] == []
    assert father["health_goals"] == ["降压", "护心"]
    assert result["menu"][0]["recipe_id"] == better.recipe_id
    assert "不能判定低钠" in result["reason"]


def test_sse_and_minimal_explanation_cannot_hide_remaining_salt_caution(tmp_path: Path) -> None:
    import json

    salted = dish("蒸白菜", ("白菜", "盐"), "vegetable")
    with TestClient(prepared_app(tmp_path, [salted], seed=True)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "session_id": SESSION_ID,
                "messages": [{"role": "user", "content": "这餐新增护心要求"}],
            },
        )
    assert response.status_code == 200
    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "")
        for event in events
        if event != "[DONE]"
    )
    assert "需关注" in text and "盐" in text
    assert "不能判定低钠" in text
