"""Finite authored brown-rice contrasts; not nutrition or independent gold."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_source_health_replay_roles import record


@pytest.mark.parametrize("food", ["糙米", "熟糙米", "发芽糙米", "糙米粉"])
def test_declared_brown_rice_can_support_named_staple(food):
    recipe = record("糙米饭" if food != "糙米粉" else "糙米饼", food + "200克", "蒸熟装盘。")
    assert recipe.categories == ["staple"]
    assert is_main_meal_recipe(recipe)


@pytest.mark.parametrize("food", ["糙米醋", "糙米酱", "糙米茶", "糙米蛋白粉", "糙米提取物"])
def test_brown_rice_word_inside_other_product_is_not_grain_evidence(food):
    recipe = record("拌白菜", food + "10克；白菜200克", "拌匀装盘。")
    assert recipe.categories == ["vegetable"]
    mismatch = record("糙米饭", food + "10克", "蒸熟装盘。")
    assert mismatch.categories != ["staple"]


@pytest.mark.parametrize(
    "name,foods,steps,role",
    [
        ("糙米茶", "糙米20克；水500克", "冲泡后作为茶饮。", "drink"),
        ("糙米鸡肉汤", "糙米50克；鸡肉100克；水500克", "煮成汤盛碗。", "soup"),
        ("鸡肉糙米饭", "糙米200克；鸡肉100克", "同煮熟装盘。", "staple"),
        ("清蒸鸡肉配糙米饭", "鸡肉200克；糙米100克", "鸡肉蒸熟，另煮糙米饭配食。", "protein"),
        ("糙米饭配鸡肉", "糙米200克；鸡肉100克", "糙米煮熟配鸡肉。", "staple"),
        ("鸡肉糙米饭配青菜", "糙米200克；鸡肉100克；青菜50克", "糙米鸡肉煮熟，配青菜。", "staple"),
        ("蒸鸡肉", "鸡肉200克；糙米50克", "鸡肉蒸熟装盘。", "protein"),
        ("糙米饭", "鸡肉200克", "蒸熟装盘。", None),
        ("花生糙米浆", "花生50克；糙米100克；白糖20克；水1000克", "煮熟后打浆，加水食用。", None),
        ("糙米浆", "糙米100克；水1000克", "煮熟后打浆。", None),
    ],
)
def test_brown_rice_keeps_finished_role_and_source_mismatch(name, foods, steps, role):
    recipe = record(name, foods, steps)
    assert recipe.categories == ([role] if role else [])


def test_brown_rice_candidate_reaches_real_planner_without_waiving_hard_gates():
    rice = record("糙米饭", "糙米200克", "糙米煮熟装盘。")
    chicken = record("蒸鸡肉", "鸡肉200克", "蒸熟装盘。")
    cabbage = record("蒸白菜", "白菜200克", "蒸熟装盘。")
    rules = RuleEngine()
    c = Constraints(
        dish_count=3,
        meal_type="晚餐",
        no_spicy=True,
        health_goals=["护心"],
        preferred_ingredients=["糙米"],
    )
    planned = MenuPlanner(rules).plan([rice, chicken, cabbage], c)
    assert not planned.failure
    assert rice in planned.recipes
    assert rules.food_matches(rice, "糙米")
    assert rules.goal_evidence(rice, "护心").preferred_foods
    spicy = record("辣椒糙米饭", "糙米200克；辣椒20克", "煮熟装盘。")
    peanut = record("花生糙米饭", "糙米200克；花生20克", "煮熟装盘。")
    assert not rules.evaluate(spicy, c).allowed
    assert not rules.evaluate(peanut, c.model_copy(update={"allergies": ["花生"]})).allowed
