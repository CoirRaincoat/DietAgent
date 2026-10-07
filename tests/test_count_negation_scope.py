"""A negation in a food/health clause must not invalidate explicit meal counts."""

import pytest

from app.agent.menu_structure import explicit_menu_structure


@pytest.mark.parametrize(
    "suffix",
    [
        "盐和酱油不是一律禁用。",
        "健康目标不是治疗承诺。",
        "不是所有豆制品都含肉。",
        "这不是低钠认证，不要宣称降压疗效。",
        "每日盐量不是凭调料出现判断。",
        "不是所有原料都有克数。",
    ],
)
@pytest.mark.parametrize("delimiter", ["。", "；", "，"])
def test_unrelated_food_or_health_negation_does_not_erase_confirmed_counts(suffix, delimiter):
    message = "四个人吃晚餐，总共5道，其中1道汤计入总数" + delimiter + suffix
    counts, issue = explicit_menu_structure(message)
    assert issue is None
    assert counts == {"dish_count": 5, "soup_count": 1}


@pytest.mark.parametrize(
    "message",
    [
        "不是5道菜。",
        "安排5道菜，不是1道汤。",
        "总共5道，其中1道汤不是最终数量。",
        "不是不要汤，安排3道菜。",
        "安排5道菜，其中1道汤，不是。",
        "如果安排5道菜，其中1道汤。盐不是禁用。",
        "安排5道菜还是6道菜。盐不是禁用。",
        "安排5道菜，其中1道汤，不要汤。盐不是禁用。",
        "安排5道菜，总共6道菜。盐不是禁用。",
        "总共2道，其中3道汤。盐不是禁用。",
    ],
)
def test_actual_count_negation_ambiguity_and_conflict_still_need_clarification(message):
    counts, issue = explicit_menu_structure(message)
    assert not counts and issue


@pytest.mark.parametrize(
    "message",
    ["解释总共5道含1道汤不是别的意思。", "只换第2道，不是5道菜。"],
)
def test_explanation_and_local_replace_do_not_gain_count_mutation_authority(message):
    assert explicit_menu_structure(message) == ({}, None)
