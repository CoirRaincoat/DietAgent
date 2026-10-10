"""Synthetic meal-role regressions for finished milk drinks and puddings."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def normalized(name, ingredients, steps):
    return next(iter(normalize_recipes([{
        "名称": name, "食材清单": ingredients, "烹饪步骤": steps, "label": "晚餐",
    }]).values()))


@pytest.mark.parametrize(("name", "ingredients", "steps", "expected"), [
    ("柠檬红茶冻撞奶", "红茶5克；白凉粉5克；牛奶200毫升；冰糖10克",
     "茶冻装入杯子，倒入牛奶后搅匀。", "drink"),
    ("芒果撞奶", "芒果100克；牛奶200毫升", "芒果打浆装杯，倒入牛奶。", "drink"),
    ("姜撞奶", "生姜20克；牛奶200毫升；糖10克", "姜汁与热牛奶混合，静置凝固。", "dessert"),
    ("红枣姜撞奶", "红枣10克；生姜20克；牛奶200毫升",
     "牛奶加热后与姜汁混合，静置凝固。", "dessert"),
])
def test_finished_milk_drinks_and_puddings_cannot_fill_a_dinner_slot(name, ingredients, steps, expected):
    item = normalized(name, ingredients, steps)
    assert {"drink", "dessert"}.intersection(item.categories) == {expected}
    assert item.eligible
    result = MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1))
    assert result.failure is not None
    assert result.recipes == []


@pytest.mark.parametrize(("name", "ingredients", "steps"), [
    ("牛奶蒸蛋", "牛奶100毫升；鸡蛋2个", "蛋液与牛奶混合后蒸熟。"),
    ("红茶炖鸡", "鸡肉200克；红茶5克", "茶水与鸡肉一起炖熟。"),
    ("奶香蒸米饭", "大米100克；牛奶100毫升", "大米与牛奶混合后蒸熟。"),
    ("姜汁蒸鱼", "鱼肉200克；生姜10克", "鱼与姜汁一起蒸熟。"),
])
def test_milk_tea_or_ginger_in_a_savoury_dish_does_not_exclude_it(name, ingredients, steps):
    item = normalized(name, ingredients, steps)
    assert not {"drink", "dessert"}.intersection(item.categories)
    result = MenuPlanner(RuleEngine()).plan([item], Constraints(dish_count=1))
    assert result.failure is None
    assert [recipe.recipe_id for recipe in result.recipes] == [item.recipe_id]


def test_unknown_role_main_dishes_do_not_receive_milk_drink_replacements():
    drinks = [normalized(name, "牛奶200毫升；冰糖10克", "装杯后搅匀。")
              for name in ["柠檬红茶冻撞奶", "芒果撞奶"]]
    vegetables = [normalized(name, "银耳100克；清水50毫升", "煮熟装盘。")
                  for name in ["清蒸银耳", "凉拌银耳", "葱油银耳"]]
    # Unknown primary roles do not silently become verified meal dishes.
    assert vegetables[0].categories == []
    candidates = [*drinks, *vegetables]
    result = MenuPlanner(RuleEngine()).plan(candidates, Constraints(dish_count=1))
    assert result.failure is not None
    assert result.recipes == []
    suggestions = replacement_candidates([vegetables[0]], candidates, "synthetic-milk-drinks", limit=10)
    assert suggestions == []


@pytest.mark.parametrize("name", ["柠檬红茶冻撞奶", "姜撞奶"])
def test_dairy_allergy_remains_independent_of_a_drink_or_dessert_role(name):
    item = normalized(name, "牛奶200毫升；生姜10克", "混合后静置。")
    decision = RuleEngine().evaluate(item, Constraints(allergies=["牛奶"]))
    assert not decision.allowed
    assert any("过敏" in reason for reason in decision.reasons)
