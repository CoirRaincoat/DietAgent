"""Monotone bounded repairs of grounded food/slot finished-method coverage."""

from collections.abc import Callable, Mapping, Sequence

from app.agent.method_preferences import MethodRepair
from app.domain.dish_composition import composition_satisfied
from app.domain.entree_preferences import mixed_entree_preserves
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import scoped_method_coverage
from app.rules.engine import compact


def repair_scoped_methods(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_repair: bool = True,
) -> MethodRepair:
    """Safety-screened caller; retain quotas, roles, existing coverage and scope.

    Explicit scoped coverage precedes soft flavor/health ranking, not safety.
    No fabricated flavor or additional slots compensate a missing source method.
    Greedy single swaps are bounded; failure is not a proof of global no-solution.
    """
    working = list(menu)
    requests = constraints.scoped_methods
    changed: set[int] = set()
    if not allow_repair or not requests or not working:
        return MethodRepair(working, frozenset())
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    for _ in range(len(requests)):
        before = scoped_method_coverage(working, requests)
        covered_foods = [
            term
            for term in constraints.preferred_ingredients
            if any(food_matches(recipe, term) for recipe in working)
        ]
        used = {r.recipe_id for r in working}
        options = []
        for index in positions:
            old = working[index]
            for candidate in candidates:
                if candidate.recipe_id in used or set(candidate.categories) != set(old.categories):
                    continue
                proposed = [*working[:index], candidate, *working[index + 1 :]]
                after = scoped_method_coverage(proposed, requests)
                if (
                    after & before != before
                    or after.bit_count() <= before.bit_count()
                    or len({compact(r.name) for r in proposed}) != len(proposed)
                    or not composition_satisfied(proposed, constraints, previous=working)
                    or not mixed_entree_preserves(working, proposed, constraints)
                    or not method_swap_preserves(
                        working,
                        index,
                        candidate,
                        constraints.preferences,
                        scoped=constraints.scoped_methods,
                    )
                    or any(
                        not any(food_matches(r, term) for r in proposed) for term in covered_foods
                    )
                ):
                    continue
                required = sum(
                    bool(after & (1 << i)) for i, request in enumerate(requests) if request.required
                )
                options.append(
                    (
                        (
                            -required,
                            -after.bit_count(),
                            order.get(candidate.recipe_id, len(order)),
                            index,
                            candidate.recipe_id,
                        ),
                        index,
                        candidate,
                    )
                )
        if not options:
            break
        _, index, candidate = min(options, key=lambda value: value[0])
        working[index] = candidate
        changed.add(index)
    return MethodRepair(working, frozenset(changed))
