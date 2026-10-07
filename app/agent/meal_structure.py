"""Contextual culinary-role targets and bounded, monotone menu repair.

These are explainable planning defaults, not dietary doses or an explicit
meat/vegetarian count. Protein entrées can be plant-based. Hard rules must be
screened by the caller before candidates reach this module.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.cooking_methods import main_cooking_methods
from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.entree_preferences import entree_reference_mask, entree_request_state
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_names import (
    declared_protein_foods,
    named_declared_protein_foods,
)
from app.domain.protein_food_references import named_protein_foods

STRUCTURE_VERSION = "contextual-meal-roles-v2"
ENTREE_ROLES = ("vegetable", "protein", "staple")


def minimum_role_counts(
    total_count: int, constraints: Constraints | None = None
) -> dict[str, int]:
    """Return culinary minima that fit the known non-soup slots.

    Args:
        total_count: Total dish slots, including soups.
        constraints: Actual meal context; None keeps the older context-free
            default for independent callers that have not supplied soup counts.

    Returns:
        Minima for vegetables, protein entrées and staples. Shared lunch/dinner
        for at least three people with at least five non-soup slots uses two
        protein entrées. Smaller menus and other meals do not inherit that rule.
        A two-slot breakfast instead uses a staple and protein entrée as a
        planning default, unless explicit entrée counts occupy both slots.
        This is not a serving or nutrition requirement.

    Raises:
        ValueError: If the total count is negative.
    """
    if total_count < 0:
        raise ValueError("total_count must not be negative")
    slots = max(0, total_count - constraints.soup_count - constraints.dessert_count) if constraints else total_count
    shared_main_meal = (
        constraints is not None
        and constraints.people >= 3
        and constraints.meal_type in {"午餐", "晚餐"}
        and slots >= 5
    )
    explicit_entrees = (
        ((constraints.meat_dish_count or 0) + (constraints.vegetarian_dish_count or 0))
        if constraints
        else 0
    )
    if (
        constraints is not None
        and constraints.meal_type == "早餐"
        and slots == 2
        and explicit_entrees < slots
    ):
        return {"vegetable": 0, "protein": 1, "staple": 1}
    return {
        "vegetable": 2 if slots >= 4 else int(slots >= 2),
        "protein": 2 if shared_main_meal else int(slots >= 2),
        "staple": int(slots >= 3 and explicit_entrees < slots),
    }


def role_counts(menu: Sequence[Recipe]) -> dict[str, int]:
    """Count primary role metadata; this never counts ingredient nutrients."""
    counts = Counter(role for recipe in menu for role in set(recipe.categories))
    return {role: counts[role] for role in ENTREE_ROLES}


def remaining_role_gaps(
    menu: Sequence[Recipe], constraints: Constraints
) -> dict[str, int]:
    """Expose unmet engineering defaults without asserting missing nutrition."""
    counts = role_counts(menu)
    targets = minimum_role_counts(constraints.dish_count, constraints)
    return {
        role: gap
        for role, target in targets.items()
        if (gap := max(0, target - counts[role]))
    }


@dataclass(frozen=True)
class RoleRepair:
    """Menu, actual zero-based changes and residual, non-hard role gaps."""

    recipes: list[Recipe]
    changed_indices: frozenset[int]
    gaps: dict[str, int]


def repair_menu_roles(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    original: Sequence[Recipe],
    scores: Mapping[str, float],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> RoleRepair:
    """Repair role deficits while preserving scope, soups and covered preferences.

    Args:
        menu: Complete, unique, hard-validated provisional menu.
        safe_candidates: Hard-screened, meal-eligible, deduplicated candidates.
        constraints: Requested context and ingredient preferences.
        original: Previous menu used to prefer reusing already changed slots.
        scores: Existing health/preference ordering, not invented nutrition scores.
        order: Retrieval ordering for deterministic ties.
        food_matches: Caller-owned canonical ingredient matcher.
        replace_slot: An explicit local edit; suppresses extra role repair.
        allow_repair: False reports default gaps without changing a retained
            menu on an unrequested, read-only continuation.

    Returns:
        A bounded local improvement, never a global optimum claim. Each accepted
        swap strictly increases capped role coverage, cannot lose a covered role
        minimum or food preference, and leaves all soup positions unchanged.
        A fulfilled retry is a no-op. Residual gaps remain visible even when the
        catalog or a protected edit prevents repair.
    """
    working = list(menu)
    gaps = remaining_role_gaps(working, constraints)
    if not gaps or replace_slot is not None or not allow_repair:
        return RoleRepair(working, frozenset(), gaps)
    targets = minimum_role_counts(constraints.dish_count, constraints)
    terms = tuple(
        dict.fromkeys(
            term for term in constraints.preferred_ingredients if term.strip()
        )
    )

    def preference_mask(recipe: Recipe) -> int:
        return sum(1 << i for i, term in enumerate(terms) if food_matches(recipe, term))

    masks = {r.recipe_id: preference_mask(r) for r in [*working, *safe_candidates]}

    def coverage_mask(recipes: Sequence[Recipe]) -> int:
        value = 0
        for recipe in recipes:
            value |= masks[recipe.recipe_id]
        return value

    changed: set[int] = set()
    # Capped coverage increases by at least one; at most sum(targets) swaps.
    for _ in range(sum(targets.values())):
        counts = role_counts(working)
        capped = {role: min(counts[role], targets[role]) for role in ENTREE_ROLES}
        used = {r.recipe_id for r in working}
        covered_preferences = coverage_mask(working)
        best: tuple[tuple[float, ...], int, Recipe] | None = None
        for index, old in enumerate(working):
            if "soup" in old.categories:
                continue
            rest_preferences = coverage_mask(working[:index] + working[index + 1 :])
            old_roles = set(old.categories)
            new_change = int(
                index < len(original) and original[index].recipe_id == old.recipe_id
            )
            for candidate in safe_candidates:
                if candidate.recipe_id in used or "soup" in candidate.categories:
                    continue
                if not method_swap_preserves(
                    working,
                    index,
                    candidate,
                    constraints.preferences,
                    scoped=constraints.scoped_methods,
                ):
                    continue
                if not composition_satisfied(
                    [*working[:index], candidate, *working[index + 1 :]],
                    constraints,
                    previous=working,
                ):
                    continue
                next_preferences = rest_preferences | masks[candidate.recipe_id]
                if next_preferences & covered_preferences != covered_preferences:
                    continue
                next_caps = {
                    role: min(
                        counts[role]
                        - int(role in old_roles)
                        + int(role in candidate.categories),
                        targets[role],
                    )
                    for role in ENTREE_ROLES
                }
                if any(next_caps[role] < capped[role] for role in ENTREE_ROLES):
                    continue
                gain = sum(next_caps.values()) - sum(capped.values())
                if gain <= 0:
                    continue
                rank = (
                    -float(gain),
                    float(new_change),
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
        if not remaining_role_gaps(working, constraints):
            break
    return RoleRepair(
        working, frozenset(changed), remaining_role_gaps(working, constraints)
    )


def repair_shared_soup_staple(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    scores: Mapping[str, float],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    query_terms: Sequence[str] = (),
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> RoleRepair:
    """Prefer finished rice over extra porridge in a shared meal WITH soup.

    A bounded final culinary adjustment, not a new health score or ban on
    porridge. Explicit porridge/food/method/count requirements and edit scope
    prevail. Only an existing hard-screened same-role source recipe may replace
    a porridge slot. No inferred portion, therapeutic benefit or source edits.
    Incidental method variety and engineering health proxies may decrease;
    requested references, meal fit and covered flavor/scene evidence cannot.
    """
    working = list(menu)
    if (
        not allow_repair
        or constraints.people < 3
        or constraints.meal_type not in {"午餐", "晚餐"}
        or constraints.soup_count < 1
        or any(
            "粥" in term
            for term in (
                *query_terms,
                *constraints.preferences,
                *constraints.preferred_ingredients,
            )
        )
    ):
        return RoleRepair(
            working, frozenset(), remaining_role_gaps(working, constraints)
        )
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    terms = tuple(dict.fromkeys((*constraints.preferred_ingredients, *query_terms)))
    changed: set[int] = set()
    for index in positions:
        old = working[index]
        # Known source name AND grain ingredients/boiling action: no generic
        # "soft food" blacklist, and egg custard is not automatically removed.
        if (
            set(old.categories) != {"staple"}
            or not old.name.strip().endswith("粥")
            or "煮" not in main_cooking_methods(old)
        ):
            continue
        rest = working[:index] + working[index + 1 :]
        used = {r.recipe_id for r in working}
        names = {"".join(r.name.split()).casefold() for r in rest}
        covered = [t for t in terms if any(food_matches(r, t) for r in working)]
        old_flavors = 0
        rest_flavors = 0
        for r in working:
            old_flavors |= flavor_coverage(r, constraints.preferences)
        for r in rest:
            rest_flavors |= flavor_coverage(r, constraints.preferences)
        old_scenes = scene_reference_mask(old, scenes)
        options = []
        for r in safe_candidates:
            # Finite rice-side evidence from declared grain and source cooking,
            # not a title-only claim that any "饭" is a complete rice dish.
            if (
                r.recipe_id in used
                or "".join(r.name.split()).casefold() in names
                or set(r.categories) != {"staple"}
                or not r.name.strip().endswith("饭")
                or not any(
                    i.name in {"米", "大米", "小米", "糙米", "燕麦", "糯米"}
                    for i in r.ingredients
                )
                or not set(main_cooking_methods(r)).intersection({"蒸", "煮", "焖"})
                or meal_cost(r, constraints.meal_type)
                > meal_cost(old, constraints.meal_type)
                or any(not any(food_matches(x, t) for x in (*rest, r)) for t in covered)
                or (rest_flavors | flavor_coverage(r, constraints.preferences))
                & old_flavors
                != old_flavors
                or scene_reference_mask(r, scenes) & old_scenes != old_scenes
                or not composition_satisfied([*rest, r], constraints, previous=working)
                or not method_swap_preserves(
                    working,
                    index,
                    r,
                    constraints.preferences,
                    scoped=constraints.scoped_methods,
                )
            ):
                continue
            options.append(r)
        if options:
            working[index] = min(
                options,
                key=lambda r: (
                    meal_cost(r, constraints.meal_type),
                    -scores.get(r.recipe_id, 0),
                    order.get(r.recipe_id, len(order)),
                    r.recipe_id,
                ),
            )
            changed.add(index)
    return RoleRepair(
        working, frozenset(changed), remaining_role_gaps(working, constraints)
    )


def repair_shared_soup_entree(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    scores: Mapping[str, float],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    query_terms: Sequence[str] = (),
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> RoleRepair:
    """Reduce unrequested egg-custard/soup overlap in a large shared meal.

    Egg custard remains a valid protein dish, not a soup or unsafe food. This
    context-specific, same-role soft adjustment preserves requests and scope,
    and never forces a meat quota or rewrites an incomplete source program.
    Candidates must already be hard-screened/meal-eligible by the caller.
    Source cooking can establish an alternative, but is NOT a hard requirement:
    an explicitly shaped meat body followed by source device cooking can also
    establish a plated alternative, without claiming its temperature/time.
    """
    working = list(menu)
    requested = (
        *query_terms,
        *constraints.preferences,
        *constraints.preferred_ingredients,
    )
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    if (
        not allow_repair
        or constraints.people < 3
        or constraints.meal_type not in {"午餐", "晚餐"}
        or constraints.soup_count < 1
        or constraints.dish_count - constraints.soup_count < 5
        or any(
            term in value
            for value in requested
            for term in ("蒸蛋", "蛋羹", "蒸水蛋", "温泉蛋")
        )
    ):
        return RoleRepair(
            working, frozenset(), remaining_role_gaps(working, constraints)
        )

    def custard(recipe: Recipe) -> bool:
        return (
            set(recipe.categories) == {"protein"}
            and any(
                term in recipe.name for term in ("蒸蛋", "蛋羹", "蒸水蛋", "温泉蛋")
            )
            and any(
                "鸡蛋" in declared_protein_foods(i.name) for i in recipe.ingredients
            )
        )

    def plated_body(recipe: Recipe) -> bool:
        if custard(recipe) or any(
            t in recipe.name for t in ("泥", "糊", "冻", "布丁", "温泉蛋")
        ):
            return False
        named = named_declared_protein_foods(
            recipe.name, tuple(i.name for i in recipe.ingredients)
        )
        if not (entree_reference_mask(recipe) & 1 or "豆腐" in named):
            # A seasoning or plain egg is not a second solid-body reference.
            return False
        shape = re.search(
            r"肉[末沫馅][^。；;\n]{0,40}压实|整理成[^。；;\n]{0,20}(?:肉饼|圆饼)",
            recipe.steps,
        )
        shaped_meat = (
            shape is not None
            and not any(
                term in shape.group()
                for term in ("不要", "不用", "无需", "不压", "未压", "避免")
            )
            and "开始烹饪" in recipe.steps
        )
        return bool(main_cooking_methods(recipe) or shaped_meat)

    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    terms = tuple(dict.fromkeys(requested))
    # Preserve an explicit mixed request, but do not turn the internal shared
    # meal default into a hard veto: it can otherwise keep the only egg custard
    # merely to retain a no-meat reference. Diet mode and exact meat/vegetarian
    # quotas still pass through hard screening and composition_satisfied below.
    protect_mixed_reference = entree_request_state(constraints.preferences)[0]
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    changed: set[int] = set()
    for index in positions:
        old = working[index]
        if not custard(old):
            continue
        rest = working[:index] + working[index + 1 :]
        used = {r.recipe_id for r in working}
        names = {"".join(r.name.split()).casefold() for r in rest}
        covered = [t for t in terms if any(food_matches(r, t) for r in working)]
        flavor_before = 0
        flavor_rest = 0
        mixed_before = 0
        mixed_rest = 0
        for r in working:
            flavor_before |= flavor_coverage(r, constraints.preferences)
            mixed_before |= entree_reference_mask(r)
        for r in rest:
            flavor_rest |= flavor_coverage(r, constraints.preferences)
            mixed_rest |= entree_reference_mask(r)
        options = []
        for candidate in safe_candidates:
            if (
                candidate.recipe_id in used
                or "".join(candidate.name.split()).casefold() in names
                or set(candidate.categories) != set(old.categories)
                or not plated_body(candidate)
                or meal_cost(candidate, constraints.meal_type)
                > meal_cost(old, constraints.meal_type)
                or any(
                    not any(food_matches(r, t) for r in (*rest, candidate))
                    for t in covered
                )
                or (flavor_rest | flavor_coverage(candidate, constraints.preferences))
                & flavor_before
                != flavor_before
                or scene_reference_mask(candidate, scenes)
                & scene_reference_mask(old, scenes)
                != scene_reference_mask(old, scenes)
                or protect_mixed_reference
                and (mixed_rest | entree_reference_mask(candidate)) & mixed_before
                != mixed_before
                or not composition_satisfied(
                    [*rest, candidate],
                    constraints,
                    previous=working if protect_mixed_reference else None,
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
            options.append(candidate)
        if options:
            # Reuse the finite named-body evidence already used by the heart
            # replacement pass. A second tofu/fish reference need not outrank a
            # distinct plated body solely for repeating its ingredient credit.
            # Covered explicit food/method/diet/quota requirements were checked
            # above; this is a shared-meal tie preference, not a nutrient claim.
            rest_bodies = frozenset().union(*(named_protein_foods(r) for r in rest))
            working[index] = min(
                options,
                key=lambda r: (
                    meal_cost(r, constraints.meal_type),
                    len(named_protein_foods(r) & rest_bodies),
                    -scores.get(r.recipe_id, 0),
                    order.get(r.recipe_id, len(order)),
                    r.recipe_id,
                ),
            )
            changed.add(index)
    return RoleRepair(
        working, frozenset(changed), remaining_role_gaps(working, constraints)
    )
