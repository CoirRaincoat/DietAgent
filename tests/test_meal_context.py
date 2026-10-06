"""Authored ordinary-meal/infant-source contrasts; no clinical or serving labels."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.meal_context import repair_meal_context
from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.meal_context import infant_only_source, meal_fit, source_meals
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def dish(name: str, foods: str, labels: str, steps: str = "蒸熟后装盘即可食用。") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": labels,
                    }
                ]
            ).values()
        )
    )


def fixture_pool() -> list[Recipe]:
    return [
        dish("早餐鸡蛋", "鸡蛋100克；水200克", "早餐、下午茶"),
        dish(
            "宝宝三文鱼",
            "三文鱼200克；水500克",
            "午餐、晚餐",
            "蒸熟后取出盘子，待温热后给宝宝食用。",
        ),
        dish("晚餐鸡肉", "鸡肉200克；盐1克", "午餐、晚餐"),
        dish("煮鱼肉", "鱼肉200克；盐1克", "午餐、晚餐"),
        dish("蒸豆腐", "豆腐200克；盐1克", "午餐、晚餐"),
        dish("炒白菜", "白菜200克；食用油3克；盐1克", "午餐、晚餐", "炒熟后装盘。"),
        dish("炒菠菜", "菠菜200克；食用油3克；盐1克", "午餐、晚餐", "炒熟后装盘。"),
        dish("早餐粥", "粳米150克；水500克", "早餐"),
        dish("米饭", "大米200克；水400克", "午餐、晚餐"),
        dish("冬瓜汤", "冬瓜200克；水500克；盐1克", "午餐、晚餐"),
    ]


def test_source_targeted_baby_recipe_does_not_enter_shared_meal() -> None:
    recipes = fixture_pool()
    assert not RuleEngine().evaluate(recipes[1], Constraints(people=5)).allowed
    plan = MenuPlanner(RuleEngine()).plan(
        recipes, Constraints(dish_count=1, people=5), query_terms=["三文鱼"]
    )
    assert plan.failure is None
    assert "宝宝三文鱼" not in [r.name for r in plan.recipes]


def test_existing_breakfast_only_protein_is_repaired_for_dinner() -> None:
    recipes = fixture_pool()
    old = [recipes[0], recipes[5], recipes[8]]
    plan = MenuPlanner(RuleEngine()).plan(recipes, Constraints(dish_count=3), current=old)
    assert plan.failure is None
    assert plan.recipes[0].name != "早餐鸡蛋"
    assert [r.recipe_id for r in plan.recipes[1:]] == [r.recipe_id for r in old[1:]]


def test_new_meal_prefers_same_role_positive_labels_before_breakfast_query_hit() -> None:
    recipes = fixture_pool()
    plan = MenuPlanner(RuleEngine()).plan(
        [recipes[0], recipes[2]], Constraints(dish_count=1), query_terms=["早餐鸡蛋"]
    )
    assert plan.failure is None
    assert "早餐鸡蛋" not in [r.name for r in plan.recipes]


def test_incompatible_fallback_has_explicit_unverified_meal_warning() -> None:
    recipes = [fixture_pool()[0]]
    plan = MenuPlanner(RuleEngine()).plan(recipes, Constraints(dish_count=1))
    assert plan.failure is None
    assert plan.recipes[0].name == "早餐鸡蛋"
    assert any("餐次" in warning and "早餐鸡蛋" in warning for warning in plan.warnings)


def test_local_edit_does_not_repair_other_legal_meal_mismatch_slots() -> None:
    recipes = fixture_pool()
    old = [recipes[0], recipes[5], recipes[8]]
    plan = MenuPlanner(RuleEngine()).plan(
        recipes, Constraints(dish_count=3), current=old, replace_slot=2
    )
    assert plan.failure is None
    assert plan.recipes[0].recipe_id == old[0].recipe_id
    assert plan.recipes[2].recipe_id == old[2].recipe_id
    assert any("早餐鸡蛋" in warning for warning in plan.warnings)


def test_replacement_suggestions_do_not_reintroduce_infant_only_source() -> None:
    recipes = fixture_pool()
    suggestions = replacement_candidates(
        [recipes[2]], [recipes[1]], "same-session", constraints=Constraints(dish_count=1)
    )
    assert suggestions == []


class ContextLLM(BaseLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "解释" in message:
            return Intent(action="explain")
        if "第二" in message:
            return Intent(action="replace", replace_slot=2)
        if state.menu_ids or state.pending_plan:
            return Intent(action="plan", meal_type="晚餐" if "改晚餐" in message else None)
        return Intent(
            action="plan",
            people=1,
            meal_type="早餐" if "早餐" in message else "晚餐",
            dish_count=3,
            restrictions_confirmed=True,
            no_spicy=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def app_for(tmp_path: Path, recipes: list[Recipe] | None = None) -> Any:
    settings = Settings.model_construct(session_db=tmp_path / "context.db")
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    chosen = fixture_pool() if recipes is None else recipes
    return create_app(
        settings,
        ContextLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in chosen}, {}),
        SessionStore(settings.database_path),
    )


def ask(client: TestClient, message: str, sid: str | None = None) -> dict[str, Any]:
    body = {"user_id": 900001, "message": message}
    if sid:
        body["session_id"] = sid
    response = client.post("/chat", json=body)
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    return result


def test_http_meal_change_rechecks_old_menu_and_restart_is_stable(tmp_path: Path) -> None:
    recipes = fixture_pool()
    store = SessionStore(tmp_path / "context.db")
    state = SessionState(
        session_id="4b26ce5a9eb24bdda340981aacba64db",
        user_id=900001,
        menu_ids=[recipes[i].recipe_id for i in (0, 5, 7)],
        constraints=Constraints(people=1, meal_type="早餐", dish_count=3),
        meal_constraints=Constraints(people=1, meal_type="早餐", dish_count=3),
        confirmed_fields=["people", "meal_type", "restrictions"],
        menu_structure_explicit=True,
        menu_valid=True,
    )
    store.save(state, None)
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "解释这份早餐", state.session_id)
        sid = first["conversation_state"]["session_id"]
        second = ask(client, "改晚餐，其他要求不变", sid)
    with TestClient(app_for(tmp_path)) as client:
        retry = ask(client, "继续", sid)
    assert first["status"] == second["status"] == retry["status"] == "ok"
    assert "早餐鸡蛋" in [r["name"] for r in first["menu"]]
    assert "早餐鸡蛋" not in [r["name"] for r in second["menu"]]
    assert [r["recipe_id"] for r in second["menu"]] == [r["recipe_id"] for r in retry["menu"]]


def test_sse_fallback_cannot_hide_meal_mismatch_from_minimal_explanation(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path, fixture_pool()[:1])) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [{"role": "user", "content": "一人晚餐，1道菜，没有其他忌口"}],
            },
        )
    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    text = "".join(
        json.loads(e)["choices"][0]["delta"].get("content", "") for e in events if e != "[DONE]"
    )
    assert "餐次标签未匹配" in text and "早餐鸡蛋" in text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("宝宝辅食", True),
        ("婴儿专用", True),
        ("婴幼儿辅食", True),
        ("给宝宝食用", True),
        ("适合小宝宝吃", True),
        ("宝宝白菜", False),
        ("不适合宝宝食用", False),
        ("不建议给宝宝吃", False),
        ("宝宝辅食，成人也可食用", False),
        ("宝宝辅食，全家都可食用", False),
        ("普通家庭白菜", False),
    ],
)
def test_audience_evidence_not_name_or_negated_suitability(text: str, expected: bool) -> None:
    record = dish("蒸白菜", "白菜200克；水500克", "晚餐", text)
    assert infant_only_source(record) is expected


@pytest.mark.parametrize(
    "raw,cache,expected",
    [
        ("早餐、晚餐", ["午餐"], "matched"),
        ("早餐、下午茶", ["晚餐"], "other_meal"),
        ("可以作为早餐或晚餐", ["晚餐"], "unknown"),
        ('["晚餐"]', ["晚餐"], "unknown"),
        ("", ["晚餐"], "matched"),
        ("", [], "unknown"),
    ],
)
def test_exact_source_meal_tags_override_stale_cache(
    raw: str, cache: list[str], expected: str
) -> None:
    record = fixture_pool()[0].model_copy(update={"raw_label": raw, "meal_types": cache})
    assert meal_fit(record, "晚餐") == expected
    assert source_meals(record) <= {"早餐", "午餐", "晚餐", "下午茶", "夜宵"}


def test_unknown_metadata_is_not_rejected_or_claimed_verified() -> None:
    record = fixture_pool()[2].model_copy(update={"raw_label": "", "meal_types": []})
    plan = MenuPlanner(RuleEngine()).plan([record], Constraints(dish_count=1))
    assert plan.recipes == [record] and plan.failure is None
    assert any("尚未核验" in warning for warning in plan.warnings)


def test_context_repair_protects_covered_food_and_explicit_quota() -> None:
    records = fixture_pool()
    constraints = Constraints(dish_count=1, preferred_ingredients=["鸡蛋"])
    plan = MenuPlanner(RuleEngine()).plan(records, constraints, current=[records[0]])
    assert plan.recipes == [records[0]]
    assert any("餐次标签未匹配" in w for w in plan.warnings)
    constraints = Constraints(dish_count=1, meat_dish_count=0, vegetarian_dish_count=1)
    result = repair_meal_context(
        [records[0]],
        [records[2]],
        constraints,
        scores={},
        order={},
        food_matches=lambda r, t: False,
    )
    assert result.recipes == [records[0]]


def test_context_repair_never_trades_primary_role_for_meal_tags() -> None:
    records = fixture_pool()
    result = repair_meal_context(
        [records[0]],
        [records[5]],
        Constraints(dish_count=1),
        scores={},
        order={},
        food_matches=lambda r, t: False,
    )
    assert result.recipes == [records[0]] and not result.changed_indices


def test_breakfast_and_afternoon_request_accept_matching_source() -> None:
    records = fixture_pool()
    for meal in ("早餐", "下午茶"):
        plan = MenuPlanner(RuleEngine()).plan(
            [records[0], records[2]], Constraints(dish_count=1, meal_type=meal)
        )
        assert plan.recipes == [records[0]]
        assert not any("餐次" in w for w in plan.warnings)


def test_context_priority_cannot_waive_no_spicy_or_allergy() -> None:
    record = dish("晚餐辣虾", "虾200克；辣椒10克", "晚餐")
    plan = MenuPlanner(RuleEngine()).plan([record], Constraints(dish_count=1, no_spicy=True))
    assert plan.failure and not plan.recipes
    plan = MenuPlanner(RuleEngine()).plan([record], Constraints(dish_count=1, allergies=["虾"]))
    assert plan.failure and not plan.recipes


def test_suggestions_do_not_worsen_matched_context() -> None:
    records = fixture_pool()
    assert (
        replacement_candidates(
            [records[2]], [records[0]], "same", constraints=Constraints(dish_count=1)
        )
        == []
    )
    matching = replacement_candidates(
        [records[2]], [records[0], records[3]], "same", constraints=Constraints(dish_count=1)
    )
    assert matching == [records[3]]
