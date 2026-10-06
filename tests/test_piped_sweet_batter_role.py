"""Authored culinary contrasts; source recipes and health effects are not gold labels."""

import csv
from pathlib import Path

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.domain.source_preparation import preparation_dessert_evidence
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine
from tests.test_dessert_preparation_gate import ordinary_menu, record

FOODS = "蛋白40克；中筋面粉80克；黄油40克；糖粉30克"
STEPS = "黄油加糖粉搅匀，加入蛋白，拌入面粉成面糊。面糊装入裱花袋，在烤盘挤成条状，烤到边缘金黄。"


@pytest.mark.parametrize("name", ["蛋白薄脆饼", "金色小条", "薄片", "面粉小点"])
def test_finished_sweet_piped_batter_is_not_a_meal_under_neutral_titles(name: str) -> None:
    sample = record(name, FOODS, STEPS)
    assert preparation_dessert_evidence((i.name for i in sample.ingredients), STEPS)
    assert not is_main_meal_recipe(sample)
    stale = sample.model_copy(update={"categories": ["staple"], "eligible": True})
    assert not is_main_meal_recipe(stale)
    assert MenuPlanner(RuleEngine()).plan([stale], Constraints(dish_count=1)).failure
    assert replacement_candidates([ordinary_menu()[2]], [stale], "stable") == []


@pytest.mark.parametrize(
    "steps",
    [
        STEPS.replace("烤到边缘金黄", "烤至酥脆"),
        STEPS.replace("挤成条状", "挤出一条条面糊"),
        STEPS.replace("裱花袋", "中号圆孔裱花袋"),
    ],
)
def test_finite_piping_and_baking_variants(steps: str) -> None:
    assert preparation_dessert_evidence(["蛋白", "面粉", "黄油", "糖粉"], steps)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("咸薄脆饼", "面粉100克；水80克；葱10克；盐1克", "面糊摊薄，煎熟装盘。"),
        ("咸薄饼", FOODS + "；葱10克；生抽2克", STEPS),
        ("面包", FOODS + "；酵母2克", STEPS),
        ("面包", FOODS, "黄油糖粉与面粉揉成面团，发酵后烤熟。"),
        ("蛋白薄脆饼", FOODS, "面糊装入裱花袋，挤成条状，放在烤盘备用。"),
        ("薄饼", FOODS, "面糊摊在平底锅，煎熟装盘。"),
        ("裹饼干鸡肉", "鸡胸肉100克；饼干30克；黄油5克；糖粉1克", "鸡肉裹饼干碎烤熟装盘。"),
        ("鸡肉小条", FOODS + "；鸡胸肉100克", STEPS),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "不要烤到边缘金黄")),
        ("薄饼", FOODS, STEPS.replace("面糊装入", "不要把面糊装入")),
        ("薄饼", FOODS, "如果制作甜点：" + STEPS),
        ("薄饼", FOODS, "可选做法：" + STEPS),
        ("薄饼", FOODS, "示例：" + STEPS),
        ("薄饼", FOODS, "参考另一个菜谱：" + STEPS),
        ("薄饼", FOODS, "有人说：“" + STEPS + "”"),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "可以烤到边缘金黄")),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "烤箱预热备用")),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "不烤到边缘金黄")),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "尚未烤到边缘金黄")),
        ("薄饼", FOODS, STEPS.replace("面糊装入", "可将面糊装入")),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "可能烤到边缘金黄")),
        ("薄饼", FOODS, STEPS.replace("烤到边缘金黄", "建议烤到边缘金黄")),
        ("薄饼", FOODS, "烤至金黄。黄油糖粉拌面粉成面糊，装裱花袋挤成条状备用。"),
    ],
)
def test_savory_meal_and_unasserted_or_unfinished_evidence_are_not_dessert(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert preparation_dessert_evidence((i.name for i in sample.ingredients), steps) == []
    # Unknown steps are not certified cooked/safe, but this new dessert gate
    # must not infer a dessert from sugar, a thin-bread title or quoted actions.
    assert "dessert" not in sample.categories
    assert is_main_meal_recipe(sample)


def test_existing_stale_staple_is_repaired_without_changing_verified_other_slots() -> None:
    sample = record("金色小条", FOODS, STEPS).model_copy(
        update={"categories": ["staple"], "eligible": True}
    )
    menu = ordinary_menu()
    result = MenuPlanner(RuleEngine()).plan(
        [sample, *menu], Constraints(), current=[*menu[:2], sample]
    )
    assert result.recipes == menu


def test_earlier_dont_whip_instruction_does_not_negate_later_baked_finish() -> None:
    steps = "黄油加糖粉搅匀，不要打发。" + STEPS
    assert preparation_dessert_evidence(["蛋白", "面粉", "黄油", "糖粉"], steps)


def test_actual_exposed_source_row_not_just_authored_fixture() -> None:
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    sample = recipes["recipe_911467143c1375b5e96b9c4f"]
    assert sample.name == "蛋白薄脆饼" and sample.source_row == 1065
    assert "不要打发" in sample.steps and "或者垫" in sample.steps
    assert "“开始烹饪”" in sample.steps
    assert not is_main_meal_recipe(sample)
    assert sample.categories == ["dessert"]
