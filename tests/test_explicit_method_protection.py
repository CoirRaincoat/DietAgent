"""Fixed source-method request contrasts, not tasted quality or health proof."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.scene_preferences import repair_scene_preferences
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def dish(name, steps, labels="晚餐", foods="鸡肉200克；盐1克"):
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": labels,
                    }
                ]
            ).values()
        )
    )


def test_requested_meal_can_change_unrequested_implicit_method():
    old = dish("早餐蒸鸡肉", "鸡肉蒸熟装盘。", "早餐")
    new = dish("晚餐煮鸡肉", "鸡肉煮熟装盘。")
    result = MenuPlanner(RuleEngine()).plan([old, new], Constraints(dish_count=1), current=[old])
    assert result.recipes == [new]


def test_requested_scene_can_change_unrequested_implicit_method():
    old = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    home = dish("家常煮鸡肉", "鸡肉煮熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [old, home],
        Constraints(dish_count=1, preferences=["家常"]),
        current=[old],
    )
    assert result.recipes == [home]


def test_no_method_request_does_not_reward_avoiding_unknown_methods():
    old = dish("早餐蒸鸡肉", "鸡肉蒸熟装盘。", "早餐")
    new = dish("晚餐鸡肉", "鸡肉放入机器，点击开始烹饪，烹饪完成装盘。")
    assert MenuPlanner(RuleEngine()).plan(
        [old, new], Constraints(dish_count=1), current=[old]
    ).recipes == [new]


def test_explicit_steam_reference_survives_a_conflicting_meal_update():
    old = dish("早餐蒸鸡肉", "鸡肉蒸熟装盘。", "早餐")
    new = dish("晚餐煮鸡肉", "鸡肉煮熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [old, new],
        Constraints(dish_count=1, preferences=["做法：蒸"]),
        current=[old],
    )
    assert result.recipes == [old]
    assert any("餐次" in warning for warning in result.warnings)


def test_explicit_method_changes_an_accepted_menu_when_recheck_is_authorized():
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    new = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [old, new],
        Constraints(dish_count=1, preferences=["做法：蒸"]),
        current=[old],
    )
    assert result.recipes == [new]
    assert any("做法" in change["reason"] for change in result.changes)


def test_explicit_method_has_real_source_priority_in_initial_ranking():
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    new = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    assert MenuPlanner(RuleEngine()).plan(
        [old, new], Constraints(dish_count=1, preferences=["蒸"])
    ).recipes == [new]


def test_source_method_coverage_is_protected_in_replacement_suggestions():
    steamed = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    boiled = dish("煮鸡肉", "鸡肉煮熟装盘。")
    assert (
        replacement_candidates(
            [steamed],
            [boiled],
            "public-methods",
            constraints=Constraints(dish_count=1, preferences=["蒸"]),
        )
        == []
    )


@pytest.mark.parametrize("scene,expected_home", [(["家常"], True), (["家常", "做法多样"], False)])
def test_explicit_diversity_is_not_inferred_from_a_default_menu(scene, expected_home):
    first = dish("炒白菜", "白菜炒熟装盘。", foods="白菜200克；盐1克")
    second = dish("蒸菠菜", "菠菜蒸熟装盘。", foods="菠菜200克；盐1克")
    home = dish("家常蒸青菜", "青菜蒸熟装盘。", foods="青菜200克；盐1克")
    result = repair_scene_preferences(
        [first, second],
        [home],
        Constraints(dish_count=2, preferences=scene),
        goal_scores={r.recipe_id: () for r in (first, second, home)},
        order={},
        food_matches=RuleEngine().food_matches,
        replace_slot=1,
    )
    assert (home in result.recipes) is expected_home


def test_empty_continue_keeps_an_accepted_menu_despite_old_method_gap():
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    new = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    result = MenuPlanner(RuleEngine()).plan(
        [old, new],
        Constraints(dish_count=1, preferences=["蒸"]),
        current=[old],
        recheck_soft_preferences=False,
    )
    assert result.recipes == [old] and result.changes == []
    assert any("做法" in warning and "尚缺" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "foods,restriction",
    [
        ("鸡肉200克；辣椒2克；盐1克", {"no_spicy": True}),
        ("鸡肉200克；花生油2克；盐1克", {"allergies": ["花生"]}),
    ],
)
def test_method_request_never_waives_spicy_or_allergy(foods, restriction):
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    unsafe = dish("蒸鸡肉", "鸡肉和配料蒸熟装盘。", foods=foods)
    result = MenuPlanner(RuleEngine()).plan(
        [old, unsafe],
        Constraints(dish_count=1, preferences=["蒸"], **restriction),
        current=[old],
    )
    assert result.failure is None and result.recipes == [old]
    assert any("尚缺" in warning for warning in result.warnings)


def test_authorized_local_method_replacement_keeps_unrelated_slot():
    first = dish("煮白菜", "白菜煮熟装盘。", foods="白菜200克；盐1克")
    old = dish("煮菠菜", "菠菜煮熟装盘。", foods="菠菜200克；盐1克")
    new = dish("蒸青菜", "青菜蒸熟装盘。", foods="青菜200克；盐1克")
    result = MenuPlanner(RuleEngine()).plan(
        [first, old, new],
        Constraints(dish_count=2, preferences=["蒸"]),
        current=[first, old],
        replace_slot=2,
    )
    assert result.recipes == [first, new]
    assert [change["slot"] for change in result.changes] == [2]
