"""Source-shaped ablation contracts; assigned vectors are NOT clinical scores."""

from collections import Counter

import pytest

pytest.importorskip("ortools", reason="solver remains an isolated offline dependency")

from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Constraints, Ingredient, Recipe, ScopedMethod
from app.rules.engine import RuleDecision, RuleEngine
from evaluation.whole_menu_guard import prepare_menu_problem, validate_menu_proposal
from evaluation.whole_menu_solver import diagnose_no_improvement, solve_whole_menu


class AssignedRules(RuleEngine):
    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return (3 if recipe.recipe_id.startswith("new-") else 2,)

    def evaluate(self, recipe: Recipe, constraints: Constraints) -> RuleDecision:
        result = super().evaluate(recipe, constraints)
        if result.allowed:
            result.score = float(self.soft_goal_scores(recipe, constraints)[0])
        return result


def fixtures() -> tuple[list[Recipe], list[Recipe], Constraints, AssignedRules]:
    source = []
    for prefix, methods in (
        ("old", ("蒸", "煮", "炒", "烤", "煎", "炖")),
        ("new", ("蒸", "蒸", "煮", "煮", "炒", "炒")),
    ):
        for index, (food, method) in enumerate(
            zip(("白菜", "冬瓜", "菠菜", "西兰花", "番茄", "胡萝卜"), methods)
        ):
            source.append(
                Recipe(
                    recipe_id=f"{prefix}-{index}",
                    name=f"{method}{food}{prefix}",
                    raw_ingredients=food,
                    ingredients=[Ingredient(name=food, raw=food)],
                    steps=f"{food}{method}熟装盘。",
                    categories=["vegetable"],
                    meal_types=["晚餐"],
                    raw_label="晚餐",
                    source_row=index + 1,
                    fingerprint=f"{prefix}-{index}",
                )
            )
    return source[:6], source, Constraints(dish_count=6, health_goals=["护心"]), AssignedRules()


def test_explicit_ablation_can_trade_incidental_methods_not_source_knowledge() -> None:
    seed, pool, constraints, rules = fixtures()
    strict = solve_whole_menu(seed, pool, constraints, rules)
    capped = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    counts = Counter(method for recipe in capped.recipes for method in main_cooking_methods(recipe))
    assert capped.status == "validated_improvement" and capped.validation_failures == []
    assert capped.goal_sums_before == (12,) and capped.goal_sums_after == (18,)
    assert sorted(counts.values()) == [2, 2, 2]
    assert (
        len({method for recipe in strict.recipes for method in main_cooking_methods(recipe)}) == 6
    )
    assert strict.goal_sums_after < capped.goal_sums_after
    old_problem, _ = prepare_menu_problem(seed, pool, constraints, rules)
    assert old_problem is not None
    assert "source_method_coverage_or_concentration" in validate_menu_proposal(
        old_problem, capped.recipes
    )
    assert "whole_menu_balance" in validate_menu_proposal(old_problem, capped.recipes)


@pytest.mark.parametrize("preference", ["做法：炖", "做法：煎", "做法：烤"])
def test_specific_methods_cannot_be_traded_for_higher_reference_score(preference: str) -> None:
    seed, pool, constraints, rules = fixtures()
    constraints.preferences = [preference]
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert preference.split("：")[1] in {m for r in result.recipes for m in main_cooking_methods(r)}
    assert result.validation_failures == []
    assert result.goal_sums_after < (18,)


def test_explicit_diversity_keeps_existing_target_and_cannot_collapse_to_steam() -> None:
    seed, pool, constraints, rules = fixtures()
    constraints.preferences = ["做法多样"]
    for recipe in pool[6:]:
        food = recipe.ingredients[0].name
        recipe.name = f"蒸{food}new"
        recipe.steps = f"{food}蒸熟装盘。"
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    methods = Counter(m for r in result.recipes for m in main_cooking_methods(r))
    assert len(methods) >= 3 and max(methods.values()) <= 2
    assert result.goal_sums_after < (18,)


def test_unknown_source_action_never_purchases_health_improvement() -> None:
    seed, pool, constraints, rules = fixtures()
    for recipe in pool[6:]:
        recipe.steps = "按设备提示完成。"
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert result.recipes == seed and result.status == "no_improvement_in_guarded_model_kept_seed"


@pytest.mark.parametrize("slot", [1, 2, 5])
def test_local_authority_stays_one_slot_under_capped_method_policy(slot: int) -> None:
    seed, pool, constraints, rules = fixtures()
    result = solve_whole_menu(
        seed, pool, constraints, rules, method_guard_policy="capped_balance", replace_slot=slot
    )
    assert set(result.changed_slots) <= {slot}
    assert all(result.recipes[i] == recipe for i, recipe in enumerate(seed) if i != slot - 1)


def test_scoped_food_method_does_not_get_reassigned_to_another_slot() -> None:
    seed, pool, constraints, rules = fixtures()
    constraints.scoped_methods = [ScopedMethod(food="冬瓜", method="煮", slot=2, required=True)]
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert result.recipes[1].recipe_id == "old-1"
    assert result.validation_failures == []


def test_new_policy_baseline_feasibility_and_explanation_are_not_quality_labels() -> None:
    seed, pool, constraints, rules = fixtures()
    result = solve_whole_menu(
        seed, pool, constraints, rules, method_guard_policy="capped_balance", verify_baseline=True
    )
    assert result.status == "validated_seed_feasible" and result.recipes == seed
    diagnostic = diagnose_no_improvement(
        seed, pool, constraints, rules, method_guard_policy="capped_balance"
    )
    assert diagnostic["status"] == "guarded_strict_improvement_feasible_no_menu_published"
    assert diagnostic["guards_removed"] == [] and "recipes" not in diagnostic


@pytest.mark.parametrize("preference", ["不要做法多样", "做法：不要炒", "保留全部六种做法"])
def test_unknown_negative_or_more_specific_language_is_not_ignored(preference: str) -> None:
    seed, pool, constraints, rules = fixtures()
    constraints.preferences = [preference]
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert result.status == "unsupported_request_kept_unchanged" and result.recipes == seed


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy", "excluded", "vegan"])
def test_capped_mode_cannot_purchase_hard_restriction_violation(restriction: str) -> None:
    seed, pool, constraints, rules = fixtures()
    food = (
        "辣椒"
        if restriction == "no_spicy"
        else "花生" if restriction in {"allergy", "excluded"} else "猪肉"
    )
    for recipe in pool[6:]:
        recipe.ingredients.append(Ingredient(name=food, raw=food))
        recipe.raw_ingredients += "、" + food
    if restriction == "no_spicy":
        constraints.no_spicy = True
    elif restriction == "allergy":
        constraints.allergies = [food]
    elif restriction == "excluded":
        constraints.excluded_ingredients = [food]
    else:
        constraints.diet_mode = "vegan"
    result = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert result.recipes == seed and result.changed_slots == []


def test_source_and_requests_unchanged_and_capped_replay_stable() -> None:
    seed, pool, constraints, rules = fixtures()
    source = [r.model_dump() for r in pool]
    request_snapshot = constraints.model_dump()
    first = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    second = solve_whole_menu(seed, pool, constraints, rules, method_guard_policy="capped_balance")
    assert first.recipes == second.recipes and first.goal_sums_after == second.goal_sums_after
    assert [r.model_dump() for r in pool] == source and constraints.model_dump() == request_snapshot
