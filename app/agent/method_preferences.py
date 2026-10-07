"""Bounded repair of explicit source-method references inside verified slots."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import no_goal_regression
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import (
    method_reference_mask,
    method_requests,
    method_spread,
    method_swap_preserves,
    supported_method_preferences,
)
from app.domain.models import Constraints, Recipe
from app.rules.engine import compact


@dataclass(frozen=True)
class MethodRepair:
    recipes: list[Recipe]
    changed_positions: frozenset[int]


def repair_method_preferences(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    goal_scores: Mapping[str, tuple[int, ...]],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> MethodRepair:
    """Improve requested method coverage/spread, never inferred default spread.

    Caller supplies hard-screened, meal-eligible candidates. Source data remain
    untouched. Covered foods/flavors, each slot's scenes, roles, known meal fit,
    explicit counts and each goal are retained. Methods are finite source
    references, not proof that every dish uses a requested technique. Negative
    requests/unknowns are disclosed separately, not upgraded to safety rules.
    """
    working = list(menu)
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    requests = method_requests(constraints.preferences)
    requested = supported_method_preferences(constraints.preferences)
    diversity = requests.diversity and not requests.negative_diversity
    if not allow_repair or not working or not (requested or diversity):
        return MethodRepair(working, frozenset())
    records: dict[str, Recipe] = {}
    for record in [*working, *candidates]:
        if record.recipe_id in records and records[record.recipe_id] != record:
            raise ValueError("One recipe identity cannot refer to different source records")
        records[record.recipe_id] = record
    method_masks = {
        key: method_reference_mask(record, constraints.preferences)
        for key, record in records.items()
    }
    flavor_masks = {
        key: flavor_coverage(record, constraints.preferences) for key, record in records.items()
    }
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {key: scene_reference_mask(record, scenes) for key, record in records.items()}
    terms = tuple(dict.fromkeys(t for t in constraints.preferred_ingredients if t.strip()))
    food_masks = {
        key: sum(1 << i for i, term in enumerate(terms) if food_matches(record, term))
        for key, record in records.items()
    }

    def coverage(values: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        mask = 0
        for record in values:
            mask |= masks[record.recipe_id]
        return mask

    def potential(values: Sequence[Recipe]) -> tuple[int, ...]:
        matched = coverage(values, method_masks).bit_count()
        known, distinct, maximum, pairs = method_spread(values) if diversity else (0, 0, 0, 0)
        return matched, known, distinct, -maximum, -pairs

    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    required_goals = len(dict.fromkeys(constraints.health_goals))
    changed: set[int] = set()
    # Each accepted swap improves at least one integer coordinate; explicit
    # method coverage/known/distinct cannot decrease, max/pairs cannot increase.
    # Their total possible improvement bounds the iterations, without a timer.
    limit = len(requested) + (
        3 * len(working) + len(working) * (len(working) - 1) // 2 if diversity else 0
    )
    for _ in range(limit):
        before = potential(working)
        before_food, before_flavor = coverage(working, food_masks), coverage(working, flavor_masks)
        used = {r.recipe_id for r in working}
        best: tuple[tuple[int, int, int, int, int, int, int, int, int, str], int, Recipe] | None = None
        for index in positions:
            old = working[index]
            previous_goals = goal_scores.get(old.recipe_id)
            if previous_goals is None or len(previous_goals) < required_goals:
                continue
            rest = working[:index] + working[index + 1 :]
            rest_names = {compact(record.name) for record in rest}
            rest_food, rest_flavor = coverage(rest, food_masks), coverage(rest, flavor_masks)
            for candidate in candidates:
                new_goals = goal_scores.get(candidate.recipe_id)
                if (
                    candidate.recipe_id in used
                    or compact(candidate.name) in rest_names
                    or set(candidate.categories) != set(old.categories)
                    or new_goals is None
                    or len(new_goals) < required_goals
                    or not no_goal_regression(new_goals, previous_goals)
                    or (
                        meal_cost(candidate, constraints.meal_type)
                        > meal_cost(old, constraints.meal_type)
                        and not (
                            constraints.method_meal_priority == "method"
                            and (method_masks[candidate.recipe_id]
                                 | coverage(rest, method_masks)).bit_count()
                            > before[0]
                        )
                    )
                    or (rest_food | food_masks[candidate.recipe_id]) & before_food != before_food
                    or (rest_flavor | flavor_masks[candidate.recipe_id]) & before_flavor
                    != before_flavor
                    or scene_masks[candidate.recipe_id] & scene_masks[old.recipe_id]
                    != scene_masks[old.recipe_id]
                    or not composition_satisfied([*rest, candidate], constraints, previous=working)
                    or not method_swap_preserves(working, index, candidate, constraints.preferences, scoped=constraints.scoped_methods)
                ):
                    continue
                proposal = [*working[:index], candidate, *working[index + 1 :]]
                after = potential(proposal)
                if after <= before:
                    continue
                rank = (
                    -after[0],
                    -after[1],
                    -after[2],
                    -after[3],
                    -after[4],
                    sum(meal_cost(r, constraints.meal_type) for r in proposal),
                    int(index not in changed),
                    order.get(candidate.recipe_id, len(order)),
                    index,
                    candidate.recipe_id,
                )
                if best is None or rank < best[0]:
                    best = rank, index, candidate
        if best is None:
            break
        _, index, candidate = best
        working[index] = candidate
        changed.add(index)
    return MethodRepair(working, frozenset(changed))
