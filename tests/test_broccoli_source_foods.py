"""Known public broccoli omission; original complete formula, not new recipe."""
import pytest

from app.agent.planner import MenuPlanner
from app.domain.dish_composition import known_no_meat_food
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def test_original_broccoli_rice_is_not_unknown_meat_due_to_cooked_rice_or_pepper():
    source = catalog()[37]
    constraints = Constraints(dish_count=1, soup_count=0, diet_mode="vegan", no_spicy=True,
                              excluded_ingredients=["蒜"], preferred_ingredients=["西兰花"])
    assert source.name == "西兰花饭团"
    assert source.ingredients[0].quantity == 150
    assert source.ingredients[0].raw == "主料：西兰花150g（（切小朵））"
    result = MenuPlanner(RuleEngine()).plan([source], constraints)
    assert not result.failure and result.recipes == [source]


@pytest.mark.parametrize("name", ["熟米饭", "黑胡椒", "黑胡椒碎"])
def test_complete_supported_food_names(name):
    assert known_no_meat_food(name)


@pytest.mark.parametrize("name", ["熟米饭复合酱", "黑胡椒牛肉汁", "黑胡椒调味料", "熟米饭鸡汤"])
def test_compounds_are_not_certified_by_recognized_substrings(name):
    assert not known_no_meat_food(name)
    source = dish("西兰花饭", f"西兰花150克；{name}10克", "煮熟后装盘。")
    assert not RuleEngine().evaluate(source, Constraints(diet_mode="vegan")).allowed


@pytest.mark.parametrize("changes", [{"allergies": ["西兰花"]}, {"excluded_ingredients": ["西兰花"]},
                                     {"inventory": ["豆腐", "水"]}])
def test_source_recognition_does_not_relax_other_hard_requirements(changes):
    assert not RuleEngine().evaluate(catalog()[37], Constraints(diet_mode="vegan", **changes)).allowed


def test_broccoli_with_garlic_or_oyster_sauce_still_rejected():
    assert not RuleEngine().evaluate(catalog()[1167], Constraints(diet_mode="vegan", excluded_ingredients=["蒜"])).allowed
