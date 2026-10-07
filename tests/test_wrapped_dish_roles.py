"""Authored complete-wrapper contrasts, independent of source recipe data."""

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.api.presentation import build_card
from app.domain.dish_roles import primary_dish_role
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def record(name: str, foods: str, steps: str) -> Recipe:
    """Normalize a synthetic source, preserving its ingredients and preparation."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "晚餐"}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "荠菜石榴包",
            "肉末100克；荠菜100克；豆腐衣50克；盐1克",
            "荠菜与肉末混合成馅，豆腐衣剪好平铺，包入荠菜肉馅扎紧，蒸熟。",
        ),
        ("青菜小包", "豆腐皮50克；青菜100克", "豆腐皮平铺，包入青菜馅，蒸熟。"),
        ("荠菜卷", "荠菜100克；薄百叶50克；猪肉50克", "薄百叶包入荠菜肉馅，蒸熟装盘。"),
        ("韭菜卷", "千张100克；韭菜100克", "千张平铺，包入韭菜，卷起蒸熟。"),
        ("香菇小卷", "豆皮100克；香菇100克", "豆皮包入香菇馅，蒸熟。"),
        ("白菜卷", "白菜叶100克；猪肉100克", "白菜叶包入肉馅，卷起蒸熟。"),
        ("娃娃菜包", "娃娃菜100克；鸡胸肉100克", "娃娃菜叶包入鸡肉馅，蒸熟。"),
    ],
)
def test_complete_non_grain_protein_wrappers_are_not_vegetable_entree_slots(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["protein"]
    assert analyze_menu_balance([sample]).category_counts["vegetable"] == 0
    assert "蔬菜类" not in build_card(sample).badges
    assert MenuPlanner(RuleEngine()).plan([sample], Constraints(dish_count=1)).failure is None


@pytest.mark.parametrize(
    "name,foods,steps,role",
    [
        (
            "荠菜石榴包",
            "荠菜100克；猪肉10克；豆腐衣10克",
            "荠菜与肉末豆腐衣炒熟装盘。",
            "vegetable",
        ),
        ("荠菜小炒", "荠菜100克；猪肉末10克", "肉末与荠菜炒熟。", "vegetable"),
        ("白菜卷金针菇", "白菜100克；金针菇100克", "白菜叶包入金针菇卷起蒸熟。", "vegetable"),
        ("荠菜卷", "荠菜100克；鸡腿菇100克", "菜叶包入鸡腿菇馅蒸熟。", "vegetable"),
        ("白菜包", "白菜100克；鸡粉1克", "白菜包入馅后蒸熟。", "vegetable"),
        (
            "荠菜蒸饺",
            "荠菜100克；猪肉100克；面粉200克",
            "面粉揉面团擀面皮，包入肉馅蒸熟。",
            "staple",
        ),
        ("荠菜卷汤", "荠菜100克；豆腐衣100克；水500克", "豆腐衣包入荠菜馅，放入汤里煮熟。", "soup"),
        ("荠菜拼盘", "荠菜100克；豆腐衣50克", "荠菜蒸熟；豆腐衣另行包入其它馅料。", "vegetable"),
        ("荠菜包", "荠菜100克；豆腐衣50克", "豆腐衣撕碎，混合荠菜蒸熟。", "vegetable"),
    ],
)
def test_garnishes_grain_wrappers_and_soups_keep_their_distinct_roles(
    name: str, foods: str, steps: str, role: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == [role]
    assert primary_dish_role(name, (i.name for i in sample.ingredients), steps, []) == role
