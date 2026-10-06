"""Source-role and authored menu regressions, not nutrition gold labels."""

import pytest

from app.agent.meal_structure import role_counts
from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.dish_roles import primary_dish_role
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


@pytest.mark.parametrize("name", ["蒸肠粉", "猪肉肠粉", "鲜虾肠粉", "鸡蛋肠粉", "肠粉卷"])
@pytest.mark.parametrize("grain", ["肠粉专用粉", "粘米粉"])
def test_named_rice_roll_with_declared_grain_is_not_a_second_protein_entree(
    name: str, grain: str
) -> None:
    role = primary_dish_role(
        name, ["猪肉", "鸡蛋", "虾", grain], "粉和水拌成粉浆，蒸熟后卷起，装盘。", []
    )
    assert role == "staple"


@pytest.mark.parametrize("grain", ["肠粉专用粉酱汁", "肠粉香精", "肠粉专用粉替代品", "淀粉"])
def test_rice_roll_title_cannot_invent_a_missing_grain_declaration(grain: str) -> None:
    assert primary_dish_role("猪肉肠粉", ["猪肉", grain], "猪肉蒸熟装盘。", []) is None


def test_rice_roll_accompaniment_cannot_relabel_the_named_meat_main() -> None:
    assert (
        primary_dish_role("蒸鸡肉配肠粉", ["鸡肉", "肠粉专用粉"], "鸡肉蒸熟，肠粉另做后配食。", [])
        == "protein"
    )


def test_source_rice_roll_identity_changes_without_erasing_its_meat_or_safety() -> None:
    recipe = catalog()[604]
    assert recipe.name == "蒸肠粉" and recipe.categories == ["staple"]
    assert "猪里脊肉馅" in [ingredient.name for ingredient in recipe.ingredients]
    rules = RuleEngine()
    assert not rules.evaluate(recipe, Constraints(allergies=["猪肉"])).allowed
    assert not rules.evaluate(recipe, Constraints(diet_mode="vegan")).allowed


def test_shared_meal_adds_actual_second_entree_not_a_filled_rice_roll() -> None:
    records = [
        dish("猪肉肠粉", "肠粉专用粉60克；猪肉100克", "粉浆蒸熟卷起装盘。"),
        dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。"),
        dish("蒸鱼", "鱼肉200克", "鱼肉蒸熟装盘。"),
        dish("炒白菜", "白菜200克", "白菜炒熟装盘。"),
        dish("蒸菠菜", "菠菜200克", "菠菜蒸熟装盘。"),
        dish("白菜汤", "白菜200克；水500克", "白菜加水煮汤后盛出。"),
    ]
    constraints = Constraints(people=5, dish_count=6, soup_count=1, no_spicy=True)
    result = MenuPlanner(RuleEngine()).plan(records, constraints)
    assert result.failure is None
    assert role_counts(result.recipes) == {"vegetable": 2, "protein": 2, "staple": 1}
    assert {r.name for r in result.recipes if r.categories == ["protein"]} == {"蒸鸡肉", "蒸鱼"}


def test_same_role_replacement_suggestion_does_not_replace_meat_with_rice_roll() -> None:
    meat = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    other = dish("蒸鱼", "鱼肉200克", "鱼肉蒸熟装盘。")
    roll = dish("猪肉肠粉", "猪肉100克；肠粉专用粉60克", "粉浆蒸熟卷起装盘。")
    constraints = Constraints(dish_count=1)
    suggestions = replacement_candidates(
        [meat], [meat, other, roll], "rice-roll-public", constraints=constraints, rules=RuleEngine()
    )
    assert roll.recipe_id not in [r.recipe_id for r in suggestions]
