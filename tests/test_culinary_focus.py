"""Authored whole-dish focus contrasts, not nutrient or clinical judgments."""

from collections.abc import Sequence

import pytest

from app.agent.menu_variety import repair_menu_variety
from app.agent.planner import MenuPlanner
from app.domain.culinary_focus import culinary_food_focus
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine


def dish(name: str, foods: Sequence[str], role: str, steps: str = "蒸熟食材后装盘。") -> Recipe:
    """Construct an ordinary declared recipe fixture, with no original records."""
    return Recipe(
        recipe_id=name,
        name=name,
        ingredients=[Ingredient(raw=food, name=food) for food in foods],
        raw_ingredients="、".join(foods),
        steps=steps,
        categories=[role],
        meal_types=["晚餐"],
        methods=["蒸"],
        source_row=1,
        fingerprint=name,
    )


@pytest.mark.parametrize(
    "name,foods,role,wanted",
    [
        ("奶汁白菜", ["大白菜", "猪肉片", "面粉"], "vegetable", {"cabbage"}),
        ("油渣白菜", ["猪油", "大白菜", "猪油渣"], "vegetable", {"cabbage"}),
        ("鸡肉玉米卷", ["鸡胸肉", "胡萝卜", "玉米淀粉"], "protein", {"chicken"}),
        ("鸡蛋豆腐", ["鸡蛋", "豆腐", "胡萝卜"], "protein", {"egg", "tofu"}),
        ("小葱豆腐", ["嫩豆腐", "五花肉末"], "protein", {"tofu"}),
        ("豆腐皮素菜卷", ["豆腐皮", "大白菜", "胡萝卜"], "protein", {"tofu"}),
        ("脆香猪排", ["猪里脊肉", "鸡蛋", "生菜"], "protein", {"pork"}),
        ("蒸蛋", ["鸡蛋", "盐"], "protein", {"egg"}),
        ("香蒸贝贝南瓜", ["贝贝南瓜", "蔬菜粒"], "vegetable", {"pumpkin"}),
        ("燕麦南瓜泥", ["南瓜", "燕麦", "盐"], "vegetable", {"pumpkin"}),
        ("开花馒头", ["面粉", "熟南瓜块", "熟紫薯片"], "staple", {"wheat"}),
        ("南瓜小米粥", ["南瓜", "小米", "大米", "胡萝卜"], "staple", {"pumpkin", "millet", "rice"}),
        ("菠菜粳米粥", ["粳米", "菠菜"], "staple", {"rice", "spinach"}),
        ("番茄豆腐汤", ["西红柿", "豆腐", "鸡蛋"], "soup", {"tomato", "tofu"}),
        ("蒸红薯", ["红薯", "盐"], "staple", {"sweet_potato"}),
        ("白切肘子", ["猪肘子", "水"], "protein", {"pork"}),
        ("白切鸡", ["三黄鸡", "水"], "protein", {"chicken"}),
        ("低温温泉蛋", ["鸡蛋", "水"], "protein", {"egg"}),
        ("西红柿豆腐羹", ["西红柿", "内酯豆腐"], "soup", {"tomato", "tofu"}),
        ("醋溜山药", ["菜山药", "盐"], "vegetable", {"yam"}),
    ],
)
def test_focus_uses_declared_role_and_named_body_not_all_garnishes(
    name: str, foods: list[str], role: str, wanted: set[str]
) -> None:
    result = culinary_food_focus(dish(name, foods, role))
    assert result.families == frozenset(wanted)
    assert result.reason == "established_source_focus"


@pytest.mark.parametrize(
    "name,foods,role",
    [
        ("南瓜", ["冬瓜"], "vegetable"),
        ("蛋黄蒸肉", ["肉末", "鸡蛋", "胡萝卜"], "protein"),
        ("咸肉蒸蛋", ["咸肉", "鸡蛋"], "protein"),
        ("小米蔬菜糕", ["熟小米", "鸡蛋", "山药泥"], "staple"),
        ("成菜甲", ["南瓜"], "vegetable"),
        ("凉粉", ["未知原料"], "vegetable"),
        ("素汤", ["豆腐"], "soup"),
        ("南瓜蛋糕", ["南瓜", "面粉", "白糖"], "dessert"),
    ],
)
def test_uncertain_focus_cannot_win_as_perfect_novelty(
    name: str, foods: list[str], role: str
) -> None:
    assert not culinary_food_focus(dish(name, foods, role)).families


def test_stale_roles_multiple_roles_and_source_mutations_are_not_cached_as_proof() -> None:
    record = dish("南瓜米饭", ["南瓜", "大米"], "staple", "大米加水煮熟后加入南瓜。")
    assert culinary_food_focus(record).families == frozenset({"rice", "pumpkin"})
    record.categories = ["staple", "vegetable"]
    assert not culinary_food_focus(record).families
    record.categories = ["staple"]
    record.eligible = False
    assert not culinary_food_focus(record).families
    record.eligible = True
    record.name = "开花馒头"
    record.ingredients = [Ingredient(name="面粉", raw="面粉")]
    assert culinary_food_focus(record).families == frozenset({"wheat"})
    record.steps = "食材准备、清洗后备用。"
    assert not culinary_food_focus(record).families


def focused_repair(menu: list[Recipe], pool: list[Recipe]) -> list[Recipe]:
    """Use explicitly computed source-focus evidence in otherwise equal peers."""
    return repair_menu_variety(
        menu,
        pool,
        Constraints(dish_count=len(menu)),
        goal_scores={r.recipe_id: () for r in pool},
        rule_scores={r.recipe_id: 0 for r in pool},
        relevance={r.recipe_id: 0.0 for r in pool},
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, term: False,
        family_evidence={r.recipe_id: culinary_food_focus(r).families for r in pool},
    ).recipes


def test_same_body_cannot_reduce_repeat_by_hiding_or_changing_a_garnish() -> None:
    menu = [
        dish("白菜一", ["大白菜", "鸡蛋"], "vegetable"),
        dish("白菜二", ["大白菜", "猪肉片"], "vegetable"),
    ]
    equivalent = dish("白菜三", ["大白菜"], "vegetable")
    different = dish("蒸西兰花", ["西兰花"], "vegetable")
    assert focused_repair(menu, [*menu, equivalent]) == menu
    result = focused_repair(menu, [*menu, equivalent, different])
    assert result == [different, menu[1]]
    assert focused_repair(result, [*menu, equivalent, different]) == result


def test_incidental_pork_does_not_force_a_pork_entree_swap() -> None:
    menu = [
        dish("奶汁白菜", ["大白菜", "猪肉片"], "vegetable"),
        dish("猪排", ["猪里脊肉", "鸡蛋"], "protein"),
    ]
    unknown = dish("咸肉蒸蛋", ["咸肉", "鸡蛋"], "protein")
    assert focused_repair(menu, [*menu, unknown]) == menu


def test_named_pumpkin_staple_can_swap_but_unknown_cake_cannot_win() -> None:
    menu = [
        dish("香蒸南瓜", ["南瓜"], "vegetable"),
        dish("南瓜小米粥", ["小米", "南瓜"], "staple", "小米加水和南瓜煮成粥。"),
    ]
    unknown = dish("小米蔬菜糕", ["熟小米", "鸡蛋", "山药泥"], "staple")
    wheat = dish("开花馒头", ["面粉", "熟南瓜块"], "staple")
    assert focused_repair(menu, [*menu, unknown]) == menu
    result = focused_repair(menu, [*menu, unknown, wheat])
    assert result == [menu[0], wheat]
    # The replacement still declares pumpkin; focused diversity is not absence.
    assert "熟南瓜块" in [i.name for i in result[1].ingredients]


def test_planner_can_explicitly_select_focus_mode_and_preserve_plain_continue() -> None:
    menu = [
        dish("白菜一", ["白菜", "猪肉片"], "vegetable"),
        dish("猪排", ["猪里脊肉", "鸡蛋"], "protein"),
    ]
    unknown = dish("咸肉蒸蛋", ["咸肉", "鸡蛋"], "protein")
    planner = MenuPlanner(RuleEngine())
    result = planner.plan(
        [*menu, unknown],
        Constraints(dish_count=2),
        current=menu,
        experiment_menu_variety="culinary_focus",
    )
    assert result.recipes == menu
    assert not result.failure
    assert (
        planner.plan(
            [*menu, unknown],
            Constraints(dish_count=2),
            current=menu,
            experiment_menu_variety="culinary_focus",
            recheck_soft_preferences=False,
        ).recipes
        == menu
    )
