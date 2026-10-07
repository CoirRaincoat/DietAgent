"""Bounded mixed-entrée coverage before qualitative health-score adjustment."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.entree_preferences import entree_reference_mask, mixed_entree_active
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe


@dataclass(frozen=True)
class EntreeRepair:
    recipes: list[Recipe]
    changed_positions: frozenset[int]


def repair_entree_preferences(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> EntreeRepair:
    """Fill missing finite meat/no-meat references, not portions or exact quotas.

    This structure stage precedes health sorting, like culinary role repair.
    It may lower an engineering health proxy; it must not claim clinical
    equivalence. Subsequent swaps protect coverage. Every change adds a new
    reference bit and preserves covered foods/flavors, per-slot scenes/roles,
    known meal fit, explicit counts, requested methods and the authorized scope.
    Candidates must be hard-screened and meal-eligible by the caller.
    """
    working = list(menu)
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    if not allow_repair or not mixed_entree_active(constraints) or not working:
        return EntreeRepair(working, frozenset())
    records: dict[str, Recipe] = {}
    for recipe in [*working, *candidates]:
        if recipe.recipe_id in records and records[recipe.recipe_id] != recipe:
            raise ValueError("One recipe identity cannot refer to different source records")
        records[recipe.recipe_id] = recipe
    mixed = {key: entree_reference_mask(r) for key, r in records.items()}
    foods = tuple(dict.fromkeys(t for t in constraints.preferred_ingredients if t.strip()))
    food_masks = {
        key: sum(1 << i for i, t in enumerate(foods) if food_matches(r, t))
        for key, r in records.items()
    }
    flavors = {key: flavor_coverage(r, constraints.preferences) for key, r in records.items()}
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {key: scene_reference_mask(r, scenes) for key, r in records.items()}

    def cover(values: Sequence[Recipe], masks: Mapping[str, int]) -> int:
        result = 0
        for r in values:
            result |= masks[r.recipe_id]
        return result

    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    changed: set[int] = set()
    for _ in range(2):
        before = cover(working, mixed)
        if before == 3:
            break
        food_before, flavor_before = cover(working, food_masks), cover(working, flavors)
        used = {r.recipe_id for r in working}
        best: tuple[tuple[int, int, int, int, str], int, Recipe] | None = None
        for index in positions:
            old = working[index]
            rest = working[:index] + working[index + 1 :]
            other, other_food, other_flavor = (
                cover(rest, mixed),
                cover(rest, food_masks),
                cover(rest, flavors),
            )
            rest_names = {"".join(r.name.split()).casefold() for r in rest}
            for candidate in candidates:
                key = candidate.recipe_id
                after = other | mixed[key]
                if (
                    key in used
                    or "".join(candidate.name.split()).casefold() in rest_names
                    or set(candidate.categories) != set(old.categories)
                    or after == before
                    or after & before != before
                    or (other_food | food_masks[key]) & food_before != food_before
                    or (other_flavor | flavors[key]) & flavor_before != flavor_before
                    or scene_masks[key] & scene_masks[old.recipe_id] != scene_masks[old.recipe_id]
                    or meal_cost(candidate, constraints.meal_type)
                    > meal_cost(old, constraints.meal_type)
                    or not composition_satisfied([*rest, candidate], constraints, previous=working)
                    or not method_swap_preserves(working, index, candidate, constraints.preferences, scoped=constraints.scoped_methods)
                ):
                    continue
                rank = (
                    -after.bit_count(),
                    int(index not in changed),
                    order.get(key, len(order)),
                    index,
                    key,
                )
                if best is None or rank < best[0]:
                    best = rank, index, candidate
        if best is None:
            break
        _, index, candidate = best
        working[index] = candidate
        changed.add(index)
    return EntreeRepair(working, frozenset(changed))
