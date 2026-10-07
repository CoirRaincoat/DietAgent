"""Source-bound final repair of actual recent-meal repetition, not a solver."""

from collections.abc import Callable, Mapping, Sequence

from app.agent.menu_variety import VarietyRepair
from app.domain.cooking_methods import main_cooking_methods
from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import HealthEvidence, no_goal_regression
from app.domain.heart_protein_reference import preferred_protein_body
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.meal_history import recommendation_counts
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.rules.engine import compact


def repair_next_meal_repetition(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    recent_recipe_names: Sequence[Sequence[str]],
    order: Mapping[str, int],
    relevance: Callable[[Recipe], float],
    goal_scores: Mapping[str, tuple[int, ...]],
    goal_evidence: Callable[[Recipe, str], HealthEvidence | None],
    food_matches: Callable[[Recipe, str], bool],
    protected_food_terms: Sequence[str] = (),
) -> VarietyRepair:
    """Swap only previously recommended slots of a new meal, at most once each.

    Caller owns explicit new-meal authority and supplies hard-screened, role-
    eligible, name-deduplicated source records. This pass runs after ordinary
    repairs so their proxy choices cannot silently restore a repeated dish.
    It protects roles, counts, meal fit, covered food/flavor/scene/method and
    goal references. Retrieval relevance and the unrequested preparation
    bonus are ranking preferences, not per-slot vetoes. Explicit cooking
    methods and whole-menu flavor coverage remain protected. History is
    recommendation exposure, never actual consumption.
    No source editing, new scoring weights, pool expansion or optimality claim.
    """
    working = list(menu)
    latest = (
        {compact(name) for name in recent_recipe_names[-1]}
        if recent_recipe_names
        else set()
    )
    exposures = recommendation_counts(recent_recipe_names)
    positions = [i for i, r in enumerate(working) if exposures[compact(r.name)] > 0]
    if not positions:
        return VarietyRepair(working, frozenset())
    records: dict[str, Recipe] = {}
    for record in [*working, *safe_candidates]:
        if record.recipe_id in records and records[record.recipe_id] != record:
            raise ValueError(
                "One recipe identity cannot refer to different source records"
            )
        records[record.recipe_id] = record
    terms = tuple(
        dict.fromkeys((*constraints.preferred_ingredients, *protected_food_terms))
    )
    food_masks = {
        key: sum(
            1 << i
            for i, term in enumerate(terms)
            if term.strip() and food_matches(r, term)
        )
        for key, r in records.items()
    }
    flavor_masks = {
        key: flavor_coverage(r, constraints.preferences) for key, r in records.items()
    }
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {
        key: scene_reference_mask(r, scenes) if scenes else 0
        for key, r in records.items()
    }
    methods = {key: main_cooking_methods(r) for key, r in records.items()}
    goals = tuple(dict.fromkeys(constraints.health_goals))

    def coverage(values: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        result = 0
        for record in values:
            result |= masks[record.recipe_id]
        return result

    def keeps_goal_references(old: Recipe, new: Recipe) -> bool:
        for goal in goals:
            previous, candidate = goal_evidence(old, goal), goal_evidence(new, goal)
            if previous is None:
                continue
            if candidate is None or (
                not set(previous.preferred_foods) <= set(candidate.preferred_foods)
                or not set(previous.good_methods) <= set(candidate.good_methods)
                or (
                    previous.category_rank_enabled
                    and previous.category_foods
                    and not (
                        candidate.category_rank_enabled and candidate.category_foods
                    )
                )
                or not set(
                    (
                        *candidate.discouraged_foods,
                        *candidate.raw_cautions,
                        *candidate.step_cautions,
                        *candidate.bad_methods,
                    )
                )
                <= set(
                    (
                        *previous.discouraged_foods,
                        *previous.raw_cautions,
                        *previous.step_cautions,
                        *previous.bad_methods,
                    )
                )
            ):
                return False
        return True

    changed: set[int] = set()
    for index in positions:
        old = working[index]
        rest = working[:index] + working[index + 1 :]
        used_names = {compact(r.name) for r in working}
        used_ids = {r.recipe_id for r in working}
        before_food, before_flavor = coverage(working, food_masks), coverage(
            working, flavor_masks
        )
        rest_food, rest_flavor = coverage(rest, food_masks), coverage(
            rest, flavor_masks
        )
        previous_scores = goal_scores.get(old.recipe_id)
        if previous_scores is None or len(previous_scores) < len(goals):
            continue
        peers = []
        for candidate in safe_candidates:
            new_scores = goal_scores.get(candidate.recipe_id)
            if (
                candidate.recipe_id in used_ids
                or compact(candidate.name) in used_names | latest
                or exposures[compact(candidate.name)] >= exposures[compact(old.name)]
                or set(candidate.categories) != set(old.categories)
                or not candidate.eligible
                or not candidate.raw_ingredients.strip()
                or not candidate.ingredients
                or not candidate.steps.strip()
                or (bool(old.fingerprint) and candidate.fingerprint == old.fingerprint)
                or (
                    old.ingredients
                    and compact(old.ingredients[0].name)
                    == compact(candidate.ingredients[0].name)
                    and set(methods[old.recipe_id]) == set(methods[candidate.recipe_id])
                )
                or (bool(methods[old.recipe_id]) and not methods[candidate.recipe_id])
                or meal_cost(candidate, constraints.meal_type)
                > meal_cost(old, constraints.meal_type)
                or new_scores is None
                or len(new_scores) < len(goals)
                # Only configured health goals occupy this prefix. A trailing
                # steam/boil bonus for 清淡 is not an explicit steam-only demand.
                # Preserve real flavor coverage below and explicit methods via
                # method_swap_preserves; relevance still ranks eligible peers.
                or not no_goal_regression(new_scores[:len(goals)], previous_scores[:len(goals)])
                or (
                    "护心" in goals
                    and preferred_protein_body(old)
                    and not preferred_protein_body(candidate)
                )
                or not keeps_goal_references(old, candidate)
                or (rest_food | food_masks[candidate.recipe_id]) & before_food
                != before_food
                or (rest_flavor | flavor_masks[candidate.recipe_id]) & before_flavor
                != before_flavor
                or scene_masks[candidate.recipe_id] & scene_masks[old.recipe_id]
                != scene_masks[old.recipe_id]
                or not composition_satisfied(
                    [*rest, candidate], constraints, previous=working
                )
                or not method_swap_preserves(
                    working,
                    index,
                    candidate,
                    constraints.preferences,
                    scoped=constraints.scoped_methods,
                )
            ):
                continue
            peers.append(candidate)
        if not peers:
            continue
        chosen = min(
            peers,
            key=lambda r: (
                exposures[compact(r.name)],
                -relevance(r),
                order.get(r.recipe_id, len(order)),
                r.recipe_id,
            ),
        )
        working[index] = chosen
        changed.add(index)
    return VarietyRepair(working, frozenset(changed))
