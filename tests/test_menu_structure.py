import pytest

from app.agent.menu_structure import explicit_menu_structure


@pytest.mark.parametrize("message,expected", [
    ("安排4道菜，其中1道汤。", {"dish_count": 4, "soup_count": 1}),
    ("共四道菜，包含一道汤。", {"dish_count": 4, "soup_count": 1}),
    ("四菜一汤", {"dish_count": 5, "soup_count": 1}),
    ("4道菜加1道汤", {"dish_count": 5, "soup_count": 1}),
    ("三道菜，不要汤", {"dish_count": 3, "soup_count": 0}),
    ("两道菜，无需汤", {"dish_count": 2, "soup_count": 0}),
    ("不要汤", {"soup_count": 0}),
    ("先换第 4 道菜", {}),
    ("第二道再换一道", {}),
    ("第二道再换一道汤", {}),
    ("上次安排4道菜，这次随便", {}),
    ("解释4道菜含1道汤的意思", {}),
    ("3个人晚餐", {}),
    ('解释“四菜一汤”的意思', {}),
])
def test_menu_count_semantics(message, expected):
    counts, issue = explicit_menu_structure(message)
    assert counts == expected
    assert issue is None


@pytest.mark.parametrize("message", [
    "安排4道菜还是5道菜？",
    "安排4到5道菜",
    "如果安排4道菜，其中1道汤呢",
    "不要4道菜，要3道菜",
    "安排4道菜，然后安排5道菜",
    "安排4道菜，其中1道汤，不要汤",
    "安排2道菜，其中3道汤",
    "安排0道菜",
    "安排9道菜",
    "安排十一道菜",
    "安排4道汤",
])
def test_uncertain_conflicting_or_unsupported_counts_require_clarification(message):
    counts, issue = explicit_menu_structure(message)
    assert counts == {}
    assert issue
