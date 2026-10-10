"""Same-name source counterexamples for the merged beverage/sauce boundaries."""

import pytest

from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


@pytest.mark.parametrize("steps,role", [
    ("茶冻装入杯子，倒入牛奶后搅匀。", "drink"),
    ("冷藏凝固后加入牛奶。", "dessert"),
    ("倒入杯子后凝固成型。", "dessert"),
])
def test_same_milk_title_uses_final_source_serving_without_bypassing_allergy(steps, role):
    item = next(iter(normalize_recipes([{
        "名称": "柠檬红茶冻撞奶", "食材清单": "红茶5克；牛奶200毫升；冰糖10克",
        "烹饪步骤": steps, "label": "晚餐",
    }]).values()))
    assert item.categories == [role]
    assert item.steps == steps and item.raw_ingredients == "红茶5克；牛奶200毫升；冰糖10克"
    assert not is_main_meal_recipe(item)
    assert not RuleEngine().evaluate(item, Constraints(allergies=["牛奶"])).allowed


@pytest.mark.parametrize("steps,expected", [
    ("蒸熟后蘸酱油食用。", "vegetable"),
    ("熬煮后取出，作为调味酱使用。", "component"),
])
def test_declared_dipping_body_and_prepared_sauce_are_not_the_same_role(steps, expected):
    item = next(iter(normalize_recipes([{
        "名称": "山药蘸酱", "食材清单": "山药200克；酱油2克",
        "烹饪步骤": steps, "label": "晚餐",
    }]).values()))
    assert item.categories == [expected]
    assert is_main_meal_recipe(item) is (expected != "component")
    assert item.steps == steps
