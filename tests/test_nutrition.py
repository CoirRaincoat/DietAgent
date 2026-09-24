import json

import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from app.nutrition.models import MenuNutrition, RecipeNutrition
from app.nutrition.qualitative import analyze, analyze_menu, analyze_recipe


def recipe(names, *, recipe_id="r1", categories=None, **kwargs):
    return Recipe(
        recipe_id=recipe_id, name="营养标签不可作为证据", source_row=12, fingerprint=recipe_id,
        raw_ingredients="、".join(names),
        ingredients=[Ingredient(name=name, raw=name) for name in names],
        steps="将所有食材煮熟。", categories=categories or [], **kwargs,
    )


def test_sources_are_actual_ingredients_and_traceable():
    sample = recipe(["鸡胸肉", "糙米", "橄榄油", "西兰花"], categories=["vegetable"])
    result = analyze_recipe(sample, Constraints(health_goals=["增肌", "控糖"]))
    assert result.protein_sources == ["鸡胸肉"]
    assert result.carbohydrate_sources == ["糙米"]
    assert result.fat_sources == ["橄榄油"]
    assert result.dietary_fiber == ["糙米", "西兰花"]
    for contribution in result.ingredient_contributions:
        assert contribution.ingredient_name in [item.name for item in sample.ingredients]
        assert contribution.recipe_id == sample.recipe_id
        assert contribution.source_row == sample.source_row
        assert "未推算营养含量" in contribution.explanation
    assert all(goal.status == "preference_match" for goal in result.goal_matches)
    assert all(goal.sources[0].url.startswith("https://www.who.int/") for goal in result.goal_matches)
    assert RecipeNutrition.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("ingredient", [
    "酱油", "蒸鱼豉油", "牛肉高汤", "鱼露", "鸡精", "蚝油", "鸡腿菇", "蟹味菇",
    "鸡头米", "鱼腥草", "肉豆蔻", "肉桂", "蛋黄酱", "牛油果",
])
def test_false_protein_friends_and_titles_never_create_protein(ingredient):
    sample = recipe([ingredient], categories=["protein"], raw_label="高蛋白降糖抗癌")
    sample.name = "鸡蛋鱼肉大米套餐"
    result = analyze_recipe(sample, Constraints(health_goals=["增肌"]))
    assert result.protein_sources == []
    assert "高蛋白降糖抗癌" not in result.model_dump_json()
    assert result.goal_matches[0].status == "insufficient_data"


@pytest.mark.parametrize("ingredient", [
    "玉米淀粉", "土豆淀粉", "红薯粉条", "番茄酱", "胡萝卜汁", "花生油", "燕麦奶",
])
def test_processed_starch_oil_and_unknown_drinks_do_not_imply_fiber(ingredient):
    result = analyze_recipe(recipe([ingredient]), Constraints())
    assert result.dietary_fiber == []


def test_health_match_retains_contradictory_evidence_and_sources():
    result = analyze_recipe(recipe(["燕麦", "白砂糖", "生抽"], categories=["vegetable"]),
                            Constraints(health_goals=["控糖", "降压", "神奇目标"]))
    goals = {goal.goal: goal for goal in result.goal_matches}
    assert goals["控糖"].status == "caution"
    assert "燕麦" in goals["控糖"].ingredient_names
    assert "白砂糖" in goals["控糖"].ingredient_names
    assert goals["降压"].status == "caution"
    assert goals["神奇目标"].status == "insufficient_data"
    assert goals["神奇目标"].sources == []
    assert "不能判断" in goals["控糖"].limitation
    assert {risk.code for risk in result.risks} >= {"quantity_incomplete", "added_sugar_source", "sodium_source"}


def test_composite_missing_quantities_and_constraint_conflict_are_explicit():
    result = analyze_recipe(recipe(["鸡蛋", "调味料", "沙拉酱"]), Constraints(allergies=["鸡蛋"]))
    assert {risk.code for risk in result.risks} >= {
        "quantity_incomplete", "composite_unknown", "constraint_conflict", "allergen_scope",
    }
    assert result.suitable_reasons == []
    assert all(risk.recipe_ids == ["r1"] for risk in result.risks)
    assert "调味料" in next(risk for risk in result.risks if risk.code == "composite_unknown").ingredient_names


def test_known_raw_quantities_do_not_turn_into_nutrient_estimates():
    sample = recipe(["鸡蛋", "大米"])
    sample.ingredients = [Ingredient(name="鸡蛋", raw="鸡蛋100g", quantity=100, unit="g"),
                          Ingredient(name="大米", raw="大米200g", quantity=200, unit="g")]
    result = analyze_recipe(sample, Constraints())
    assert "quantity_incomplete" not in {risk.code for risk in result.risks}
    assert all(item.quantity_recorded for item in result.ingredient_contributions)
    serialized = result.model_dump_json()
    assert "100" not in serialized and "200" not in serialized
    assert "不计算精确营养量" in serialized
    assert "kcal" not in serialized and "热量值" not in serialized


def test_meal_merges_real_sources_and_preserves_each_recipe_provenance():
    first = recipe(["鸡蛋", "大米"], recipe_id="a")
    second = recipe(["鸡蛋", "青菜", "花生油"], recipe_id="b", categories=["vegetable"])
    result = analyze_menu([first, second, first], Constraints(health_goals=["增肌"]))
    assert result.recipe_ids == ["a", "b"]
    assert result.protein_sources == ["鸡蛋"]
    egg_contributions = [item for item in result.ingredient_contributions if item.ingredient_name == "鸡蛋"]
    assert [item.recipe_id for item in egg_contributions] == ["a", "b"]
    assert len(result.recipe_analyses) == 2
    assert result.goal_matches[0].status == "preference_match"
    assert MenuNutrition.model_validate(json.loads(result.model_dump_json())) == result


def test_empty_menu_and_unrecognized_sources_remain_insufficient():
    empty = analyze_menu([], Constraints(health_goals=["增肌"]))
    assert empty.protein_sources == [] and empty.suitable_reasons == []
    assert [risk.code for risk in empty.risks] == ["empty_menu"]
    assert empty.goal_matches[0].status == "insufficient_data"
    unknown = analyze_menu([recipe(["未知原料"])], Constraints())
    assert "unidentified_sources" in {risk.code for risk in unknown.risks}
    assert unknown.ingredient_contributions == []


def test_legacy_sentence_interface_is_unchanged():
    notes = analyze(recipe(["鸡蛋", "豆腐"]), Constraints())
    assert notes[0] == "配料含蛋白质来源：鸡蛋、豆腐；未计算蛋白质含量。"

def test_nutrition_tool_supports_legacy_notes_dish_and_meal_structures():
    from app.tools.nutrition_analysis import NutritionAnalysisTool
    tool = NutritionAnalysisTool()
    sample = recipe(["鸡胸肉", "糙米"])
    constraints = Constraints()
    assert tool(recipe=sample, constraints=constraints) == analyze(sample, constraints)
    assert isinstance(tool(recipe=sample, constraints=constraints, detailed=True), RecipeNutrition)
    assert isinstance(tool(recipes=[sample], constraints=constraints), MenuNutrition)
    with pytest.raises(ValueError):
        tool(recipe=sample, recipes=[sample], constraints=constraints)
