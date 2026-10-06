"""Authored count-metadata contrasts, not universal title classification."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.domain.title_counts import culinary_title_without_zero_soup_metadata
from app.rules.engine import RuleEngine
from tests.test_component_slots import dish

SINGLE = (
    ("一人食1菜0汤", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。", "protein"),
    ("一人食一菜零汤", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。", "protein"),
    ("蒸鸡肉（1道菜0道汤）", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。", "protein"),
    ("蒸鸡肉【１菜０汤】", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。", "protein"),
    ("零汤·炒白菜", "白菜200克；盐1克", "白菜炒熟装盘。", "vegetable"),
    ("炒白菜（0汤）", "白菜200克；盐1克", "白菜炒熟装盘。", "vegetable"),
    ("鸡蛋炒饭（1菜0汤）", "鸡蛋50克；大米100克", "米饭煮熟和鸡蛋炒熟装盘。", "staple"),
    ("无馅汤圆（1菜0汤）", "糯米粉100克；水200克", "糯米粉揉面搓汤圆煮熟。", "staple"),
)


@pytest.mark.parametrize("name,foods,steps,role", SINGLE)
def test_zero_soup_count_metadata_does_not_create_a_soup_role(name, foods, steps, role) -> None:
    recipe = dish(name, foods, steps)
    assert recipe.categories == [role]
    assert is_main_meal_recipe(recipe)
    result = MenuPlanner(RuleEngine()).plan([recipe], Constraints(dish_count=1, soup_count=0))
    assert result.failure is None and result.recipes == [recipe]


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("番茄汤（0汤）", "番茄200克；水500克；盐1克", "番茄加水煮汤出锅。"),
        ("番茄汤（零汤）", "番茄200克；水500克；盐1克", "番茄加水煮汤出锅。"),
        ("白菜汤（1菜0汤）", "白菜200克；水500克；盐1克", "白菜加水煮汤出锅。"),
    ],
)
def test_actual_soup_name_is_not_erased_by_conflicting_zero_soup_note(name, foods, steps) -> None:
    recipe = dish(name, foods, steps)
    assert recipe.categories == ["soup"]
    assert MenuPlanner(RuleEngine()).plan([recipe], Constraints(dish_count=1, soup_count=0)).failure
    assert MenuPlanner(RuleEngine()).plan(
        [recipe], Constraints(dish_count=1, soup_count=1)
    ).recipes == [recipe]


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("牛肉酱（1菜0汤）", "牛肉200克；盐1克", "肉末炒熟熬煮成酱即可使用。"),
        ("番茄锅底【1菜0汤】", "番茄200克；水500克", "番茄煮熟作为锅底使用。"),
        ("绞肉（1菜0汤）", "猪肉200克", "猪肉切碎，烹饪结束取出即可使用。"),
        ("蛋白糖霜（1菜0汤）", "蛋清40克；糖粉170克", "蛋清糖粉搅打混匀取出即可使用。"),
    ],
)
def test_count_decoration_cannot_reintroduce_standalone_components(name, foods, steps) -> None:
    recipe = dish(name, foods, steps)
    assert recipe.categories == ["component"] and not is_main_meal_recipe(recipe)


@pytest.mark.parametrize(
    "name", ["二人食三菜一汤", "二人食2菜0汤", "二人食（２菜０汤）", "二人食【2道菜，0道汤】"]
)
def test_multiple_dishes_are_not_hidden_by_zero_soup_cleanup(name) -> None:
    recipe = dish(name, "鸡肉200克；白菜200克；水500克", "鸡肉和白菜分别蒸熟装盘。")
    assert recipe.categories == ["component"] and not is_main_meal_recipe(recipe)


@pytest.mark.parametrize(
    "name", ["10汤", "２０汤", "100汤", "1.0汤", "0汤匙盐", "零汤圆", "0汤底", "0汤汁", "〇汤料"]
)
def test_cleanup_does_not_erase_longer_counts_decimals_or_word_parts(name) -> None:
    assert culinary_title_without_zero_soup_metadata(name) == name


def test_title_view_does_not_change_original_name_or_source_identity() -> None:
    original = "蒸鸡肉（1菜0汤）"
    recipe = dish(original, "鸡肉200克；盐1克", "鸡肉蒸熟装盘。")
    assert recipe.name == original and recipe.recipe_id == "recipe_" + recipe.fingerprint[:24]
    assert recipe.raw_ingredients == "鸡肉200克；盐1克"
    assert recipe.steps == "鸡肉蒸熟装盘。" and recipe.raw_label == "午餐、晚餐"


def test_title_cleanup_cannot_waive_spicy_or_allergen_constraints() -> None:
    spicy = dish("辣炒鸡肉（1菜0汤）", "鸡肉200克；小米椒10克", "鸡肉炒熟装盘。")
    allergen = dish("蒸虾（1菜0汤）", "虾200克", "虾蒸熟装盘。")
    planner = MenuPlanner(RuleEngine())
    assert planner.plan([spicy], Constraints(dish_count=1, no_spicy=True)).failure
    assert planner.plan([allergen], Constraints(dish_count=1, allergies=["虾"])).failure
