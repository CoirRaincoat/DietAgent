"""Bounded source-backed tradeoffs, not a health score or clinical validation."""

import pytest

from app.agent.health_preferences import (
    repair_health_preferences,
    source_caution_reduced,
)
from app.agent.planner import MenuPlanner
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, ScopedMethod
from app.rules.engine import RuleEngine
from tests.test_component_slots import dish


def fixtures():
    steamed = dish("蒸鸡蛋", "鸡蛋100克", "鸡蛋蒸熟后装盘。")
    baked = dish("烤南瓜饭", "大米100克；南瓜50克；盐2克", "米饭南瓜烤熟后装盘。")
    rice = dish("蒸南瓜饭", "大米100克；南瓜50克；水200克", "米饭南瓜蒸熟后装盘。")
    return [steamed, baked], rice


def repair(menu, candidate, constraints, **extra):
    rules = RuleEngine()
    pool = [*menu, candidate]
    scores = extra.pop(
        "scores", {r.recipe_id: rules.soft_goal_scores(r, constraints) for r in pool}
    )
    return repair_health_preferences(
        menu,
        pool,
        constraints,
        scores=scores,
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, term: bool(rules.food_matches(r, term)),
        allow_diversity_tradeoff=True,
        goal_evidence=rules.goal_evidence,
        **extra,
    )


def test_salt_presence_removal_cannot_trade_generic_spread_or_named_methods():
    menu, rice = fixtures()
    constraints = Constraints(
        dish_count=2, preferences=["做法多样"], health_goals=["降压"]
    )
    assert not method_swap_preserves(menu, 1, rice, constraints.preferences)
    result = repair(menu, rice, constraints)
    assert result.recipes == menu
    assert result.diversity_tradeoff_indices == frozenset()
    constraints.preferences.append("做法：烤")
    assert repair(menu, rice, constraints).recipes == menu


@pytest.mark.parametrize("protection", ["scope", "food", "query", "local"])
def test_tradeoff_preserves_scoped_methods_covered_food_and_local_slots(protection):
    menu, rice = fixtures()
    constraints = Constraints(
        dish_count=2, preferences=["做法多样"], health_goals=["降压"]
    )
    extra = {}
    if protection == "scope":
        constraints.scoped_methods = [ScopedMethod(food="南瓜", method="烤", slot=2)]
    elif protection == "food":
        constraints.preferred_ingredients = [
            "盐"
        ]  # Literal covered food, not a health claim.
    elif protection == "query":
        extra["protected_food_terms"] = ["盐"]
    else:
        extra["replace_slot"] = 1
    assert repair(menu, rice, constraints, **extra).recipes == menu


@pytest.mark.parametrize(
    "source", ["positive_only", "new_caution", "raw_salt", "step_salt"]
)
def test_added_positive_tokens_or_new_or_hidden_cautions_cannot_unlock_tradeoff(source):
    menu, rice = fixtures()
    rules = RuleEngine()
    if source == "positive_only":
        menu[1].raw_ingredients = "大米100克"
        menu[1].ingredients = [i for i in menu[1].ingredients if i.name != "盐"]
    elif source == "new_caution":
        rice = dish("蒸米饭", "大米100克；生抽2克", "米饭蒸熟后装盘。")
    elif source == "raw_salt":
        rice.raw_ingredients += "；盐适量"
    else:
        rice.steps += "可选加盐。"
    assert not source_caution_reduced(menu[1], rice, ["降压"], rules.goal_evidence)
    constraints = Constraints(
        dish_count=2, preferences=["做法多样"], health_goals=["降压"]
    )
    # Even an externally inflated vector cannot authorize this source tradeoff.
    scores = {menu[0].recipe_id: (0,), menu[1].recipe_id: (0,), rice.recipe_id: (9,)}
    assert repair(menu, rice, constraints, scores=scores).recipes == menu


def test_light_only_gain_does_not_waive_explicit_diversity():
    menu, rice = fixtures()
    constraints = Constraints(
        dish_count=2, preferences=["做法多样", "清淡"], health_goals=["降压"]
    )
    scores = {
        menu[0].recipe_id: (0, 0),
        menu[1].recipe_id: (0, 0),
        rice.recipe_id: (0, 1),
    }
    assert repair(menu, rice, constraints, scores=scores).recipes == menu


@pytest.mark.parametrize("priority", [None, "method"])
def test_default_planner_honors_explicit_method_priority(priority):
    menu, rice = fixtures()
    constraints = Constraints(
        dish_count=2,
        preferences=["做法多样"],
        health_goals=["降压"],
        method_meal_priority=priority,
    )
    result = MenuPlanner(RuleEngine()).plan([*menu, rice], constraints, current=menu)
    assert not result.failure
    assert result.recipes == menu
    assert not any("一般做法多样性取舍" in w for w in result.warnings)


@pytest.mark.parametrize("restriction", ["allergy", "spicy", "vegan"])
def test_default_planner_never_buys_soft_gain_by_violating_hard_restrictions(
    restriction,
):
    menu, rice = fixtures()
    constraints = Constraints(
        dish_count=2, preferences=["做法多样"], health_goals=["降压"]
    )
    if restriction == "allergy":
        rice = dish("蒸米饭", "大米100克；花生20克", "米饭花生蒸熟后装盘。")
        constraints.allergies = ["花生"]
    elif restriction == "spicy":
        rice = dish("蒸米饭", "大米100克；辣椒2克", "米饭辣椒蒸熟后装盘。")
        constraints.no_spicy = True
    else:
        rice = dish("蒸米饭", "大米100克；鸡肉20克", "米饭鸡肉蒸熟后装盘。")
        constraints.diet_mode = "vegan"
    result = MenuPlanner(RuleEngine()).plan([*menu, rice], constraints, current=menu)
    assert rice not in result.recipes
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)
