"""Same-role, bounded contextual repair after hard screening and role coverage."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.rules.engine import compact


@dataclass(frozen=True)
class ContextRepair:
    recipes: list[Recipe]
    changed_indices: frozenset[int]


def repair_meal_context(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    scores: Mapping[str, float],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    goal_scores: Mapping[str, tuple[int, ...]] | None = None,
    allow_repair: bool = True,
) -> ContextRepair:
    """Reduce context cost without losing covered foods, roles, counts or scope.

    Each slot improves at most three times (other -> unknown -> suggested -> matched). Exact
    categories preserve primary roles and soup positions. Caller owns safety
    and name deduplication; no search-optimality or clinical claim is made.
    """
    working = list(menu)
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    if not allow_repair:
        return ContextRepair(working, frozenset())
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    if not any(meal_cost(working[index], constraints.meal_type) for index in positions):
        return ContextRepair(working, frozenset())
    records: dict[str, Recipe] = {}
    for recipe in [*menu, *safe_candidates]:
        if recipe.recipe_id in records and records[recipe.recipe_id] != recipe:
            raise ValueError("One recipe identity cannot refer to different source records")
        records[recipe.recipe_id] = recipe
    goals = goal_scores or {}
    required_goals = len(dict.fromkeys(constraints.health_goals))
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {
        key: scene_reference_mask(r, scenes) if scenes else 0 for key, r in records.items()
    }
    flavor_masks = {
        key: flavor_coverage(r, constraints.preferences) if constraints.preferences else 0
        for key, r in records.items()
    }
    terms = tuple(dict.fromkeys(constraints.preferred_ingredients))
    masks = {
        r.recipe_id: sum(1 << i for i, t in enumerate(terms) if food_matches(r, t))
        for r in [*menu, *safe_candidates]
    }

    def coverage(records: Sequence[Recipe]) -> int:
        mask = 0
        for record in records:
            mask |= masks[record.recipe_id]
        return mask

    def flavors(records: Sequence[Recipe]) -> int:
        mask = 0
        for record in records:
            mask |= flavor_masks[record.recipe_id]
        return mask

    changed: set[int] = set()
    for _ in range(3 * len(working)):
        used = {r.recipe_id for r in working}
        covered = coverage(working)
        previous_flavors = flavors(working)
        best: tuple[tuple[float, ...], int, Recipe] | None = None
        for index in positions:
            old = working[index]
            previous_goals = goals.get(old.recipe_id, () if not required_goals else None)
            if previous_goals is None or len(previous_goals) < required_goals:
                continue
            cost = meal_cost(old, constraints.meal_type)
            if not cost:
                continue
            rest = working[:index] + working[index + 1 :]
            names = {compact(r.name) for r in rest}
            rest_flavors = flavors(rest)
            for candidate in safe_candidates:
                gain = cost - meal_cost(candidate, constraints.meal_type)
                next_goals = goals.get(candidate.recipe_id, () if not required_goals else None)
                if (
                    candidate.recipe_id in used
                    or compact(candidate.name) in names
                    or gain <= 0
                    or set(candidate.categories) != set(old.categories)
                    or next_goals is None
                    or len(next_goals) < required_goals
                    or not no_goal_regression(next_goals, previous_goals)
                    or scene_masks[candidate.recipe_id] & scene_masks[old.recipe_id]
                    != scene_masks[old.recipe_id]
                    or (rest_flavors | flavor_masks[candidate.recipe_id]) & previous_flavors
                    != previous_flavors
                ):
                    continue
                next_mask = coverage(rest) | masks[candidate.recipe_id]
                if next_mask & covered != covered:
                    continue
                if not composition_satisfied([*rest, candidate], constraints, previous=working):
                    continue
                if not method_swap_preserves(
                    working, index, candidate, constraints.preferences,
                    protect_requested=constraints.method_meal_priority != "meal",
                    scoped=constraints.scoped_methods,
                ):
                    continue
                rank = (
                    -float(gain),
                    -scores.get(candidate.recipe_id, 0.0),
                    float(order.get(candidate.recipe_id, len(order))),
                    float(index),
                )
                if best is None or rank < best[0]:
                    best = (rank, index, candidate)
        if best is None:
            break
        _, index, candidate = best
        working[index] = candidate
        changed.add(index)
    return ContextRepair(working, frozenset(changed))
