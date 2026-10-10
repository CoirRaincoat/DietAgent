"""Synthetic regressions for food-name homonyms and genuine mixed dishes."""

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.nutrition.qualitative import analyze
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine


def recipe(*foods, name="食材角色反例"):
    return next(iter(normalize_recipes([{
        "名称": name,
        "食材清单": "；".join(food + "100克" for food in foods),
        "烹饪步骤": "处理食材后装盘。",
        "label": "晚餐",
    }]).values()))


@pytest.mark.parametrize("food", [
    "杏鲍菇", "杏鲍菇片", "鸡毛菜", "猪肚菇", "肉桂", "肉桂粉", "肉豆蔻", "肉蔻",
    "红枣肉", "和田大枣肉", "桂圆肉", "干桂圆肉", "梨肉", "去皮梨肉",
    "椰肉", "新鲜椰肉条", "椰子肉", "芒果肉", "芒果果肉", "榴莲果肉",
    "橙子果肉", "白桃果肉", "鸡腿菇", "蟹味菇", "鱼腥草", "鸡头米", "鸭梨",
])
def test_plant_and_spice_homonyms_cannot_create_protein_or_muscle_goal_evidence(food):
    item = recipe(food, name="鸡肉鱼肉高蛋白套餐")
    assert "protein" not in item.categories
    structured = analyze_recipe(item, Constraints(health_goals=["增肌"]))
    assert structured.protein_sources == []
    assert all("protein" not in part.roles for part in structured.ingredient_contributions)
    assert structured.goal_matches[0].status == "insufficient_data"
    assert all("配料含蛋白质来源" not in note for note in analyze(item, Constraints()))
    assert any("未识别不表示" in text for text in structured.limitations)


@pytest.mark.parametrize("food", ["鲍鱼", "牛肉", "鸡胸肉", "鱼肉", "鸡蛋", "豆腐", "素鸡", "猪肚"])
def test_real_animal_and_soy_foods_retain_their_protein_evidence(food):
    item = recipe(food)
    assert "protein" in item.categories
    structured = analyze_recipe(item, Constraints())
    assert structured.protein_sources == [food]
    assert any("配料含蛋白质来源：" + food in note for note in analyze(item, Constraints()))


@pytest.mark.parametrize("food", [
    "杏鲍菇鸡胸肉", "鸡腿菇牛肉", "蟹味菇豆腐", "肉桂鸡翅", "芒果果肉牛肉", "鲍鱼杏鲍菇",
    "鸡头米鸡蛋", "牛油果鸡胸肉",
])
def test_homonym_in_one_composite_name_does_not_hide_a_genuine_protein_food(food):
    item = recipe(food)
    assert "protein" in item.categories
    assert analyze_recipe(item, Constraints()).protein_sources == [food]
    assert any("配料含蛋白质来源：" + food in note for note in analyze(item, Constraints()))


@pytest.mark.parametrize(("plant", "protein"), [
    ("杏鲍菇", "鸡肉"), ("肉桂", "牛肉"), ("芒果果肉", "鱼肉"), ("蟹味菇", "豆腐"),
])
def test_separately_parsed_mixed_dish_attributes_protein_only_to_the_actual_source(plant, protein):
    item = recipe(plant, protein)
    # Primary culinary role is exclusive; a meat garnish still contributes
    # ingredient-level protein without renaming a mushroom side as an entree.
    assert item.categories == (["vegetable"] if plant in {"杏鲍菇", "蟹味菇"} else ["protein"])
    assert analyze_recipe(item, Constraints()).protein_sources == [protein]
    note = next(text for text in analyze(item, Constraints()) if "配料含蛋白质来源" in text)
    assert protein in note and plant not in note


@pytest.mark.parametrize("food", ["杏鲍菇", "鸡腿菇", "蟹味菇", "鸡枞菌", "猪肚菇", "鸡毛菜"])
def test_known_mushroom_and_leaf_names_have_consistent_vegetable_and_fiber_evidence(food):
    item = recipe(food)
    assert "vegetable" in item.categories
    assert "protein" not in item.categories
    structured = analyze_recipe(item, Constraints(health_goals=["控糖"]))
    assert structured.dietary_fiber == [food]
    assert food in structured.goal_matches[0].ingredient_names
    assert any("含蔬菜类食材" in text for text in analyze(item, Constraints()))


def test_a_mushroom_title_without_mushroom_ingredients_does_not_add_a_vegetable_role():
    item = recipe("清水", name="杏鲍菇鸡毛菜")
    assert "vegetable" not in item.categories
    assert analyze_recipe(item, Constraints()).dietary_fiber == []


def test_a_known_mushroom_after_the_first_three_ingredients_is_still_recognized():
    item = recipe("清水", "食用油", "盐", "杏鲍菇")
    assert "vegetable" in item.categories and "protein" not in item.categories
    assert analyze_recipe(item, Constraints()).dietary_fiber == ["杏鲍菇"]


def test_ignoring_a_homonym_does_not_join_unrelated_text_into_a_food_name():
    item = recipe("排肉桂骨")
    assert "protein" not in item.categories
    assert analyze_recipe(item, Constraints()).protein_sources == []


@pytest.mark.parametrize("food", ["鸡精", "鸡汤", "牛肉高汤", "鲍鱼汁", "鸡肉粉", "蚝油", "肉汤"])
def test_broths_and_condiments_do_not_turn_into_a_primary_protein_food(food):
    item = recipe(food)
    assert "protein" not in item.categories
    assert analyze_recipe(item, Constraints()).protein_sources == []
    assert all("配料含蛋白质来源" not in text for text in analyze(item, Constraints()))


@pytest.mark.parametrize(("foods", "constraints", "allowed"), [
    (("杏鲍菇",), Constraints(allergies=["海鲜"]), True),
    (("鲍鱼",), Constraints(allergies=["海鲜"]), False),
    (("杏鲍菇鲍鱼",), Constraints(allergies=["海鲜"]), False),
    (("杏鲍菇牛肉",), Constraints(excluded_ingredients=["牛肉"]), False),
])
def test_role_correction_never_changes_original_ingredient_safety(foods, constraints, allowed):
    assert RuleEngine().evaluate(recipe(*foods), constraints).allowed is allowed


def test_mushrooms_cannot_hide_the_absence_of_a_primary_protein_dish_in_menu_balance():
    menu = [recipe("杏鲍菇", name="香煎杏鲍菇"), recipe("青菜", name="清炒青菜"),
            recipe("大米", name="米饭")]
    balance = analyze_menu_balance(menu)
    assert balance.category_counts["protein"] == 0
    assert balance.category_counts["vegetable"] == 2
    assert any("蛋白质" in gap for gap in balance.gaps)


def test_planner_covers_actual_protein_and_replacements_keep_the_same_food_role():
    mushroom = recipe("杏鲍菇", name="香煎杏鲍菇")
    chicken = recipe("鸡胸肉", name="清蒸鸡胸肉")
    fish = recipe("鱼肉", name="清蒸鱼")
    green = recipe("青菜", name="清炒青菜")
    rice = recipe("大米", name="米饭")
    result = MenuPlanner(RuleEngine()).plan([mushroom, green, rice, chicken], Constraints(dish_count=3))
    assert result.failure is None
    assert chicken.recipe_id in {item.recipe_id for item in result.recipes}
    suggestions = replacement_candidates([chicken], [mushroom, chicken, fish], "synthetic-food-roles", limit=10)
    assert [item.recipe_id for item in suggestions] == [fish.recipe_id]
