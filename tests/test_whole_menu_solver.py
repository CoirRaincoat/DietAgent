"""Public algebra/source contracts, not independent culinary or clinical labels."""

from itertools import product

import pytest

pytest.importorskip("ortools", reason="offline solver has a separate hashed environment")

from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleDecision, RuleEngine
from evaluation.whole_menu_guard import (
    goal_sums,
    peer_health_frontier,
    prepare_menu_problem,
    validate_menu_proposal,
)
from evaluation.whole_menu_solver import diagnose_no_improvement, solve_whole_menu


def dish(key: str, food: str) -> Recipe:
    return Recipe(
        recipe_id=key,
        name=f"蒸{food}{key}",
        raw_ingredients=food,
        ingredients=[Ingredient(name=food, raw=food)],
        steps=f"{food}蒸熟装盘。",
        categories=["vegetable"],
        meal_types=["晚餐"],
        raw_label="晚餐、清淡",
        labels=["清淡"],
        source_row=1,
        fingerprint=key,
    )


VECTORS = {
    "a": (2, 2, 2),
    "b": (2, 2, 2),
    "c": (2, 2, 2),
    "d": (0, 3, 4),
    "e": (4, 0, 3),
    "f": (3, 4, 0),
}


class AlgebraRules(RuleEngine):
    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return VECTORS[recipe.recipe_id]

    def evaluate(self, recipe: Recipe, constraints: Constraints) -> RuleDecision:
        result = super().evaluate(recipe, constraints)
        if result.allowed:
            result.score = float(sum(VECTORS[recipe.recipe_id]))
        return result


def records() -> list[Recipe]:
    return [
        dish(key, food)
        for key, food in zip("abcdef", ("白菜", "冬瓜", "菠菜", "西兰花", "番茄", "胡萝卜"))
    ]


def demand() -> Constraints:
    return Constraints(dish_count=3, health_goals=["降压", "护心", "控糖"])


def test_three_slot_compensation_is_found_without_sacrificing_any_goal() -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), time_limit_seconds=5)
    assert {r.recipe_id for r in result.recipes} == {"d", "e", "f"}
    assert result.goal_sums_before == (6, 6, 6) and result.goal_sums_after == (7, 7, 7)
    assert result.changed_slots == [1, 2, 3]
    assert result.validation_failures == []
    assert result.status == "validated_improvement"


@pytest.mark.parametrize("scope", [1, 2])
def test_bounded_scope_infeasibility_is_not_called_full_catalog_infeasibility(scope: int) -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), max_changed_slots=scope)
    assert result.recipes == pool[:3]
    assert result.solver_status == "INFEASIBLE"
    assert result.status == "no_improvement_in_guarded_model_kept_seed"


@pytest.mark.parametrize("slot", [1, 2, 3])
def test_local_edit_never_gets_whole_meal_permission(slot: int) -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), replace_slot=slot)
    assert result.recipes == pool[:3] and result.changed_slots == []
    assert result.model_scope["replace_slot"] == slot


def test_baseline_feasibility_is_verified_by_constraints_not_only_a_hint() -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), verify_baseline=True)
    assert result.status == "validated_seed_feasible"
    assert result.recipes == pool[:3] and result.solver_status == "OPTIMAL"


def test_model_matches_exhaustive_validator_on_small_problem() -> None:
    pool, constraints, rules = records(), demand(), AlgebraRules()
    problem, issue = prepare_menu_problem(pool[:3], pool, constraints, rules)
    assert problem is not None and issue is None
    accepted = []
    for keys in product(*problem.peers):
        proposed = [problem.records[key] for key in keys]
        if not validate_menu_proposal(problem, proposed):
            accepted.append(sum(goal_sums(proposed, problem.features)))
    result = solve_whole_menu(pool[:3], pool, constraints, rules)
    assert sum(result.goal_sums_after) == max(accepted)
    assert result.solver_status == "OPTIMAL"


@pytest.mark.parametrize(
    "mutation", ["spicy", "allergy", "salt", "breakfast", "unknown_method", "unknown_food", "soup"]
)
def test_global_search_cannot_purchase_safety_or_source_loss(mutation: str) -> None:
    pool = records()
    candidate = pool[3]
    if mutation in {"spicy", "allergy", "salt"}:
        token = {"spicy": "辣椒", "allergy": "花生", "salt": "盐"}[mutation]
        candidate.ingredients.append(Ingredient(name=token, raw=token))
        candidate.raw_ingredients += "、" + token
    elif mutation == "breakfast":
        candidate.meal_types = ["早餐"]
        candidate.raw_label = "早餐、清淡"
    elif mutation == "unknown_method":
        candidate.steps = "按设备提示。"
    elif mutation == "unknown_food":
        candidate.ingredients = [Ingredient(name="原料", raw="原料")]
        candidate.raw_ingredients = "原料"
    else:
        candidate.categories = ["soup"]
    constraints = demand().model_copy(update={"no_spicy": True, "allergies": ["花生"]})
    result = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert result.recipes == pool[:3] and result.status != "validated_improvement"


def test_losing_unique_preferred_food_is_rejected_even_with_health_gain() -> None:
    pool = records()
    constraints = demand().model_copy(update={"preferred_ingredients": ["冬瓜"]})
    assert solve_whole_menu(pool[:3], pool, constraints, AlgebraRules()).recipes == pool[:3]


@pytest.mark.parametrize("preference", ["不要烤", "早点做完", "清淡一点别太油"])
def test_unsupported_prose_stays_unsupported(preference: str) -> None:
    pool = records()
    constraints = demand().model_copy(update={"preferences": [preference]})
    result = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert result.status == "unsupported_request_kept_unchanged" and result.recipes == pool[:3]


def test_timeout_retains_seed_and_does_not_claim_no_solution_exists() -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), time_limit_seconds=1e-9)
    assert result.recipes == pool[:3]
    assert result.status == "time_budget_exhausted_kept_seed"


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_budgets_are_not_silently_coerced(limit: int) -> None:
    pool = records()
    with pytest.raises(ValueError):
        solve_whole_menu(pool[:3], pool, demand(), AlgebraRules(), time_limit_seconds=limit)


def test_input_records_and_constraints_are_not_mutated_and_replay_is_stable() -> None:
    pool, constraints = records(), demand()
    snapshot = [r.model_dump() for r in pool]
    original = constraints.model_dump()
    first = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    second = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert [r.recipe_id for r in first.recipes] == [r.recipe_id for r in second.recipes]
    assert [r.model_dump() for r in pool] == snapshot and constraints.model_dump() == original


def test_validator_refuses_modified_or_unbound_recipe_and_wrong_counts() -> None:
    pool = records()
    problem, _ = prepare_menu_problem(pool[:3], pool, demand(), AlgebraRules())
    assert problem is not None
    altered = pool[3].model_copy(update={"raw_ingredients": "虚构配方"})
    assert validate_menu_proposal(problem, [altered, pool[4], pool[5]]) == [
        "unbound_or_modified_source"
    ]
    assert validate_menu_proposal(problem, pool[:2]) == ["dish_count"]
    assert validate_menu_proposal(problem, [pool[0], pool[0], pool[1]]) == [
        "duplicate_identity_or_name"
    ]


def test_explicit_preference_coverage_is_preserved_and_not_newly_claimed() -> None:
    pool = records()
    for recipe in pool[3:]:
        recipe.raw_label = "晚餐"
        recipe.labels = []
    constraints = demand().model_copy(update={"preferences": ["清淡"]})
    result = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert result.recipes == pool[:3]


def test_no_health_request_has_no_permission_to_churn() -> None:
    pool = records()
    result = solve_whole_menu(pool[:3], pool, Constraints(dish_count=3), RuleEngine())
    assert result.status == "no_configured_health_goal_kept_unchanged"
    assert result.recipes == pool[:3]


def test_feasible_diagnostic_does_not_publish_or_relax_menu() -> None:
    pool = records()
    result = diagnose_no_improvement(pool[:3], pool, demand(), AlgebraRules())
    assert result["status"] == "guarded_strict_improvement_feasible_no_menu_published"
    assert result["guards_removed"] == []
    assert result["sufficient_assumption_subset_NOT_minimal"] == []
    assert "recipes" not in result


def test_infeasible_explanation_maps_only_asserted_group_names() -> None:
    pool = records()
    constraints = demand().model_copy(update={"preferred_ingredients": ["冬瓜"]})
    result = diagnose_no_improvement(pool[:3], pool, constraints, AlgebraRules())
    assert result["status"] == "guarded_strict_improvement_infeasible"
    assert result["guards_removed"] == []
    assert set(result["sufficient_assumption_subset_NOT_minimal"]) <= set(
        result["assumption_groups"]
    )
    assert "foods_coverage" in result["sufficient_assumption_subset_NOT_minimal"]


def test_explanation_timeout_is_not_an_infeasibility_proof() -> None:
    pool = records()
    result = diagnose_no_improvement(
        pool[:3], pool, demand(), AlgebraRules(), time_limit_seconds=1e-9
    )
    assert result["status"] == "time_budget_exhausted"
    assert result["solver_status"] is None
    assert result["sufficient_assumption_subset_NOT_minimal"] == []


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1])
def test_diagnostic_invalid_budget_rejected(budget: float) -> None:
    pool = records()
    with pytest.raises(ValueError):
        diagnose_no_improvement(pool[:3], pool, demand(), AlgebraRules(), time_limit_seconds=budget)


def test_exact_soup_and_non_meat_quotas_are_preserved_in_the_full_model() -> None:
    pool = records()
    pool[0].categories = pool[3].categories = ["soup"]
    constraints = demand().model_copy(
        update={
            "soup_count": 1,
            "vegetarian_dish_count": 2,
            "meat_dish_count": 0,
            "vegetarian_soup_count": 1,
            "meat_soup_count": 0,
        }
    )
    result = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert result.status == "validated_improvement"
    assert {r.recipe_id for r in result.recipes} == {"d", "e", "f"}
    assert result.changed_slots == [1, 2, 3]
    assert sum("soup" in r.categories for r in result.recipes) == 1
    assert result.validation_failures == []


def test_explicit_excluded_ingredient_is_not_bought_with_reference_points() -> None:
    pool = records()
    constraints = demand().model_copy(update={"excluded_ingredients": ["番茄"]})
    result = solve_whole_menu(pool[:3], pool, constraints, AlgebraRules())
    assert result.recipes == pool[:3] and result.changed_slots == []


def test_peer_bound_is_not_called_a_feasible_or_healthy_menu() -> None:
    pool = records()
    problem, _ = prepare_menu_problem(pool[:3], pool, demand(), AlgebraRules())
    assert problem is not None
    frontier = peer_health_frontier(problem)
    assert frontier["seed_reference_sum"] == 18
    assert frontier["additive_upper_bound_ignoring_cross_slot_guards"] == 21
    assert frontier["strict_improvement_impossible_even_before_cross_slot_guards"] is False
    assert all(
        w["single_proposal_violations_NOT_conflict_core"]
        for slot in frontier["slots"]
        for w in slot["higher_reference_witnesses_NOT_recommendations"]
    )


def test_single_authorized_slot_bound_excludes_frozen_other_slots() -> None:
    pool = records()
    problem, _ = prepare_menu_problem(pool[:3], pool, demand(), AlgebraRules(), replace_slot=1)
    assert problem is not None
    frontier = peer_health_frontier(problem, witnesses_per_slot=0)
    assert frontier["additive_upper_bound_ignoring_cross_slot_guards"] == 19
    assert [slot["peer_count"] for slot in frontier["slots"]] == [6, 1, 1]
    assert all(
        not slot["higher_reference_witnesses_NOT_recommendations"] for slot in frontier["slots"]
    )
