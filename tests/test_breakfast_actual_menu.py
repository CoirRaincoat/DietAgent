"""Actual two-dish breakfast plus hard constraints and soft edit boundaries."""

from app.agent.meal_structure import minimum_role_counts
from app.agent.planner import MenuPlanner
from app.domain.meal_context import meal_fit
from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog
from tests.test_meal_structure import dish, pool


def breakfast(**updates: object) -> Constraints:
    values: dict[str, object] = {
        "people": 1,
        "meal_type": "早餐",
        "dish_count": 2,
        "soup_count": 0,
        "no_spicy": True,
        "preferences": ["清淡"],
    }
    values.update(updates)
    return Constraints.model_validate(values)


def ids(menu: list[Recipe]) -> list[str]:
    return [r.recipe_id for r in menu]


def test_two_dish_breakfast_defaults_fit_two_slots_with_a_staple() -> None:
    assert minimum_role_counts(2, breakfast()) == {
        "vegetable": 0,
        "protein": 1,
        "staple": 1,
    }


def test_two_dish_dinner_does_not_inherit_breakfast_template() -> None:
    assert minimum_role_counts(2, breakfast(meal_type="晚餐")) == {
        "vegetable": 1,
        "protein": 1,
        "staple": 0,
    }


def test_frozen_original_breakfast_has_actual_staple_and_source_context() -> None:
    records = list(catalog().values())
    original = {r.recipe_id: r.model_dump() for r in records}
    constraints = breakfast()
    result = MenuPlanner(RuleEngine()).plan(records, constraints)
    assert result.failure is None
    assert len(result.recipes) == 2
    assert any("staple" in r.categories for r in result.recipes)
    assert any("protein" in r.categories for r in result.recipes)
    assert all(meal_fit(r, "早餐") in {"matched", "suggested"} for r in result.recipes)
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)
    assert {r.recipe_id: r.model_dump() for r in records} == original


def test_explicit_two_meat_dishes_are_not_replaced_by_default_staple() -> None:
    constraints = breakfast(meat_dish_count=2)
    choices = [dish("蒸鸡肉", "鸡肉200克"), dish("蒸猪肉", "猪肉200克"), pool()[7]]
    result = MenuPlanner(RuleEngine()).plan(choices, constraints)
    assert result.failure is None
    assert len(result.recipes) == 2
    assert not any("staple" in r.categories for r in result.recipes)


def test_two_explicit_food_preferences_are_not_lost_to_default_staple() -> None:
    constraints = breakfast(preferred_ingredients=["鸡肉", "猪肉"])
    choices = [dish("蒸鸡肉", "鸡肉200克"), dish("蒸猪肉", "猪肉200克"), pool()[7]]
    rules = RuleEngine()
    result = MenuPlanner(rules).plan(choices, constraints)
    assert result.failure is None
    assert all(
        any(rules.food_matches(r, food) for r in result.recipes) for food in ("鸡肉", "猪肉")
    )


def test_readonly_recheck_does_not_repair_unrequested_breakfast_default() -> None:
    choices = pool()
    current = [choices[4], choices[5]]
    result = MenuPlanner(RuleEngine()).plan(
        choices, breakfast(), current=current, recheck_soft_preferences=False
    )
    assert result.failure is None
    assert ids(result.recipes) == ids(current)
    assert result.changes == []


def test_local_second_slot_never_edits_first_for_breakfast_template() -> None:
    choices = pool()
    current = [choices[4], choices[5]]
    result = MenuPlanner(RuleEngine()).plan(choices, breakfast(), current=current, replace_slot=2)
    assert result.failure is None
    assert result.recipes[0] == current[0]
    assert all(change["slot"] == 2 for change in result.changes)


def test_safe_vegan_breakfast_does_not_add_animal_protein_to_fill_role() -> None:
    constraints = breakfast(diet_mode="vegan")
    result = MenuPlanner(RuleEngine()).plan(pool(), constraints)
    assert result.failure is None
    assert len(result.recipes) == 2
    assert any("staple" in r.categories for r in result.recipes)
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)


def test_allergic_staple_is_not_selected_to_satisfy_breakfast_default() -> None:
    constraints = breakfast(allergies=["芝麻"])
    bad = dish("芝麻米饭", "大米200克；芝麻10克")
    choices = [bad, *pool()]
    result = MenuPlanner(RuleEngine()).plan(choices, constraints)
    assert result.failure is None
    assert len(result.recipes) == 2
    assert bad.recipe_id not in ids(result.recipes)
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)


def test_breakfast_explicit_soup_is_still_counted_in_two_total_dishes() -> None:
    constraints = breakfast(soup_count=1)
    result = MenuPlanner(RuleEngine()).plan(pool(), constraints)
    assert result.failure is None
    assert len(result.recipes) == 2
    assert sum("soup" in r.categories for r in result.recipes) == 1
