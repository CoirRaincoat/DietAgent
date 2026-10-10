"""Synthetic finished-food roles with explicit source and serving evidence."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def recipe(name, ingredients, steps):
    return next(iter(normalize_recipes([{
        "名称": name, "食材清单": ingredients, "烹饪步骤": steps, "label": "晚餐",
    }]).values()))


@pytest.mark.parametrize("name", ["烧烤酱", "蘸酱", "调味酱", "花生酱", "芝麻酱", "自配烧烤酱", "调配蘸酱"])
def test_standalone_sauces_are_components_and_cannot_fill_a_meal_slot(name):
    item = recipe(name, "梨肉100克；食用油10克；盐1克", "熬煮后取出，作为调味酱使用。")
    assert "component" in item.categories
    assert not item.eligible and "processing_component" in item.quality_flags
    assert MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1)).failure is not None


@pytest.mark.parametrize("name", ["固元阿胶糕", "红枣阿胶糕"])
def test_set_sweet_gel_cakes_are_desserts_without_claiming_health_effects(name):
    item = recipe(name, "阿胶粉20克；红枣100克；冰糖10克", "混合蒸好，冷却后切块食用。")
    assert "dessert" in item.categories
    assert item.eligible
    assert MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1)).failure is not None


@pytest.mark.parametrize(("name", "steps"), [
    ("香芒乌龙", "第1步：水果加水打浆。第2步：将锅内液体倒入装有冰块的杯子中，搅匀后即可食用。"),
    ("妩媚妃子笑", "第1步：水果打浆。第2步：将锅中液体倒入杯中，搅匀即可食用。"),
    ("奇葩说", "第1步：水果打碎。第2步：将锅中液体倒入杯中，淋上酸奶，搅匀后即可食用。"),
    ("白桃茉莉气泡水", "第1步：水果打碎。第2步：将锅内液体倒入装有冰块的杯中，倒入气泡水，即可食用。"),
    ("清爽A杯", "将液体倒入杯子中，搅匀即可饮用。"),
])
def test_liquid_served_in_a_cup_at_the_end_is_a_drink_even_with_an_opaque_name(name, steps):
    item = recipe(name, "苹果100克；冰水100毫升", steps)
    assert "drink" in item.categories
    assert item.eligible
    assert MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1)).failure is not None


@pytest.mark.parametrize(("name", "ingredients", "steps"), [
    ("酱牛肉", "牛肉100克；生抽10克", "牛肉煮熟后装盘。"),
    ("烤鸡配烧烤酱", "鸡肉100克；烧烤酱10克", "鸡肉烤熟后装盘。"),
    ("青菜佐芝麻酱", "青菜100克；芝麻酱10克", "青菜处理好装盘。"),
    ("咸味萝卜糕", "萝卜100克；米粉50克", "蒸熟后切块。"),
    ("酸奶水果碗", "酸奶100克；苹果100克", "将混合液体倒入碗中，搅匀即可食用。"),
    ("蒸蛋杯", "鸡蛋100克；牛奶100克", "第1步：将液体倒入杯中。第2步：蒸熟后即可食用。"),
    ("杯子蒸糕", "面粉100克；水50克", "将液体倒入杯中，再蒸熟后即可食用。"),
    ("杯装冬瓜汤", "冬瓜100克；水100克", "将锅内液体倒入杯中，即可食用。"),
    ("炒青菜", "青菜100克；水10克", "第1步：液体倒入杯中备用。第2步：青菜炒熟后装盘即可食用。"),
    ("暂存调味液", "盐1克；水100克", "将液体倒入杯中备用，不可直接饮用。"),
    ("凝固甜杯", "牛奶100克；吉利丁5克", "将液体倒入杯中，冷却凝固后即可食用。"),
    ("炖肉杯", "牛肉100克；水100克", "将液体倒入杯中，加入炖好的牛肉，即可食用。"),
])
def test_sauce_accompaniment_bowl_preparation_soup_or_solid_food_is_not_a_finished_drink(name, ingredients, steps):
    item = recipe(name, ingredients, steps)
    assert not {"drink", "dessert", "component"}.intersection(item.categories)
    assert item.eligible


def test_menu_and_unknown_role_replacements_exclude_finished_drinks_and_sauces():
    drink = recipe("清爽A杯", "苹果100克；水100克", "将液体倒入杯中，搅匀即可食用。")
    sauce = recipe("烧烤酱", "梨肉100克；盐1克", "煮好后作为调味使用。")
    cake = recipe("固元阿胶糕", "阿胶粉20克；红枣100克", "蒸好后冷却切块。")
    dishes = [recipe(name, "银耳100克；水10克", "处理好后装盘。")
              for name in ["清蒸银耳", "凉拌银耳", "葱油银耳"]]
    assert dishes[0].categories == []
    candidates = [drink, sauce, cake, *dishes]
    planned = MenuPlanner(RuleEngine()).plan(candidates, Constraints(dish_count=1))
    # Unknown primary roles now fail closed, independently of drink/sauce tags.
    assert planned.failure is not None
    assert planned.recipes == []
    suggestions = replacement_candidates([dishes[0]], candidates, "synthetic-finished-roles", limit=10)
    assert suggestions == []


def test_source_steps_and_ingredients_are_not_changed_by_a_serving_role():
    steps = "将液体倒入杯中，搅匀即可食用。"
    item = recipe("清爽A杯", "苹果100克；水100克", steps)
    assert item.raw_ingredients == "苹果100克；水100克"
    assert [part.name for part in item.ingredients] == ["苹果", "水"]
    assert item.steps == steps
    assert item.raw_label == "晚餐"
