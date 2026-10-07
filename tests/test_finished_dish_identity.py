"""Authored contrasts: whole dish identity is not incidental ingredient presence."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def record(name: str, ingredients: str, steps: str) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [dict(名称=name, 食材清单=ingredients, 烹饪步骤=steps, label="晚餐、清淡")]
            ).values()
        )
    )


COOKIE_FOODS = "鸡蛋2个；黄油85克；糖粉40克；低筋面粉85克；玉米淀粉85克；盐1克"
COOKIE_STEPS = "将黄油糖粉面粉混合成面团。面团分成小面团搓圆。用大拇指从中间按压至四周裂开。烘烤至表面金黄，取出晾凉食用。"


@pytest.mark.parametrize("name", ["小圆点", "玛格丽特", "妈妈的小点心"])
def test_finished_thumb_pressed_sweet_dough_is_not_egg_entree(name: str) -> None:
    recipe = record(name, COOKIE_FOODS, COOKIE_STEPS)
    assert recipe.categories == ["dessert"]


@pytest.mark.parametrize("name", ["蒸秋季时蔬", "烤时蔬", "蔬菜拼盘"])
@pytest.mark.parametrize(
    "foods",
    [
        "土豆100克；胡萝卜80克；花菜80克；豆腐60克；盐1克",
        "豆腐60克；胡萝卜80克；花菜80克；土豆100克；盐1克",
    ],
)
def test_explicit_mixed_vegetable_dish_is_not_first_potato_or_tofu_role(
    name: str, foods: str
) -> None:
    recipe = record(name, foods, "将食材切块摆入盘中，蒸熟后装盘。")
    assert recipe.categories == ["vegetable"]


@pytest.mark.parametrize(
    "name,foods,expected",
    [
        ("鸡胸肉配时蔬", "鸡胸肉200克；胡萝卜80克；花菜80克", "protein"),
        ("时蔬鸡肉", "鸡肉200克；胡萝卜80克；花菜80克", "protein"),
        ("时蔬饭", "大米200克；胡萝卜80克；花菜80克", "staple"),
        ("时蔬汤", "水500克；胡萝卜80克；花菜80克", "soup"),
        ("蒸土豆", "土豆200克；盐1克", "staple"),
        ("时蔬", "土豆200克；盐1克", "staple"),
        ("蛋羹配时蔬", "鸡蛋2个；胡萝卜80克；花菜80克", "protein"),
    ],
)
def test_specific_main_identity_precedes_generic_mixed_vegetable_title(
    name: str, foods: str, expected: str
) -> None:
    assert record(name, foods, "食材煮熟后装盘。").categories == [expected]


def test_planner_does_not_use_sweet_dough_as_preferred_egg_entree() -> None:
    cookie = record("小圆点", COOKIE_FOODS, COOKIE_STEPS)
    egg = record("蒸鸡蛋", "鸡蛋2个；水100克；盐1克", "鸡蛋加水蒸熟食用。")
    veg = record("蒸花菜", "花菜200克；盐1克", "花菜蒸熟装盘。")
    staple = record("米饭", "大米200克；水300克", "大米加水煮熟食用。")
    menu = MenuPlanner(RuleEngine()).plan(
        [cookie, veg, staple, egg], Constraints(preferred_ingredients=["鸡蛋"])
    )
    assert menu.failure is None
    assert egg.recipe_id in {r.recipe_id for r in menu.recipes}
    assert cookie.recipe_id not in {r.recipe_id for r in menu.recipes}


def test_original_ingredients_and_allergy_gate_not_erased_by_culinary_role() -> None:
    recipe = record(
        "蒸时蔬", "土豆100克；花菜80克；胡萝卜80克；虾10克；辣椒5克", "全部食材蒸熟后装盘。"
    )
    assert recipe.categories == ["vegetable"]
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["虾"])).allowed
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed
    assert "虾10克" in recipe.raw_ingredients and "辣椒5克" in recipe.raw_ingredients


@pytest.mark.parametrize(
    "foods",
    [
        "土豆100克；花菜80克；菜花80克",
        "土豆100克；卷心菜80克；包菜80克",
        "土豆100克；山药80克；淮山80克",
        "土豆100克；番茄80克；西红柿80克",
    ],
)
def test_vegetable_aliases_do_not_create_two_distinct_declared_vegetables(foods: str) -> None:
    assert record("蒸时蔬", foods, "食材蒸熟后装盘。").categories == ["staple"]


@pytest.mark.parametrize(
    "steps",
    [
        "将黄油糖粉面粉混合成面团。烘烤至金黄。分小面团搓圆。用拇指按压至四周裂开。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。不要用拇指按压至四周裂开。烘烤至金黄。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。用拇指按压至四周裂开。不要烘烤。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。用拇指按压至四周裂开。烤箱预热到180℃。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。用拇指按压至四周裂开。包入鸡肉馅后烘烤。",
        "将黄油糖粉面粉混合成面团。面团发酵。小面团搓圆。用拇指按压至四周裂开。烘烤至金黄。",
        "将黄油糖粉面粉混合成面团。例如小面团搓圆。用拇指按压至四周裂开。烘烤至金黄。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。用拇指按压至四周裂开。不烘烤。",
        "将黄油糖粉面粉混合成面团。小面团搓圆。用拇指按压至四周裂开。未烘烤。",
        "将黄油糖粉面粉混合成面团。参考“分小面团搓圆”。用拇指按压至四周裂开。烘烤至金黄。",
    ],
)
def test_partial_reversed_negated_or_stuffed_steps_do_not_prove_cookie(steps: str) -> None:
    assert record("小圆点", COOKIE_FOODS, steps).categories != ["dessert"]


@pytest.mark.parametrize(
    "foods",
    [
        COOKIE_FOODS.replace("黄油85克；", ""),
        COOKIE_FOODS.replace("糖粉40克；", ""),
        COOKIE_FOODS.replace("低筋面粉85克；", ""),
        COOKIE_FOODS + "；酵母2克",
        COOKIE_FOODS + "；泡打粉2克",
        COOKIE_FOODS + "；鸡肉100克",
    ],
)
def test_missing_or_conflicting_declarations_do_not_prove_cookie(foods: str) -> None:
    assert record("小圆点", foods, COOKIE_STEPS).categories != ["dessert"]


def test_stale_protein_cache_does_not_promote_cookie_to_legal_main_meal() -> None:
    cookie = record("小圆点", COOKIE_FOODS, COOKIE_STEPS).model_copy(
        update={"categories": ["protein"], "eligible": True}
    )
    assert not is_main_meal_recipe(cookie)
    egg = record("蒸鸡蛋", "鸡蛋2个；水100克", "蒸熟食用。")
    result = MenuPlanner(RuleEngine()).plan([cookie, egg], Constraints(dish_count=1))
    assert [r.recipe_id for r in result.recipes] == [egg.recipe_id]


def test_sweet_dough_keeps_egg_and_dairy_hard_allergy_checks() -> None:
    cookie = record("小圆点", COOKIE_FOODS, COOKIE_STEPS)
    assert cookie.categories == ["dessert"]
    assert not RuleEngine().evaluate(cookie, Constraints(allergies=["鸡蛋"])).allowed
    assert not RuleEngine().evaluate(cookie, Constraints(allergies=["牛奶"])).allowed


def test_culinary_role_does_not_rewrite_source_identity_labels_steps_or_foods() -> None:
    original = record("时蔬", "土豆100克；花菜80克；胡萝卜80克；豆腐50克", "全部食材蒸熟后装盘。")
    same = record(original.name, original.raw_ingredients, original.steps)
    assert original.model_dump() == same.model_dump()
    assert original.categories == ["vegetable"]
    assert original.labels == ["晚餐", "清淡"]
