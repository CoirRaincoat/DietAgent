"""Real source bento alternatives: soft proxies are not safety vetoes."""
import pytest

from app.agent.next_meal_rotation import repair_next_meal_repetition
from app.domain.matching_tags import flavor_coverage
from app.domain.models import Constraints, ScopedMethod
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog
from tests.test_next_meal_final_rotation import dish


def rotate(menu, candidates, c):
    rules = RuleEngine()
    safe = [r for r in candidates if rules.evaluate(r, c).allowed]
    return repair_next_meal_repetition(
        menu, safe, c, recent_recipe_names=[[r.name for r in menu]],
        order={r.recipe_id: i for i, r in enumerate(safe)},
        relevance=lambda r: recipe_relevance_score(r, [], c, rules),
        goal_scores={r.recipe_id: rules.soft_goal_scores(r, c) for r in [*menu, *safe]},
        goal_evidence=rules.goal_evidence,
        food_matches=lambda r, term: bool(rules.preference_matches(r, term)),
    )


def constraints(**kwargs):
    return Constraints(people=1, dish_count=3, soup_count=0, meal_type="午餐",
                       preferences=["便当", "清淡"], no_spicy=True, **kwargs)


@pytest.mark.parametrize("source_row", [566, 1817])
def test_actual_bento_can_trade_unrequested_steam_bonus_for_next_meal_variety(source_row):
    old, rice, new = (catalog()[i] for i in [469, 299, source_row])
    veg = next(r for r in catalog().values() if r.name == "白灼芥蓝")
    rules, c = RuleEngine(), constraints()
    assert rules.evaluate(new, c).allowed
    assert rules.soft_goal_scores(new, c) < rules.soft_goal_scores(old, c)
    assert recipe_relevance_score(new, [], c, rules) < recipe_relevance_score(old, [], c, rules)
    before = [old, veg, rice]
    after = rotate(before, [new], c)
    assert after.recipes == [new, veg, rice] and after.changed_indices == {0}
    assert flavor_coverage(veg, c.preferences) | flavor_coverage(new, c.preferences)
    assert all(rules.evaluate(r, c).allowed for r in after.recipes)


def test_soft_tradeoff_does_not_remove_all_actual_light_flavor_coverage():
    old, shrimp = catalog()[469], catalog()[1817]
    c = constraints()
    assert flavor_coverage(shrimp, c.preferences) == 0
    assert rotate([old], [shrimp], c).recipes == [old]


def test_explicit_steam_method_still_blocks_fried_source():
    shrimp, rice = (catalog()[i] for i in [1817, 299])
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克", label="午餐、清淡")
    veg = next(r for r in catalog().values() if r.name == "白灼芥蓝")
    c = constraints()
    c.preferences = ["清淡"]
    c.scoped_methods = [ScopedMethod(food="鸡胸肉", method="蒸", slot=1)]
    assert rotate([old, veg, rice], [shrimp], c).recipes == [old, veg, rice]


@pytest.mark.parametrize("allergy", ["虾", "甲壳类"])
def test_real_shrimp_alternative_cannot_waive_allergy(allergy):
    old, shrimp, rice = (catalog()[i] for i in [469, 1817, 299])
    veg = next(r for r in catalog().values() if r.name == "白灼芥蓝")
    assert rotate([old, veg, rice], [shrimp], constraints(allergies=[allergy])).recipes == [old, veg, rice]


@pytest.mark.parametrize("candidate_seen", [False, True])
def test_recent_nonadjacent_repeat_changes_only_to_less_exposed_source(candidate_seen):
    old, fish, shrimp, rice = (catalog()[i] for i in [469, 150, 1817, 299])
    veg = next(r for r in catalog().values() if r.name == "白灼芥蓝")
    rules, c = RuleEngine(), constraints()
    history = [[old.name], [fish.name]]
    if candidate_seen:
        history[0].append(shrimp.name)
    before = [old, veg, rice]
    result = repair_next_meal_repetition(
        before, [shrimp], c, recent_recipe_names=history,
        order={shrimp.recipe_id: 0},
        relevance=lambda r: recipe_relevance_score(r, [], c, rules),
        goal_scores={r.recipe_id: rules.soft_goal_scores(r, c) for r in [*before, shrimp]},
        goal_evidence=rules.goal_evidence,
        food_matches=lambda r, term: bool(rules.preference_matches(r, term)),
    )
    assert result.recipes == (before if candidate_seen else [shrimp, veg, rice])
