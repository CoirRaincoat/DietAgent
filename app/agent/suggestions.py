"""Choose safe, varied same-role alternatives for a menu slot."""

import hashlib
from collections.abc import Sequence

from app.agent.menu_balance import balance_rank
from app.domain.context_exclusions import context_exclusion_hits
from app.domain.cooking_methods import main_cooking_methods
from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import (
    flavor_coverage,
    flavor_exclusion_hits,
    scene_reference_mask,
    supported_flavor_preferences,
)
from app.domain.meal_context import infant_only_source, meal_cost
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_references import (
    protein_food_coverage,
    protein_food_mask,
    supported_protein_foods,
)
from app.rules.engine import RuleEngine, compact


def replacement_candidates(
    menu: Sequence[Recipe],
    safe_recipes: Sequence[Recipe],
    session_id: str,
    limit: int = 2,
    *,
    constraints: Constraints | None = None,
    rules: RuleEngine | None = None,
) -> list[Recipe]:
    """Rank slot-one alternatives by roles, source context, variety and stable ties.

    The session tie-break rotates equally suitable options across conversations
    while keeping retries within one conversation stable.
    Proposals never worsen known meal-label evidence for the target slot.
    """
    if not menu or limit <= 0:
        return []
    used_ids = {recipe.recipe_id for recipe in menu}
    used_names = {compact(recipe.name) for recipe in menu}
    target_categories = set(menu[0].categories)
    goal_rules = (rules or RuleEngine()) if constraints and (
        constraints.health_goals or constraints.preferred_ingredients or "清淡" in supported_flavor_preferences(constraints.preferences)
    ) else None
    target_scores = goal_rules.soft_goal_scores(menu[0], constraints) if goal_rules and constraints else ()
    flavor_target = flavor_coverage(menu[0], constraints.preferences) if constraints else 0
    flavor_rest = 0
    if constraints:
        for recipe in menu[1:]:
            flavor_rest |= flavor_coverage(recipe, constraints.preferences)
    protected_flavors = flavor_target & ~flavor_rest
    scenes = supported_scene_preferences(constraints.preferences, constraints.people) if constraints else ()
    protected_scenes = scene_reference_mask(menu[0], scenes) if scenes else 0
    protected_foods = []
    protein_foods = supported_protein_foods(
        (goal_rules or rules or RuleEngine()).canonical_food(t) for t in constraints.preferred_ingredients
    ) if constraints else ()
    protected_protein_foods = (
        protein_food_mask(menu[0], protein_foods) & ~protein_food_coverage(menu[1:], protein_foods)
    )
    if goal_rules and constraints:
        protected_foods = [term for term in dict.fromkeys(constraints.preferred_ingredients)
                           if goal_rules.preference_matches(menu[0], term)
                           and not any(goal_rules.preference_matches(r, term) for r in menu[1:])]
    pool: list[Recipe] = []
    seen_pool_names = set(used_names)
    # The caller has already sorted safe recipes by retrieval relevance. A
    # bounded pool keeps optional suggestions from delaying the first token.
    for recipe in safe_recipes:
        name = compact(recipe.name)
        if (
            not is_main_meal_recipe(recipe)
            or protein_food_mask(recipe, protein_foods) & protected_protein_foods != protected_protein_foods
            or constraints is not None and bool(flavor_exclusion_hits(recipe, constraints.preferences))
            or constraints is not None and bool(context_exclusion_hits(recipe, constraints))
            or infant_only_source(recipe)
            or constraints is not None and meal_cost(recipe, constraints.meal_type)
            > meal_cost(menu[0], constraints.meal_type)
            or recipe.recipe_id in used_ids
            or name in seen_pool_names
            or set(recipe.categories) != target_categories
            or constraints is not None and not composition_satisfied([recipe, *menu[1:]], constraints, previous=menu)
            or goal_rules is not None and constraints is not None and not no_goal_regression(
                goal_rules.soft_goal_scores(recipe, constraints), target_scores,
            )
            or goal_rules is not None and any(not goal_rules.preference_matches(recipe, term) for term in protected_foods)
            or constraints is not None and (
                flavor_coverage(recipe, constraints.preferences) & protected_flavors
            ) != protected_flavors
            or scenes and scene_reference_mask(recipe, scenes) & protected_scenes != protected_scenes
            or constraints is not None and not method_swap_preserves(menu, 0, recipe, constraints.preferences, scoped=constraints.scoped_methods)
        ):
            continue
        pool.append(recipe)
        seen_pool_names.add(name)
        if len(pool) == 64:
            break
    fit_by_id = {
        recipe.recipe_id: balance_rank([recipe, *menu[1:]], len(menu), constraints)
        for recipe in pool
    }
    tie_by_id = {
        recipe.recipe_id: hashlib.sha256(
            f"{session_id}:{recipe.recipe_id}".encode("utf-8")
        ).hexdigest()
        for recipe in pool
    }
    options: list[Recipe] = []
    seen_names = set(used_names)
    while pool and len(options) < limit:
        chosen_methods = {method for recipe in options for method in main_cooking_methods(recipe)}

        def rank(recipe: Recipe) -> tuple[tuple[int, ...], int, int, int, str]:
            novelty = len(set(main_cooking_methods(recipe)) - chosen_methods)
            context = -meal_cost(recipe, constraints.meal_type) if constraints else 0
            scene_fit = scene_reference_mask(recipe, scenes).bit_count() if scenes else 0
            return fit_by_id[recipe.recipe_id], context, scene_fit, novelty, tie_by_id[recipe.recipe_id]

        candidate = max(
            (recipe for recipe in pool if compact(recipe.name) not in seen_names),
            key=rank,
            default=None,
        )
        if candidate is None:
            break
        options.append(candidate)
        seen_names.add(compact(candidate.name))
        pool.remove(candidate)
    return options
