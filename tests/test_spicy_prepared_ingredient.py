"""Finite prepared-ingredient gap found in the original catalog, not title inference."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def source_recipe():
    return next(r for r in catalog().values() if r.name == "紫苏辣子鸡")


@pytest.mark.parametrize(
    "constraints", [Constraints(no_spicy=True), Constraints(preferences=["不辣"])]
)
def test_original_prepared_chicken_is_not_verified_nonspicy(constraints):
    recipe = source_recipe()
    assert [r.name for r in recipe.ingredients] == ["紫苏辣子鸡"]
    assert recipe.labels == []
    decision = RuleEngine().evaluate(recipe, constraints)
    assert not decision.allowed
    assert any("辣子鸡" in reason for reason in decision.reasons)


@pytest.mark.parametrize("name", ["辣子鸡", "紫苏辣子鸡", "冷冻辣子鸡", "成品辣子鸡"])
def test_prepared_ingredient_is_screened_without_a_spicy_source_label(name):
    recipe = dish("鸡块", f"主料：{name}400克", "食材加水煮熟后装盘。")
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_prepared_ingredient_in_source_steps_also_cannot_silently_pass():
    recipe = dish("鸡块", "鸡肉200克", "加入现成辣子鸡，煮熟后装盘。")
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_source_is_not_globally_deleted_without_nonspicy_requirement():
    recipe = source_recipe()
    assert recipe.eligible
    assert RuleEngine().evaluate(recipe, Constraints()).allowed


@pytest.mark.parametrize("foods", ["鸡肉200克", "辣木叶200克", "辣根20克；鸡肉200克"])
def test_finite_term_does_not_turn_any_lachi_character_into_chili(foods):
    recipe = dish("普通配菜", foods, "食材蒸熟装盘。")
    assert RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_title_is_not_substituted_for_actual_ingredient_evidence():
    recipe = dish("紫苏辣子鸡", "鸡肉200克；紫苏10克", "鸡肉与紫苏蒸熟装盘。")
    assert RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("preferences", [[], ["不辣"]])
def test_planner_rejects_the_single_source_instead_of_outputting_a_false_safe_menu(preferences):
    result = MenuPlanner(RuleEngine()).plan(
        [source_recipe()],
        Constraints(dish_count=1, no_spicy=not preferences, preferences=preferences),
    )
    assert result.failure and not result.recipes


def test_local_replacement_does_not_use_spicy_prepared_candidate_or_edit_other_slots():
    rice = dish("蒸米饭", "大米100克；水200克", "大米蒸熟食用。")
    chicken = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    egg = dish("煮鸡蛋", "鸡蛋2个", "鸡蛋煮熟食用。")
    planner = MenuPlanner(RuleEngine())
    result = planner.plan(
        [source_recipe(), egg],
        Constraints(dish_count=2, no_spicy=True),
        current=[rice, chicken],
        replace_slot=2,
    )
    assert result.failure is None
    assert result.recipes == [rice, egg]


def test_existing_spicy_source_in_an_unedited_slot_requires_wider_permission():
    rice = dish("蒸米饭", "大米100克；水200克", "大米蒸熟食用。")
    chicken = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    egg = dish("煮鸡蛋", "鸡蛋2个", "鸡蛋煮熟食用。")
    result = MenuPlanner(RuleEngine()).plan(
        [chicken, egg],
        Constraints(dish_count=2, no_spicy=True),
        current=[source_recipe(), rice],
        replace_slot=2,
    )
    assert result.failure and not result.recipes
    assert "其他菜位" in result.failure


@pytest.mark.parametrize("restriction", ["allergy", "excluded", "no_spicy"])
def test_local_scope_guard_applies_to_ingredient_hard_rules_not_only_flavor_labels(restriction):
    rice = dish("蒸米饭", "大米100克；水200克", "大米蒸熟食用。")
    unsafe = dish("蒸虾", "虾200克；辣椒10克", "食材蒸熟食用。")
    chicken = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    egg = dish("煮鸡蛋", "鸡蛋2个", "鸡蛋煮熟食用。")
    fields = {
        "allergy": {"allergies": ["虾"]},
        "excluded": {"excluded_ingredients": ["虾"]},
        "no_spicy": {"no_spicy": True},
    }[restriction]
    result = MenuPlanner(RuleEngine()).plan(
        [chicken, egg],
        Constraints(dish_count=2, **fields),
        current=[unsafe, rice],
        replace_slot=2,
    )
    assert result.failure and not result.recipes
    assert "其他菜位" in result.failure
