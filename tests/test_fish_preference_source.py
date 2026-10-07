"""Fish preference needs declared fish, not the spelling of a condiment."""
import pytest

from app.domain.models import Constraints
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.rules.engine import RuleEngine
from tests.test_protein_food_preferences import dish
from tests.test_recipe_generation import make_client, request_intent


@pytest.mark.parametrize("term", ["鱼", "鱼肉", "鱼类"])
@pytest.mark.parametrize("condiment", ["蒸鱼豉油", "鱼露", "鱼汤"])
def test_condiment_or_stock_alone_does_not_satisfy_positive_fish(term, condiment):
    recipe = dish("蒸青菜", f"青菜200克；{condiment}10克")
    rules = RuleEngine()
    assert not rules.preference_matches(recipe, term)


@pytest.mark.parametrize("food", ["鲈鱼", "黄鱼", "三文鱼", "鱼肉", "鲜鱼片"])
def test_declared_whole_fish_is_positive_preference_evidence(food):
    recipe = dish("蒸鱼", f"{food}200克；水100克")
    assert RuleEngine().preference_matches(recipe, "鱼")


def test_declared_fish_and_condiment_still_count_as_fish():
    recipe = dish("清蒸鲈鱼", "鲈鱼200克；蒸鱼豉油10克")
    assert RuleEngine().preference_matches(recipe, "鱼") == ["鲈鱼"]
    # Positive coverage must not change the independently conservative safety path.
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["鱼"])).allowed


def test_step_only_fish_does_not_acquire_declared_preference_proof():
    recipe = dish("家常菜", "青菜200克", "最后加入鱼片一起蒸熟装盘。")
    rules = RuleEngine()
    assert not rules.preference_matches(recipe, "鱼")
    assert not rules.evaluate(recipe, Constraints(allergies=["鱼"])).allowed


def test_same_actual_api_fish_greens_rice_request_covers_fish(tmp_path):
    catalog = load_runtime_catalog()
    intent = request_intent(diet_mode=None, preferred_ingredients=["鱼", "青菜", "米饭"],
                            query_terms=["嫩一点的鱼肉"])
    message = "2个人吃晚餐，3道菜不要汤，不辣，没有其他忌口，想吃嫩一点的鱼肉，再配青菜和米饭。"
    with make_client(tmp_path, catalog, [intent]) as client:
        response = client.post("/chat", json={"user_id": 900001, "message": message}).json()
    assert response["status"] == "ok"
    menu = [catalog.recipes[item["recipe_id"]] for item in response["menu"]]
    assert len(menu) == 3 and not any("soup" in recipe.categories for recipe in menu)
    assert any("鱼" in named_protein_foods(recipe) for recipe in menu)
    rules = RuleEngine()
    assert all(any(rules.preference_matches(recipe, food) for recipe in menu)
               for food in ("青菜", "米饭"))
    assert "蛋白菜名称参考：鱼" not in response["reason"]
