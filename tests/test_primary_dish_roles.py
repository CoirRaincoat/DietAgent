"""Independent authored expectations for one dish's primary culinary role."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.api.presentation import build_card
from app.domain.dish_roles import has_protein_ingredient, primary_dish_role
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.nutrition.structured import analyze_recipe
from evaluation.menu_quality import menu_quality_snapshot

CASES_PATH = Path(__file__).resolve().parents[1] / "evaluation/cases/dish_roles_v1.json"
CASES: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def normalize(case: dict[str, Any]) -> Recipe:
    """Exercise the catalog boundary with only handwritten source text."""
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": case["name"],
                        "食材清单": case["ingredients"],
                        "烹饪步骤": case["steps"],
                        "label": "晚餐",
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_primary_role_is_exclusive_not_every_ingredient_presence(case: dict[str, Any]) -> None:
    recipe = normalize(case)
    assert recipe.categories == ([case["role"]] if case["role"] else [])


def test_chicken_sausage_summary_and_card_do_not_claim_vegetable_or_staple_dish() -> None:
    chicken = normalize(CASES[0])
    analysis = analyze_menu_balance([chicken])
    assert analysis.category_counts == {"protein": 1, "vegetable": 0, "staple": 0, "soup": 0}
    assert "蔬菜类" not in build_card(chicken).badges
    assert "主食类" not in build_card(chicken).badges
    snapshot = menu_quality_snapshot([chicken.recipe_id], {chicken.recipe_id: chicken})
    assert snapshot["role_coverage"] == 1


def test_soup_does_not_replace_protein_or_staple_entree_in_summary() -> None:
    soup = normalize(next(c for c in CASES if c["id"] == "corn_rib_soup"))
    assert analyze_menu_balance([soup]).category_counts == {
        "protein": 0,
        "vegetable": 0,
        "staple": 0,
        "soup": 1,
    }


@pytest.mark.parametrize(
    "name,expected",
    [
        ("鸡腿菇", False),
        ("牛肝菌", False),
        ("马蹄", False),
        ("马蹄粉", False),
        ("猪蹄", True),
        ("马蹄和猪蹄", True),
        ("荔枝果肉", False),
        ("肉桂", False),
        ("椰肉", False),
        ("桂圆肉", False),
        ("荔枝果肉与猪肉", True),
        ("蚝油", False),
        ("鱼香酱", False),
        ("鸡肉粉", False),
        ("鸡胸肉", True),
        ("牛柳", True),
        ("筒骨", True),
        ("豆腐", True),
        ("鸡蛋", True),
        ("鸡腿菇和鸡肉", True),
    ],
)
def test_ingredient_evidence_is_not_a_whole_dish_role(name: str, expected: bool) -> None:
    assert has_protein_ingredient(name) is expected


@pytest.mark.parametrize(
    "name,foods,expected",
    [
        ("家常拼盘", ["水", "食用油", "鸡精", "青菜"], "vegetable"),
        ("家常拼盘", ["盐", "大米"], "staple"),
        ("家常饭", ["米饭", "鸡蛋"], "staple"),
        ("莴笋菜饭", ["莴笋叶", "咸肉", "黄酒", "米"], "staple"),
        ("杂粮饭", ["米", "小米"], "staple"),
        ("家常拼盘", ["米"], "staple"),
        ("台式卤肉饭", ["洋葱碎", "五花肉丁", "生抽"], None),
        ("虾仁蛋炒饭", ["虾仁", "鸡蛋", "葱"], None),
        ("清蒸鱼", [], None),
        ("特色拼盘", ["水", "盐"], None),
        ("山药蒸蛋", ["山药", "鸡蛋"], "protein"),
        ("腌笃鲜", ["猪肉", "春笋"], "soup"),
        ("蔬菜碎", ["白菜", "胡萝卜"], "component"),
    ],
)
def test_fallback_evidence_and_precedence(
    name: str, foods: list[str], expected: str | None
) -> None:
    assert primary_dish_role(name, foods, "蒸熟后装盘。", []) == expected


def test_role_change_does_not_remove_meat_garnish_from_ingredient_analysis() -> None:
    recipe = normalize(next(c for c in CASES if c["id"] == "veg_with_pork_garnish"))
    assert recipe.categories == ["vegetable"]
    assert [i.name for i in recipe.ingredients] == ["茄子", "猪肉末", "食用油"]
    nutrition = analyze_recipe(recipe, Constraints())
    assert "猪肉末" in nutrition.protein_sources
    assert nutrition.analysis_type == "qualitative"
    assert "protein_g" not in nutrition.model_dump()


@pytest.mark.parametrize(
    "name,foods,role",
    [
        ("菠菜粳米粥", ["菠菜", "粳米", "水"], "staple"),
        ("葱爆面", ["挂面", "葱", "食用油"], "staple"),
        ("蔬菜焗蝴蝶面", ["蝴蝶面", "西葫芦"], "staple"),
        ("蘑菇意大利饭", ["意大利米", "白蘑菇"], "staple"),
        ("玻璃烧麦", ["白菜", "猪肉", "烧麦皮"], "staple"),
        ("草原羊肉烧麦", ["羊肉", "烧麦皮"], "staple"),
        ("金钱小面包", ["高筋粉", "奶粉"], "staple"),
        ("全麦吐司", ["高筋粉", "全麦粉"], "staple"),
        ("欧包", ["高粉", "全麦粉"], "staple"),
        ("酒酿馒头", ["中粉", "奶粉", "酵母"], "staple"),
        ("水晶韭菜蒸饺", ["韭菜", "猪肉", "澄面"], "staple"),
        ("虾饺", ["虾仁", "澄粉"], "staple"),
        ("冻虾饺", ["虾饺"], "staple"),
        ("复热叉烧包", ["叉烧包"], "staple"),
        ("芝士烤馒头", ["馒头", "芝士", "培根"], "staple"),
        ("烤法棍片", ["法棍", "黄油"], "staple"),
        ("红豆薏米粥", ["红豆", "薏米"], "staple"),
        ("牛奶薏仁粥", ["牛奶", "薏仁"], "staple"),
        ("海南鸡饭", ["鸡肉", "香米"], "staple"),
        ("意式肉酱面", ["意大利细面", "猪肉"], "staple"),
        ("白酒鲜虾意大利面", ["虾", "熟意大利面"], "staple"),
        ("蒸泡面", ["方便面", "鸡蛋"], "staple"),
        ("豆面", ["熟豆面", "鸡蛋"], "staple"),
        ("鸡胸肉白菜卷", ["鸡胸肉", "白菜"], "protein"),
        ("毛豆蒸肉饼", ["毛豆", "猪肉馅"], "protein"),
        ("温州鱼饼", ["鱼肉", "蛋清", "淀粉"], "protein"),
        ("三文鱼蔬菜饼", ["三文鱼", "菠菜", "鸡蛋"], "protein"),
        ("荠菜蛋饺", ["鸡蛋", "猪肉", "荠菜"], "protein"),
        ("鸡汁百叶包", ["猪肉", "薄百叶", "荠菜"], "protein"),
        ("豆腐皮素菜卷", ["菠菜", "豆腐皮"], "protein"),
        ("娃娃菜卷金针菇", ["娃娃菜", "金针菇"], "vegetable"),
    ],
)
def test_grain_aliases_and_non_grain_wrappers_preserve_culinary_role(
    name: str,
    foods: list[str],
    role: str,
) -> None:
    assert primary_dish_role(name, foods, "煮熟后装盘。", []) == role
