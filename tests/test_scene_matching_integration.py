"""Finite source-scene menu acceptance; scripted transport is not model judging."""

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.agent.response_copy import required_fact_ids, response_facts
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints, Intent, SessionState, UserProfile
from app.infrastructure.data import normalize_recipes
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine
from tests.test_flavor_matching_integration import FlavorLLM, dish, make_app


@pytest.fixture(scope="module")
def original_scene_pair():
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        rows = {r.source_row: r for r in normalize_recipes(csv.DictReader(stream)).values()}
    assert rows[1613].name == "清蒸鲈鱼" and rows[5].name == "家常鲈鱼"
    assert "干辣椒段8克" in rows[5].raw_ingredients
    return rows[1613], rows[5]


def test_new_scene_changes_original_menu_not_only_metadata(original_scene_pair):
    old, home = original_scene_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, home], Constraints(dish_count=1, preferences=["家常"]), current=[old]
    )
    assert result.failure is None and result.recipes == [home]
    assert any("场景" in change["reason"] for change in result.changes)


def test_explicit_scene_reference_reaches_initial_ranking(original_scene_pair):
    old, home = original_scene_pair
    constraints = Constraints(dish_count=1, preferences=["家常"])
    rules = RuleEngine()
    assert recipe_relevance_score(home, [], constraints, rules) > recipe_relevance_score(
        old, [], constraints, rules
    )
    assert MenuPlanner(rules).plan([old, home], constraints).recipes == [home]


@pytest.mark.parametrize("restriction", [{"no_spicy": True}, {"allergies": ["鱼"]}])
def test_scene_title_cannot_waive_original_chili_or_allergy(original_scene_pair, restriction):
    old, home = original_scene_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, home],
        Constraints(dish_count=1, preferences=["家常"], **restriction),
        current=[old],
    )
    assert home not in result.recipes
    if not result.failure:
        assert result.recipes == [old]
        assert any("场景" in warning and "缺少" in warning for warning in result.warnings)


def test_empty_continue_does_not_reverse_accepted_edit_for_scene(original_scene_pair):
    old, home = original_scene_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, home],
        Constraints(dish_count=1, preferences=["家常"]),
        current=[old],
        recheck_soft_preferences=False,
    )
    assert result.recipes == [old] and result.changes == []
    assert any("场景" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "preferences,marker",
    [
        (["便当"], "缺少"),
        (["家常", "不要家常"], "冲突"),
        (["场景：野餐"], "野餐"),
        (["不要便当"], "负向"),
    ],
)
def test_missing_conflict_and_unknown_scene_are_mandatory_facts(preferences, marker):
    old = dish("蒸鸡肉", "鸡肉200克；盐1克")
    constraints = Constraints(dish_count=1, preferences=preferences)
    result = MenuPlanner(RuleEngine()).plan([old], constraints, current=[old])
    facts = response_facts(
        intent=Intent(),
        constraints=constraints,
        diners=[],
        previous=[old],
        chosen=result.recipes,
        balance=analyze_menu_balance(result.recipes),
    )
    assert "dining_scene" in required_fact_ids(Intent(), facts)
    assert marker in facts["dining_scene"]
    assert "当前菜单无需调整" not in facts["opening"]


def test_partial_scene_reference_does_not_certify_whole_menu():
    home = dish("家常炒白菜", "白菜200克；盐1克")
    plain = dish("炒菠菜", "菠菜200克；盐1克")
    facts = response_facts(
        intent=Intent(),
        constraints=Constraints(dish_count=2, preferences=["家常"]),
        diners=[],
        previous=[],
        chosen=[home, plain],
        balance=analyze_menu_balance([home, plain]),
    )
    assert "炒菠菜" in facts["dining_scene"] and "缺少" in facts["dining_scene"]
    assert "整餐" in facts["dining_scene"] and "不代表" in facts["dining_scene"]


def test_scene_repair_preserves_flavor_ingredient_and_explicit_vegetarian_slots():
    old = dish("蒜香白菜", "白菜200克；大蒜2克；盐1克")
    loses_flavor = dish("家常炒白菜", "白菜200克；盐1克")
    loses_food = dish("家常蒜香菠菜", "菠菜200克；大蒜2克；盐1克")
    adds_meat = dish("家常蒜香白菜", "白菜200克；猪肉50克；盐1克", "晚餐、蒜香")
    constraints = Constraints(
        dish_count=1,
        preferences=["蒜香", "家常"],
        preferred_ingredients=["白菜"],
        meat_dish_count=0,
        vegetarian_dish_count=1,
    )
    result = MenuPlanner(RuleEngine()).plan(
        [old, loses_flavor, loses_food, adds_meat], constraints, current=[old]
    )
    assert result.failure is None and result.recipes == [old]
    assert any("场景" in warning for warning in result.warnings)


def test_scene_reference_is_preserved_by_optional_suggestions():
    old = dish("家常炒白菜", "白菜200克；盐1克")
    home = dish("家常炒菠菜", "菠菜200克；盐1克")
    plain = dish("炒青菜", "青菜200克；盐1克")
    options = replacement_candidates(
        [old],
        [plain, home],
        "synthetic-scene",
        constraints=Constraints(dish_count=1, preferences=["家常"]),
        rules=RuleEngine(),
    )
    assert options == [home]


def test_actual_planner_scene_repair_keeps_unrelated_slot():
    first = dish("炒白菜", "白菜200克；盐1克")
    second = dish("炒菠菜", "菠菜200克；盐1克")
    home = dish("家常炒青菜", "青菜200克；盐1克")
    result = MenuPlanner(RuleEngine()).plan(
        [first, second, home],
        Constraints(dish_count=2, preferences=["家常"]),
        current=[first, second],
        replace_slot=2,
    )
    assert result.failure is None and result.recipes == [first, home]
    assert [change["slot"] for change in result.changes] == [2]
    assert any("缺少" in warning and first.name in warning for warning in result.warnings)


def test_new_flavor_cannot_remove_previous_per_slot_scene_reference():
    old = dish("家常炒白菜", "白菜200克；盐1克")
    sour = dish("糖醋白菜", "白菜200克；醋10克；白糖2克；盐1克")
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["家常", "酸甜"]),
        current=[old],
    )
    assert result.failure is None and result.recipes == [old]
    assert any("酸甜" in warning for warning in result.warnings)


class SceneLLM(FlavorLLM):
    """Intentionally omits the scene: source-text grounding must preserve it."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
            query_terms=["清蒸"],
        )


def test_http_new_scene_with_local_replace_keeps_other_menu_ids(tmp_path):
    first = dish("炒白菜", "白菜200克；盐1克")
    second = dish("炒菠菜", "菠菜200克；盐1克")
    home = dish("家常炒青菜", "青菜200克；盐1克")

    class LocalSceneLLM(SceneLLM):
        async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
            if state.menu_ids:
                return Intent(action="replace", replace_slot=2)
            return Intent(
                action="plan",
                people=2,
                meal_type="晚餐",
                dish_count=2,
                soup_count=0,
                restrictions_confirmed=True,
                query_terms=["白菜", "菠菜"],
            )

    with TestClient(make_app(tmp_path, (first, second, home), LocalSceneLLM())) as client:
        before = client.post(
            "/chat", json={"user_id": 900001, "message": "2人晚餐2道菜，无其他忌口。"}
        ).json()
        assert home.recipe_id not in [item["recipe_id"] for item in before["menu"]]
        after = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": before["conversation_state"]["session_id"],
                "message": "只换第2道，想要家常菜，其他不变。",
            },
        ).json()
    assert after["status"] == "ok"
    assert after["menu"][0]["recipe_id"] == before["menu"][0]["recipe_id"]
    assert after["menu"][1]["recipe_id"] == home.recipe_id
    # ChatResult exposes verified text/menu IDs, not planner-internal changes.
    assert "只将第 2 道" in after["reason"] and "其他菜保持不变" in after["reason"]


def test_http_explicit_scene_text_is_grounded_and_persisted_across_restart(
    tmp_path, original_scene_pair
):
    old, home = original_scene_pair
    with TestClient(make_app(tmp_path, original_scene_pair, SceneLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道清蒸鲈鱼，无忌口。"}
        ).json()
        assert first["menu"][0]["recipe_id"] == old.recipe_id
        sid = first["conversation_state"]["session_id"]
        changed = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "这餐想要家常风格，可以调整。",
            },
        ).json()
    assert changed["status"] == "ok" and changed["menu"][0]["recipe_id"] == home.recipe_id
    assert "家常" in changed["conversation_state"]["constraints"]["preferences"]
    assert "场景" in changed["reason"] and "不代表" in changed["reason"]
    with TestClient(make_app(tmp_path, original_scene_pair, SceneLLM())) as client:
        continued = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
        ).json()
    assert continued["menu"][0]["recipe_id"] == home.recipe_id


def test_sse_minimal_fact_selection_cannot_hide_unverified_scene(tmp_path, original_scene_pair):
    with TestClient(make_app(tmp_path, original_scene_pair, SceneLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道清蒸鲈鱼，无忌口。"}
        ).json()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "session_id": first["conversation_state"]["session_id"],
                "messages": [{"role": "user", "content": "用餐场景：便当"}],
            },
        )
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "") for event in events[:-1]
    )
    assert "便当" in text and "缺少" in text and "当前菜单无需调整" not in text
