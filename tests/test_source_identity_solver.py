"""Isolated assigned-score contracts, NOT proof of real menu quality."""

import pytest

pytest.importorskip("ortools", reason="solver remains an isolated offline dependency")

from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleDecision, RuleEngine
from evaluation.whole_menu_solver import solve_whole_menu


class AssignedRules(RuleEngine):
    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        return (3 if recipe.recipe_id.startswith("new") else 2,)

    def evaluate(self, recipe: Recipe, constraints: Constraints) -> RuleDecision:
        result = super().evaluate(recipe, constraints)
        if result.allowed:
            result.score = float(self.soft_goal_scores(recipe, constraints)[0])
        return result


def dish(key: str, name: str, food: str) -> Recipe:
    return Recipe(
        recipe_id=key,
        name=name,
        raw_ingredients=food,
        ingredients=[Ingredient(name=food, raw=food)],
        steps="将食材蒸熟后装盘。",
        categories=["protein"],
        meal_types=["晚餐"],
        raw_label="晚餐",
        source_row=1,
        fingerprint=key,
    )


@pytest.mark.parametrize(
    "name,food", [("清蒸鲈鱼", "鲈鱼"), ("蒸虾仁", "虾仁"), ("牛腩", "牛腩"), ("鸭腿", "鸭腿")]
)
def test_explicit_identity_repairs_candidate_gate_without_changing_legacy(
    name: str, food: str
) -> None:
    seed = [dish("old", "白切鸡", "鸡肉")]
    new = dish("new", name, food)
    constraints, rules = Constraints(dish_count=1, health_goals=["护心"]), AssignedRules()
    legacy = solve_whole_menu(seed, [*seed, new], constraints, rules)
    fixed = solve_whole_menu(
        seed,
        [*seed, new],
        constraints,
        rules,
        food_identity_policy="shared_source_v1",
        verify_baseline=True,
    )
    shared = solve_whole_menu(
        seed, [*seed, new], constraints, rules, food_identity_policy="shared_source_v1"
    )
    replay = solve_whole_menu(
        seed, [*seed, new], constraints, rules, food_identity_policy="shared_source_v1"
    )
    assert legacy.recipes == seed and legacy.status == "no_improvement_in_guarded_model_kept_seed"
    assert fixed.recipes == seed and fixed.status == "validated_seed_feasible"
    assert shared.recipes == [new] and shared.status == "validated_improvement"
    assert shared.validation_failures == [] and replay.recipes == shared.recipes
    assert shared.goal_sums_before == (2,) and shared.goal_sums_after == (3,)
    assert shared.model_scope["food_identity_policy"] == "shared_source_v1"


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy", "excluded", "vegan"])
def test_higher_assigned_score_cannot_bypass_hard_restrictions(restriction: str) -> None:
    seed = [dish("old", "蒸豆腐", "豆腐")]
    new = dish("new", "清蒸鲈鱼", "鲈鱼")
    constraints = Constraints(dish_count=1, health_goals=["护心"])
    if restriction == "no_spicy":
        constraints.no_spicy = True
        new.ingredients.append(Ingredient(name="辣椒", raw="辣椒"))
        new.raw_ingredients += "、辣椒"
    elif restriction == "allergy":
        constraints.allergies = ["鱼"]
    elif restriction == "excluded":
        constraints.excluded_ingredients = ["鲈鱼"]
    else:
        constraints.diet_mode = "vegan"
    result = solve_whole_menu(
        seed, [*seed, new], constraints, AssignedRules(), food_identity_policy="shared_source_v1"
    )
    assert result.recipes == seed and result.validation_failures == []


def test_local_edit_freezes_other_slots_and_preserves_source_records() -> None:
    seed = [dish("old-1", "鸡肉", "鸡肉"), dish("old-2", "蒸豆腐", "豆腐")]
    new = dish("new", "鲈鱼", "鲈鱼")
    pool = [*seed, new]
    before = [recipe.model_dump() for recipe in pool]
    result = solve_whole_menu(
        seed,
        pool,
        Constraints(dish_count=2, health_goals=["护心"]),
        AssignedRules(),
        food_identity_policy="shared_source_v1",
        replace_slot=1,
    )
    assert result.status == "validated_improvement" and result.changed_slots == [1]
    assert result.recipes[1] == seed[1]
    assert [recipe.model_dump() for recipe in pool] == before


def test_unknown_preparation_and_generic_animal_never_buy_improvement() -> None:
    seed = [dish("old", "鸡肉", "鸡肉")]
    new = dish("new", "鲈鱼", "鲈鱼")
    new.steps = "按提示完成。"
    unknown = dish("new-unknown", "蒸肉丸", "肉末")
    result = solve_whole_menu(
        seed,
        [*seed, new, unknown],
        Constraints(dish_count=1, health_goals=["护心"]),
        AssignedRules(),
        food_identity_policy="shared_source_v1",
    )
    assert result.recipes == seed and result.model_scope["peer_counts"] == [1]


def test_no_health_goal_does_not_perturb_menu_for_new_observation() -> None:
    seed = [dish("old", "鸡肉", "鸡肉")]
    new = dish("new", "鲈鱼", "鲈鱼")
    result = solve_whole_menu(
        seed,
        [*seed, new],
        Constraints(dish_count=1),
        AssignedRules(),
        food_identity_policy="shared_source_v1",
    )
    assert result.recipes == seed and result.status == "no_configured_health_goal_kept_unchanged"
