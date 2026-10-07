"""Authored culinary-role ranking counterexamples, not medical or quality labels."""

from dataclasses import replace
from itertools import product

import pytest

from app.agent.planner import MenuPlanner
from app.domain.health_evidence import HealthRule, health_evidence
from app.domain.models import Constraints, Ingredient, Recipe
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine


def dish(key: str, foods: tuple[str, ...], roles: list[str]) -> Recipe:
    return Recipe(
        recipe_id=key,
        name=key,
        raw_ingredients="、".join(foods),
        ingredients=[Ingredient(name=f, raw=f) for f in foods],
        steps="将食材蒸熟后装盘。",
        categories=roles,
        meal_types=["晚餐"],
        methods=["蒸"],
        source_row=1,
        fingerprint=key,
    )


@pytest.mark.parametrize("role,food", [("protein", "鸡肉"), ("staple", "大米"), ("soup", "豆腐")])
@pytest.mark.parametrize("goal", ["控糖", "降压", "护心", "降尿酸"])
def test_vegetable_garnish_is_not_a_vegetable_entree_rank_bonus(
    role: str, food: str, goal: str
) -> None:
    rules = RuleEngine(experiment_category_scope=True)
    plain = dish("原菜", (food,), [role])
    garnish = dish("含胡萝卜", (food, "胡萝卜"), [role])
    before, after = rules.goal_evidence(plain, goal), rules.goal_evidence(garnish, goal)
    assert before is not None and after is not None
    assert after.category_foods == ("胡萝卜",)  # Actual source facts survive.
    assert after.score == before.score
    assert rules.evaluate(garnish, Constraints(health_goals=[goal])).allowed
    assert (
        "胡萝卜"
        in analyze_recipe(garnish, Constraints(health_goals=[goal]))
        .goal_matches[0]
        .ingredient_names
    )


@pytest.mark.parametrize(
    "roles", [[], ["unknown"], ["protein", "vegetable"], ["vegetable", "vegetable"]]
)
def test_unknown_or_nonexclusive_role_cannot_award_category_points(roles: list[str]) -> None:
    evidence = health_evidence(
        dish("白菜配方", ("白菜",), roles),
        HealthRule(prefer_categories=("vegetable",), scope_category_to_role=True),
    )
    assert evidence.category_foods == ("白菜",)
    assert evidence.score == 0


def test_exclusive_matching_role_and_actual_food_are_both_required() -> None:
    rule = HealthRule(prefer_categories=("vegetable",), scope_category_to_role=True)
    assert health_evidence(dish("蒸白菜", ("白菜",), ["vegetable"]), rule).score == 2
    assert health_evidence(dish("伪蔬菜标签", ("盐",), ["vegetable"]), rule).score == 0
    assert health_evidence(dish("伪蔬菜标签", ("鸡肉",), ["vegetable"]), rule).score == 0


def test_explicit_preferred_food_and_supported_methods_are_not_suppressed_with_category_bonus() -> (
    None
):
    rule = HealthRule(
        prefer_categories=("vegetable",),
        prefer_terms=("燕麦",),
        prefer_methods=("蒸",),
        scope_category_to_role=True,
    )
    record = dish("燕麦南瓜主食", ("燕麦", "南瓜"), ["staple"])
    evidence = health_evidence(record, rule)
    assert evidence.category_foods == ("南瓜",) and evidence.preferred_foods == ("燕麦",)
    assert evidence.good_methods == ("蒸",) and evidence.score == 3
    assert health_evidence(record, replace(rule, positive_roles=("protein",))).score == 0


@pytest.mark.parametrize("token", ["盐", "味增", "鸡粉", "白糖", "黄油", "蜂蜜"])
def test_nonmatching_role_keeps_every_caution_and_penalty(token: str) -> None:
    record = dish("鸡肉配胡萝卜", ("鸡肉", "胡萝卜", token), ["protein"])
    evidence = health_evidence(
        record,
        HealthRule(
            prefer_categories=("vegetable",), discourage_terms=(token,), scope_category_to_role=True
        ),
    )
    assert evidence.discouraged_foods == (token,) and evidence.has_caution
    assert evidence.score == -3


def test_role_source_rule_changes_recompute_scope_without_losing_food_facts() -> None:
    record = dish("鸡肉配胡萝卜", ("鸡肉", "胡萝卜"), ["protein"])
    vegetable = HealthRule(prefer_categories=("vegetable",), scope_category_to_role=True)
    assert health_evidence(record, vegetable).score == 0
    record.categories = ["vegetable"]
    assert health_evidence(record, vegetable).score == 0  # Cache alone is not source evidence.
    record.name = "蒸胡萝卜"
    record.ingredients = [Ingredient(name="胡萝卜", raw="胡萝卜")]
    record.raw_ingredients = "胡萝卜"
    assert health_evidence(record, vegetable).score == 2
    record.ingredients = [Ingredient(name="盐", raw="盐")]
    assert health_evidence(record, vegetable).score == 0
    protein = HealthRule(prefer_categories=("protein",), scope_category_to_role=True)
    record.name = "蒸鸡肉"
    record.ingredients = [Ingredient(name="鸡肉", raw="鸡肉")]
    record.raw_ingredients = "鸡肉"
    record.categories = ["protein"]
    assert health_evidence(record, protein).score == 2


@pytest.mark.parametrize(
    "goal,role", list(product(["降压", "护心", "控糖"], ["protein", "staple"]))
)
def test_new_goal_does_not_churn_equal_fit_nonvegetable_for_garnish(goal: str, role: str) -> None:
    food = "鸡肉" if role == "protein" else "大米"
    menu = [dish("原菜", (food,), [role])]
    alternative = dish("含胡萝卜", (food, "胡萝卜"), [role])
    result = MenuPlanner(RuleEngine(experiment_category_scope=True)).plan(
        [*menu, alternative], Constraints(dish_count=1, health_goals=[goal]), current=menu
    )
    assert not result.failure and result.recipes == menu


@pytest.mark.parametrize("forbidden,allergies", [("辣椒", []), ("虾", ["虾"])])
def test_real_matching_vegetable_category_never_overrides_hard_screening(
    forbidden: str, allergies: list[str]
) -> None:
    record = dish("蒸白菜", ("白菜", forbidden), ["vegetable"])
    assert (
        not RuleEngine(experiment_category_scope=True)
        .evaluate(record, Constraints(no_spicy=True, allergies=allergies, health_goals=["降压"]))
        .allowed
    )


def test_default_scopes_heart_bp_role_credit_without_enabling_offline_experiment() -> None:
    record = dish("鸡肉配胡萝卜", ("鸡肉", "胡萝卜"), ["protein"])
    ordinary = RuleEngine().goal_evidence(record, "降压")
    scoped = RuleEngine(experiment_category_scope=True).goal_evidence(record, "降压")
    assert ordinary is not None and scoped is not None
    assert ordinary.score == scoped.score == 0
    assert health_evidence(record, HealthRule(prefer_categories=("vegetable",))).score == 2
    assert ordinary.category_foods == scoped.category_foods == ("胡萝卜",)
    assert not RuleEngine().experiment_category_scope


@pytest.mark.parametrize("value", [False, None, "true", 1, [], {}])
def test_configuration_cannot_coerce_malformed_scope_activation(value: object) -> None:
    rule = HealthRule.from_mapping({"scope_category_to_role": value})
    assert not rule.scope_category_to_role
    assert HealthRule.from_mapping({"scope_category_to_role": True}).scope_category_to_role


def test_switching_explicit_scope_invalidates_cached_rank_not_source_facts() -> None:
    record = dish("鸡肉配胡萝卜", ("鸡肉", "胡萝卜"), ["protein"])
    rule = HealthRule(prefer_categories=("vegetable",))
    old, new = health_evidence(record, rule), health_evidence(
        record, replace(rule, scope_category_to_role=True)
    )
    assert old.category_foods == new.category_foods and old.score == 2 and new.score == 0
