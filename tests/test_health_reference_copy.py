"""Ranking term membership never establishes a main ingredient or its share."""
import pytest

from app.domain.models import Constraints
from app.nutrition.structured import analyze_menu, analyze_recipe
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def test_original_carrot_15g_is_a_declared_reference_not_a_certified_main_food():
    source = catalog()[971]
    assert source.name == "藕丁酿香菇"
    assert next(i.quantity for i in source.ingredients if i.name == "胡萝卜") == 15
    c = Constraints(health_goals=["护心"])
    match = analyze_recipe(source, c).goal_matches[0]
    text = "".join(match.reasons)
    assert "胡萝卜" in text and "香菇" in text
    assert "排序规则所参考的已声明食材" in text
    assert "未核主辅比例" in text
    assert "菜品主体" not in text
    assert "不据此评整道菜的健康功效" in text


@pytest.mark.parametrize("goal", ["护心", "降压", "增肌"])
def test_recipe_and_menu_use_same_reference_boundary_without_changing_status(goal):
    source = dish("蒸豆腐", "豆腐200克；盐2克", "豆腐蒸熟装盘。")
    c = Constraints(health_goals=[goal])
    one, whole = analyze_recipe(source, c).goal_matches[0], analyze_menu([source], c).goal_matches[0]
    assert one == whole
    if any("排序规则" in reason for reason in one.reasons):
        assert "未核主辅比例" in "".join(one.reasons)
    assert "菜品主体" not in "".join(one.reasons)
    # Existing BP rules do not award this protein-slot food a category bonus.
    # Nutrient-source disclosure is independent of goal ranking membership.
    if goal == "降压":
        assert one.ingredient_names == ["盐"]
        assert "豆腐" in analyze_recipe(source, c).protein_sources
    else:
        assert "豆腐" in one.ingredient_names
    assert RuleEngine().evaluate(source, c).allowed


def test_unknown_goal_stays_insufficient_and_cannot_get_a_food_reference():
    result = analyze_recipe(catalog()[971], Constraints(health_goals=["未知调养目标"]))
    assert result.goal_matches[0].status == "insufficient_data"
    assert not result.goal_matches[0].reasons


def test_incidental_plant_food_stays_disclosed_without_rank_or_main_claim():
    source = dish("鸡肉玉米肠", "鸡胸肉200克；胡萝卜15克；玉米20克", "鸡肉蒸熟装盘。")
    text = "".join(analyze_recipe(source, Constraints(health_goals=["护心"])).goal_matches[0].reasons)
    assert "其他已声明食材：胡萝卜" in text
    assert "不凭辅料给整道菜加健康分" in text
    assert "菜品主体" not in text
