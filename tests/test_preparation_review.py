"""Authored source cues, not human votes or medical/procedural certification."""

import pytest

from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine
from evaluation.preparation_review import preparation_review


def card(steps: str) -> Recipe:
    return Recipe(
        recipe_id="public",
        name="蒸豆腐",
        raw_ingredients="豆腐、燕麦",
        ingredients=[Ingredient(name="豆腐", raw="豆腐"), Ingredient(name="燕麦", raw="燕麦")],
        steps=steps,
        categories=["protein"],
        meal_types=["晚餐"],
        source_row=1,
        fingerprint="public",
    )


@pytest.mark.parametrize(
    "steps",
    [
        "放入设备，选择“开始烹饪”按键，按屏幕提示操作；蒸好后取出。",
        "点击开始烹饪。蒸好后取出。",
        "选择开始烹饪，按屏幕提示操作，蒸好后取出。",
        "放入设备，按屏幕提示操作。",
        "不用选择蒸模式。按屏幕提示操作。",
        "示例：选择蒸模式。点击开始烹饪。",
    ],
)
def test_finishing_witness_is_not_a_specified_machine_program(steps: str) -> None:
    review = preparation_review(card(steps))
    assert review.flags == ("device_program_not_in_source_text",)
    assert review.status == "needs_manual_review" and review.evidence


@pytest.mark.parametrize(
    "steps",
    [
        "选择“普通蒸”模式，蒸15分钟。点击开始烹饪。",
        "设置45分钟/65℃/1档慢煮。点击开始烹饪。",
        "不用点击开始烹饪。将豆腐放入锅里蒸熟。",
        "例如点击开始烹饪。将豆腐蒸熟。",
        "为什么要按屏幕提示操作？",
        "将豆腐放入锅中蒸熟。",
    ],
)
def test_named_settings_manual_recipe_omission_and_example_are_not_generic_program(
    steps: str,
) -> None:
    assert not preparation_review(card(steps)).flags


@pytest.mark.parametrize("action", ["蘸", "加入", "淋上", "淋入", "倒入", "拌入"])
def test_generic_sauce_reference_does_not_invent_salt_or_nutrients(action: str) -> None:
    recipe = card(f"将豆腐蒸熟，{action}少许酱料即可。")
    before = recipe.model_dump()
    review = preparation_review(recipe)
    assert review.flags == ("generic_sauce_addition_needs_composition_review",)
    assert all("盐" not in text for text in review.evidence)
    assert recipe.model_dump() == before


@pytest.mark.parametrize("prefix", ["不", "不要", "不用", "无需", "不必", "没有", "不要再"])
def test_literal_negative_sauce_addition_is_not_a_positive_action(prefix: str) -> None:
    assert not preparation_review(card(f"蒸熟豆腐。{prefix}加入酱料。")).flags


@pytest.mark.parametrize(
    "steps", ["蒸熟豆腐，淋上生抽。", "蘸上述酱料。", "例如蘸酱料。", "蘸酱料是什么意思？"]
)
def test_specific_or_referenced_sauce_and_discussion_are_outside_finite_pattern(
    steps: str,
) -> None:
    review = preparation_review(card(steps))
    assert not review.flags and review.status == "no_finite_findings_NOT_certified"


def test_later_positive_addition_survives_earlier_omission_and_source_mutation() -> None:
    recipe = card("不加酱料。将豆腐蒸熟后蘸酱料。")
    assert preparation_review(recipe).flags == ("generic_sauce_addition_needs_composition_review",)
    recipe.steps = "将豆腐蒸熟后装盘。"
    assert not preparation_review(recipe).flags
    recipe.name = "点击开始烹饪配酱料"
    recipe.raw_label = "按屏幕提示操作"
    assert not preparation_review(recipe).flags


def test_review_does_not_change_methods_health_scores_or_hard_screening() -> None:
    recipe = card("选择开始烹饪，按屏幕提示操作；蒸好后取出，蘸酱料。")
    constraints, rules = Constraints(no_spicy=True, health_goals=["护心"]), RuleEngine()
    before = (
        main_cooking_methods(recipe),
        rules.soft_goal_scores(recipe, constraints),
        rules.evaluate(recipe, constraints),
    )
    assert preparation_review(recipe).status == "needs_manual_review"
    assert (
        main_cooking_methods(recipe),
        rules.soft_goal_scores(recipe, constraints),
        rules.evaluate(recipe, constraints),
    ) == before
