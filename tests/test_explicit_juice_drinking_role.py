"""Exposed affirmative drinking-source regressions, not a whole-library oracle."""

from pathlib import Path

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe, non_meal_roles
from app.domain.models import Constraints, Intent, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.sessions import SessionStore
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_component_slots import dish

DRINKING_SOURCES = (
    ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁后饮用。"),
    ("芹菜汁", "芹菜200克；水100克", "芹菜打浆，倒入杯中直接饮用。"),
    ("生菜汁", "生菜200克；水100克", "生菜榨汁。取汁后饮用。"),
    ("西兰花汁", "西兰花200克；水100克", "西兰花搅打成汁，过滤后喝。"),
    ("羽衣甘蓝汁", "羽衣甘蓝200克；水100克", "羽衣甘蓝榨汁后饮用即可。"),
    ("红菜头汁", "红菜头200克；水100克", "红菜头打浆，直接喝！"),
)


@pytest.mark.parametrize("name,foods,steps", DRINKING_SOURCES)
def test_affirmative_finished_drinking_source_is_not_a_vegetable_dish(name, foods, steps):
    recipe = dish(name, foods, steps)
    assert recipe.categories == ["drink"]
    assert not is_main_meal_recipe(recipe)
    stale = recipe.model_copy(update={"categories": ["vegetable"], "eligible": True})
    assert not is_main_meal_recipe(stale)


@pytest.mark.parametrize(
    "name,foods,steps",
    (
        ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁后不要饮用，只作为后续烹饪的原料。"),
        ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁后不可直接饮用。"),
        ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁后禁止饮用。"),
        ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁后不得饮用。"),
        ("菠菜汁", "菠菜200克；水100克", "菠菜榨汁，但并非用于饮用。"),
        ("菠菜汁", "菠菜200克；鸡肉100克", "菠菜榨汁，加入鸡肉炖熟后装盘。"),
        ("菠菜酱汁", "菠菜200克；盐1克", "菠菜打浆，加入盐调成酱汁，供后续烹饪。"),
        ("菠菜汁饭", "菠菜200克；大米200克；水100克", "菠菜榨汁，加入大米煮熟后食用。"),
        ("菠菜鸡汤", "菠菜200克；鸡肉100克；盐1克；水500克", "鸡肉煮汤后加入菠菜，连汤食用。"),
        ("菠菜汁", "菠菜200克；水100克", "菠菜洗净，放入设备按提示开始烹饪，完成后装盘。"),
    ),
)
def test_juice_substrings_or_negated_drinking_do_not_establish_a_beverage(name, foods, steps):
    recipe = dish(name, foods, steps)
    assert "drink" not in non_meal_roles(name, [i.name for i in recipe.ingredients], steps, [])


@pytest.mark.parametrize("experiment", [False, True])
def test_planning_and_suggestions_exclude_even_stale_drinking_roles(experiment):
    juice = dish(*DRINKING_SOURCES[0]).model_copy(update={"categories": ["vegetable"]})
    safe = dish("蒸菠菜", "菠菜200克；盐20克", "菠菜加盐蒸熟后装盘。")
    c = Constraints(dish_count=1, no_spicy=True, health_goals=["降压", "护心"])
    rules = RuleEngine()
    result = MenuPlanner(rules).plan([juice, safe], c, experiment_initial_goal_frontier=experiment)
    assert result.failure is None and result.recipes == [safe]
    assert (
        replacement_candidates([safe], [juice, safe], "drinking-role", constraints=c, rules=rules)
        == []
    )


def public_catalog(has_entree):
    juice = dish(*DRINKING_SOURCES[0]).model_copy(update={"categories": ["vegetable"]})
    safe = dish("蒸菠菜", "菠菜200克；盐20克", "菠菜加盐蒸熟后装盘。")
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    return (
        DataCatalog(
            {3: profile}, {r.recipe_id: r for r in ([juice, safe] if has_entree else [juice])}, {}
        ),
        juice,
    )


@pytest.mark.parametrize("has_entree", [False, True])
def test_actual_chat_does_not_count_drinking_source_as_dinner(tmp_path: Path, has_entree):
    catalog, juice = public_catalog(has_entree)
    llm = ScriptedLLM([complete_intent(dish_count=1, health_goals=["降压", "护心"])])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，安排1道菜，无其他忌口。"}
        )
    result = response.json()
    assert response.status_code == 200
    assert result["status"] == ("ok" if has_entree else "no_feasible_menu")
    assert juice.recipe_id not in {r["recipe_id"] for r in result["menu"]}
    assert result["replacement_suggestions"] == []


def test_explain_rejects_a_persisted_legacy_drinking_menu(tmp_path: Path):
    catalog, juice = public_catalog(True)
    llm = ScriptedLLM([complete_intent(dish_count=1), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，1道菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        store = SessionStore(tmp_path / "state.db")
        state = store.get(sid, 3)
        assert state is not None
        state.menu_ids = [juice.recipe_id]
        store.save(state, state.revision)
        explained = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "解释旧菜单"}
        ).json()
    assert explained["status"] == "clarification_required"
    assert explained["menu"] == [] and explained["conversation_state"]["menu_valid"] is False
