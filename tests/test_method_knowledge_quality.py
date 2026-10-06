"""Known finishing coverage is evidence, not a clinical or diversity score."""

import pytest

from app.agent.health_preferences import repair_health_preferences
from app.agent.menu_balance import balance_rank
from app.agent.planner import MenuPlanner
from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_component_slots import dish


def known_menu():
    return [
        dish("蒸白菜", "白菜200克；水100克", "白菜蒸熟后装盘。"),
        dish("煮鸡蛋", "鸡蛋150克；水300克", "鸡蛋煮熟后食用。"),
        dish("烤鸡肉", "鸡肉200克", "鸡肉烤熟后装盘。"),
    ]


def opaque_meat():
    return dish(
        "惠灵顿牛排",
        "冷冻惠灵顿牛排300克",
        "放入烤箱，按设备提示操作，烹饪结束后装盘。",
    ).model_copy(update={"methods": ["烤"]})


def test_unknown_finishing_action_cannot_win_by_lowering_recorded_method_concentration():
    menu = known_menu()
    repeated = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟后装盘。")
    unknown = opaque_meat()
    assert not main_cooking_methods(unknown)
    assert balance_rank([*menu, repeated], 4) > balance_rank([*menu, unknown], 4)


def test_known_method_coverage_does_not_displace_unfilled_culinary_role():
    menu = known_menu()
    known_meat = dish("蒸猪肉", "猪肉200克", "猪肉蒸熟后装盘。")
    unknown_rice = dish("糯米饭", "糯米200克；水300克", "放入设备按提示烹饪，完成后食用。")
    c = Constraints(dish_count=4, meal_type="晚餐")
    assert balance_rank([*menu, unknown_rice], 4, c) > balance_rank([*menu, known_meat], 4, c)


def health_swap(old, new, **extra):
    c = Constraints(dish_count=1, meal_type="晚餐", health_goals=["护心"], **extra)
    return repair_health_preferences(
        [old],
        [old, new],
        c,
        scores={old.recipe_id: (0,), new.recipe_id: (1,)},
        order={old.recipe_id: 0, new.recipe_id: 1},
        food_matches=lambda _r, _t: False,
    )


def test_health_proxy_cannot_trade_known_finishing_action_for_unknown_composite_product():
    old = known_menu()[2]
    new = opaque_meat()
    assert old.categories == new.categories == ["protein"]
    result = health_swap(old, new)
    assert result.recipes == [old] and not result.changed_indices


def test_health_repair_can_change_unrequested_method_identity_when_both_are_known():
    old = known_menu()[2]
    new = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟后装盘。")
    assert health_swap(old, new).recipes == [new]


def test_health_repair_still_protects_explicit_method_identity():
    old = known_menu()[2]
    new = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟后装盘。")
    assert health_swap(old, new, preferences=["做法：烤"]).recipes == [old]


def test_health_repair_can_improve_an_unknown_record_without_inventing_a_method():
    old = opaque_meat()
    new = dish("牛肉片", "牛肉200克", "放入设备按提示烹饪，烹饪结束后装盘。")
    assert not main_cooking_methods(old) and not main_cooking_methods(new)
    assert health_swap(old, new).recipes == [new]


def test_health_repair_can_replace_unknown_with_known_source_action():
    old = opaque_meat()
    new = known_menu()[2]
    assert health_swap(old, new).recipes == [new]


@pytest.mark.parametrize("allergic", [False, True])
def test_available_known_method_never_waives_non_spicy_or_allergy_gate(allergic):
    unknown = dish("家常菠菜", "菠菜200克", "放入设备按提示烹饪，烹饪结束后装盘。")
    unsafe = (
        dish("虾菠菜", "菠菜200克；虾50克", "菠菜和虾蒸熟后装盘。")
        if allergic
        else dish("辣椒菠菜", "菠菜200克；辣椒5克", "菠菜辣椒蒸熟后装盘。")
    )
    c = Constraints(dish_count=1, meal_type="晚餐", no_spicy=True, allergies=["虾"])
    result = MenuPlanner(RuleEngine()).plan([unknown, unsafe], c)
    assert result.failure is None and result.recipes == [unknown]
    assert not main_cooking_methods(result.recipes[0])


@pytest.mark.parametrize("priority", [None, "meal"])
def test_method_knowledge_preference_does_not_override_confirmed_dinner_reference(priority):
    menu = known_menu()
    dinner = dish(
        "糯米饭", "糯米200克；水300克", "放入设备按提示烹饪，烹饪结束后食用。"
    ).model_copy(update={"raw_label": "晚餐", "meal_types": ["晚餐"]})
    breakfast = dish("蒸小米饭", "小米200克；水300克", "小米加水蒸熟后装盘。").model_copy(
        update={"raw_label": "早餐", "meal_types": ["早餐"]}
    )
    c = Constraints(
        dish_count=4,
        meal_type="晚餐",
        preferences=["做法多样"],
        method_meal_priority=priority,
    )
    result = MenuPlanner(RuleEngine()).plan([*menu, dinner, breakfast], c, current=menu)
    assert result.failure is None
    assert result.recipes[:3] == menu
    assert result.recipes[3] == dinner
    assert not main_cooking_methods(dinner)
