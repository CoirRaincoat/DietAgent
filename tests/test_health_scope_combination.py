"""Real configured reference contracts, not clinical benefit or menu quality."""

import pytest

pytest.importorskip("ortools", reason="offline solver remains isolated")

from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine
from evaluation.whole_menu_solver import solve_whole_menu


def dish(key: str, name: str, foods: list[str], role: str = "protein") -> Recipe:
    return Recipe(
        recipe_id=key,
        name=name,
        raw_ingredients="、".join(foods),
        ingredients=[Ingredient(name=food, raw=food) for food in foods],
        steps="将食材蒸熟后装盘。",
        categories=[role],
        meal_types=["晚餐"],
        raw_label="晚餐",
        source_row=1,
        fingerprint=key,
    )


def fixtures() -> tuple[list[Recipe], list[Recipe], Constraints]:
    seed = [dish("old", "鸡肉玉米卷", ["鸡肉", "胡萝卜"])]
    new = dish("new", "蒸豆腐", ["豆腐"])
    return seed, [*seed, new], Constraints(dish_count=1, health_goals=["降压", "护心"])


def test_joint_scope_uses_actual_configured_references_not_garnish_points() -> None:
    seed, pool, constraints = fixtures()
    legacy, scoped = RuleEngine(), RuleEngine(experiment_category_scope=True)
    assert legacy.soft_goal_scores(seed[0], constraints) == (2, 2)
    assert scoped.soft_goal_scores(seed[0], constraints) == (0, 0)
    assert legacy.goal_evidence(seed[0], "降压").category_foods == ("胡萝卜",)
    assert scoped.goal_evidence(seed[0], "降压").category_foods == ("胡萝卜",)
    assert scoped.soft_goal_scores(pool[1], constraints) == (0, 2)
    old = solve_whole_menu(seed, pool, constraints, legacy, food_identity_policy="shared_source_v1")
    new = solve_whole_menu(seed, pool, constraints, scoped, food_identity_policy="shared_source_v1")
    assert old.recipes == seed
    assert new.status == "validated_improvement" and new.recipes == [pool[1]]
    assert new.validation_failures == []
    assert new.goal_sums_before == (0, 0) and new.goal_sums_after == (0, 2)
    assert not RuleEngine().experiment_category_scope


@pytest.mark.parametrize("restriction", ["soy_allergy", "no_spicy", "excluded", "meat_quota"])
def test_correcting_cross_role_points_never_overrides_explicit_constraints(
    restriction: str,
) -> None:
    seed, pool, constraints = fixtures()
    if restriction == "soy_allergy":
        constraints.allergies = ["豆类"]
    elif restriction == "no_spicy":
        constraints.no_spicy = True
        pool[1].ingredients.append(Ingredient(name="辣椒", raw="辣椒"))
        pool[1].raw_ingredients += "、辣椒"
    elif restriction == "excluded":
        constraints.excluded_ingredients = ["豆腐"]
    else:
        constraints.meat_dish_count = 1
    result = solve_whole_menu(
        seed,
        pool,
        constraints,
        RuleEngine(experiment_category_scope=True),
        food_identity_policy="shared_source_v1",
    )
    assert result.recipes == seed and result.validation_failures == []


def test_local_scope_freezes_protein_and_repeat_is_deterministic() -> None:
    seed, pool, constraints = fixtures()
    vegetable = dish("veg", "蒸白菜", ["白菜"], "vegetable")
    seed.append(vegetable)
    pool.append(vegetable)
    constraints.dish_count = 2
    rules = RuleEngine(experiment_category_scope=True)
    local = solve_whole_menu(
        seed, pool, constraints, rules, replace_slot=2, food_identity_policy="shared_source_v1"
    )
    assert local.recipes == seed
    full = solve_whole_menu(seed, pool, constraints, rules, food_identity_policy="shared_source_v1")
    replay = solve_whole_menu(
        seed, pool, constraints, rules, food_identity_policy="shared_source_v1"
    )
    assert full.recipes == replay.recipes and full.changed_slots == [1]
    assert full.recipes[1] == vegetable
