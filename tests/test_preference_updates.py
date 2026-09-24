"""Ingredient preferences improve a meal without silently broadening edits."""

import pytest
from test_agent_api import ScriptedLLM, complete_intent

from app.agent.planner import MenuPlanner
from app.agent.service import MealAgent
from app.domain.models import Constraints, Ingredient, Intent, Recipe
from app.infrastructure.sessions import SessionStore
from app.infrastructure.synthetic import load_synthetic_catalog
from app.rules.engine import RuleEngine


def dish(key, ingredients, category="protein", name=None):
    return Recipe(
        recipe_id=key, name=name or key, raw_ingredients="；".join(ingredients),
        ingredients=[Ingredient(raw=food, name=food) for food in ingredients],
        steps="将上述食材煮熟。", categories=[category], methods=["煮"],
        source_row=1, fingerprint=key,
    )


@pytest.fixture
def baseline():
    return [
        dish("greens", ["白菜"], "vegetable"),
        dish("eggs", ["鸡蛋"]),
        dish("rice", ["大米"], "staple"),
    ]


def ids(menu):
    return [recipe.recipe_id for recipe in menu]


def test_new_ingredient_preference_changes_only_one_relevant_slot(baseline):
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, beef], Constraints(preferred_ingredients=["牛肉"]), current=baseline,
    )
    assert result.failure is None
    assert ids(result.recipes) == ["greens", "beef", "rice"]
    assert [change["slot"] for change in result.changes] == [2]
    assert "食材偏好" in result.changes[0]["reason"]


def test_already_covered_preference_does_not_churn_valid_menu(baseline):
    baseline[1] = dish("beef", ["牛肉"])
    better = dish("beef-and-tofu", ["牛肉", "豆腐"])
    result = MenuPlanner(RuleEngine()).plan(
        [better, *baseline], Constraints(preferred_ingredients=["牛肉"]), current=baseline,
    )
    assert ids(result.recipes) == ids(baseline)
    assert result.changes == []


def test_combined_preferences_can_be_met_with_a_single_swap(baseline):
    fish = dish("fish", ["鲈鱼"])
    tofu = dish("tofu", ["豆腐"])
    both = dish("fish-tofu", ["鲈鱼", "豆腐"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, fish, tofu, both],
        Constraints(preferred_ingredients=["鱼", "豆腐"]), current=baseline,
    )
    assert "fish-tofu" in ids(result.recipes)
    assert len(result.changes) == 1


def test_preference_aliases_count_once(baseline):
    tomato = dish("tomato", ["番茄"], "vegetable")
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, tomato], Constraints(preferred_ingredients=["西红柿", "番茄"]),
        current=baseline,
    )
    assert ids(result.recipes) == ["tomato", "eggs", "rice"]
    assert not any("未覆盖" in warning for warning in result.warnings)


@pytest.mark.parametrize("term,allergies", [("鱼", ["海鲜"]), ("不存在的食材", [])])
def test_unavailable_or_unsafe_preference_warns_without_failing(baseline, term, allergies):
    fish = dish("fish", ["鲈鱼"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, fish], Constraints(preferred_ingredients=[term], allergies=allergies),
        current=baseline,
    )
    assert result.failure is None
    assert ids(result.recipes) == ids(baseline)
    assert result.changes == []
    assert any("未覆盖" in warning and term in warning for warning in result.warnings)


def test_required_hard_constraint_repair_also_covers_preference(baseline):
    baseline[1] = dish("shrimp", ["虾"])
    tofu = dish("tofu", ["豆腐"])
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, tofu, beef],
        Constraints(allergies=["海鲜"], preferred_ingredients=["牛肉"]), current=baseline,
    )
    assert ids(result.recipes) == ["greens", "beef", "rice"]
    assert len(result.changes) == 1


def test_new_slot_is_used_before_replacing_existing_dishes(baseline):
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, beef], Constraints(dish_count=4, preferred_ingredients=["牛肉"]),
        current=baseline,
    )
    assert ids(result.recipes) == [*ids(baseline), "beef"]
    assert [change["slot"] for change in result.changes] == [4]


def test_explicit_replacement_keeps_unrelated_slots_and_reports_missing_preference(baseline):
    soup = dish("soup", ["冬瓜"], "soup")
    replacement = dish("other-soup", ["萝卜"], "soup")
    beef = dish("beef", ["牛肉"])
    baseline[1] = soup
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, replacement, beef],
        Constraints(soup_count=1, preferred_ingredients=["牛肉"]), current=baseline,
        replace_slot=2,
    )
    assert ids(result.recipes) == ["greens", "other-soup", "rice"]
    assert [change["slot"] for change in result.changes] == [2]
    assert any("未覆盖" in warning and "牛肉" in warning for warning in result.warnings)


def test_preference_cannot_increase_soup_count_or_restore_rejected_recipe(baseline):
    soup = dish("beef-soup", ["牛肉"], "soup")
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, soup, beef], Constraints(preferred_ingredients=["牛肉"]),
        current=baseline, reject_ids={"beef"},
    )
    assert result.failure is None
    assert ids(result.recipes) == ids(baseline)
    assert any("未覆盖" in warning for warning in result.warnings)


def test_replacement_outside_reduced_menu_size_reports_conflict(baseline):
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [*baseline, beef], Constraints(dish_count=2, preferred_ingredients=["牛肉"]),
        current=baseline, replace_slot=3,
    )
    assert result.failure is not None
    assert "总菜数" in result.failure
    assert result.recipes == []


def test_covered_preference_is_not_lost_to_make_room_for_another():
    fish = dish("fish", ["鲈鱼"])
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [fish, beef], Constraints(dish_count=1, preferred_ingredients=["鱼", "牛肉"]),
        current=[fish],
    )
    assert ids(result.recipes) == ["fish"]
    assert any("未覆盖" in warning and "牛肉" in warning for warning in result.warnings)


def test_repeating_multiple_preferences_is_idempotent(baseline):
    fish = dish("fish", ["鲈鱼"])
    tomato = dish("tomato", ["番茄"], "vegetable")
    candidates = [*baseline, fish, tomato]
    constraints = Constraints(preferred_ingredients=["鱼", "番茄", "不存在的食材"])
    planner = MenuPlanner(RuleEngine())
    first = planner.plan(candidates, constraints, current=baseline)
    second = planner.plan(candidates, constraints, current=first.recipes)
    assert {"fish", "tomato"} <= set(ids(first.recipes))
    assert ids(second.recipes) == ids(first.recipes)
    assert second.changes == []


def test_soft_health_scores_alone_do_not_rearrange_confirmed_menu(baseline):
    beef = dish("beef", ["牛肉"])
    result = MenuPlanner(RuleEngine()).plan(
        [beef, *baseline], Constraints(health_goals=["增肌"]), current=baseline,
    )
    assert ids(result.recipes) == ids(baseline)
    assert result.changes == []


@pytest.mark.asyncio
async def test_actual_catalog_preference_is_applied_and_survives_explanation(tmp_path):
    catalog = load_synthetic_catalog()
    llm = ScriptedLLM([
        complete_intent(), Intent(preferred_ingredients=["牛肉"]), Intent(action="explain"),
    ])
    agent = MealAgent(catalog, SessionStore(tmp_path / "preferences.sqlite3"), llm)
    first = await agent.chat(900001, "1人晚餐，没有其他忌口，安排三道菜")
    sid = first.conversation_state.session_id
    second = await agent.chat(900001, "希望这餐有牛肉", session_id=sid)
    assert first.status == second.status == "ok"
    assert sum(a.recipe_id != b.recipe_id for a, b in zip(first.menu, second.menu)) == 1
    # The source may name an ingredient only in its cooking steps.
    assert any(
        "牛肉" in item.steps or any("牛肉" in ingredient for ingredient in item.ingredients)
        for item in second.menu
    )
    explained = await agent.chat(900001, "解释刚才菜单", session_id=sid)
    assert [item.recipe_id for item in explained.menu] == [item.recipe_id for item in second.menu]
    assert "menu_modify" not in {event.name for event in explained.tool_calls}
