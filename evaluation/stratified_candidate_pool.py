"""Offline bounded sampling AFTER all existing slot eligibility gates.

This samples finite declaration signatures, not nutrients, dominant foods or
clinical suitability. It neither filters constraints nor certifies diversity.
Unknown-only declarations share one bucket; names cannot create new evidence.
"""

from collections import deque
from collections.abc import Mapping, Sequence

from app.domain.models import Recipe


def bound_declared_food_pool(
    ranked: Sequence[Recipe],
    old: Recipe,
    signatures: Mapping[str, frozenset[str]],
    limit: int,
) -> list[Recipe]:
    """Reserve the old slot, then round-robin declaration groups in rank order.

    Caller supplies a deduplicated, already hard/role/scene/goal-filtered pool.
    Preserve rank within each bucket and order buckets by their best member.
    No randomization, source mutation, cross-role relaxation or new features.
    Finite sampling can discard useful candidates; downstream joint guards and
    a same-budget flat-pool ablation are still necessary. Not serving code.
    """
    if limit < 1 or old.recipe_id not in {r.recipe_id for r in ranked}:
        raise ValueError("positive limit and eligible old record required")
    if len({r.recipe_id for r in ranked}) != len(ranked):
        raise ValueError("pool must have unique recipe identities")
    buckets: dict[frozenset[str], deque[Recipe]] = {}
    for recipe in ranked:
        if recipe.recipe_id == old.recipe_id:
            continue
        buckets.setdefault(signatures[recipe.recipe_id], deque()).append(recipe)
    selected = [old]
    active = deque(buckets.values())
    while active and len(selected) < limit:
        group = active.popleft()
        selected.append(group.popleft())
        if group:
            active.append(group)
    return selected
