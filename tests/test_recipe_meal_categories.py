"""Synthetic culinary-role counterexamples; never infer nutrition quantities."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def normalized(name, ingredients="主料：苹果100克；胡萝卜100克；水200毫升", steps="混合后装盘。"):
    return next(iter(normalize_recipes([{
        "名称": name, "食材清单": ingredients, "烹饪步骤": steps, "label": "晚餐",
    }]).values()))


@pytest.mark.parametrize(("name", "ingredients", "steps", "expected"), [
    ("果蔬汁", "苹果100克；胡萝卜100克；水200毫升", "主料打浆，倒出即可享用。", "drink"),
    ("蔬菜汁", "胡萝卜100克；水200毫升", "主料打浆后装杯。", "drink"),
    ("橙子胡萝卜汁", "橙子100克；胡萝卜100克；水200毫升", "榨汁后倒出即可饮用。", "drink"),
    ("山楂糕", "山楂100克；糖10克", "山楂和糖煮好后冷藏凝固。", "dessert"),
    ("蜂蜜枣糕", "红枣100克；面粉100克；蜂蜜10克", "枣、面粉和蜂蜜拌匀，蒸好切块。", "dessert"),
    ("八宝枣糕", "红枣100克；糯米粉100克；糖10克", "枣和粉类混合后蒸熟。", "dessert"),
])
def test_unlabelled_juice_and_sweet_cakes_cannot_be_main_meals(name, ingredients, steps, expected):
    item = normalized(name, ingredients, steps)
    assert expected in item.categories
    assert item.eligible  # Keep the source available; its role excludes it from dinner.
    result = MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1))
    assert result.failure is not None
    assert result.recipes == []


@pytest.mark.parametrize(("name", "steps"), [
    ("茄汁白玉菇", "番茄炒好后加入白玉菇，煮熟装盘。"),
    ("橙汁鸡", "用橙汁腌鸡肉，煎熟收汁。"),
    ("咸味萝卜糕", "萝卜和米粉、虾米混合蒸熟。"),
    ("荠菜春笋炒年糕", "年糕和蔬菜炒熟装盘。"),
    ("枣香蒸米饭", "加入红枣蒸米饭。"),
    ("浓缩调味汁", "不可直接饮用，加入蔬菜中调味。"),
])
def test_savoury_food_and_sauce_words_do_not_become_sweet_or_drinks(name, steps):
    item = normalized(name, steps=steps)
    assert not {"drink", "dessert"}.intersection(item.categories)


def test_source_identity_and_raw_fields_do_not_depend_on_culinary_role():
    rows = [{"名称": "果蔬汁", "食材清单": "主料：苹果100克；水200毫升",
             "烹饪步骤": "打浆后装杯。", "label": "晚餐"}]
    first = normalize_recipes(rows)
    reordered = normalize_recipes(reversed(rows))
    assert set(first) == set(reordered)
    item = next(iter(first.values()))
    assert item.raw_ingredients == rows[0]["食材清单"]
    assert item.steps == rows[0]["烹饪步骤"]
    assert item.raw_label == rows[0]["label"]


def test_menu_and_suggestions_use_real_food_not_unlabelled_drinks():
    drink = normalized("果蔬汁", steps="打浆后装杯。")
    dessert = normalized("山楂糕", "主料：山楂100克；糖10克", "冷藏切块。")
    vegetables = [normalized(name, "主料：青菜100克；水10毫升", "蒸熟装盘。")
                  for name in ["清蒸青菜", "凉拌青菜", "水煮青菜"]]
    candidates = [drink, dessert, *vegetables]
    result = MenuPlanner(RuleEngine()).plan(candidates, Constraints(dish_count=1))
    assert result.failure is None
    assert result.recipes[0].recipe_id in {item.recipe_id for item in vegetables}
    suggestions = replacement_candidates(result.recipes, candidates, "synthetic-meal-categories")
    assert suggestions
    assert all(item.recipe_id in {vegetable.recipe_id for vegetable in vegetables} for item in suggestions)
