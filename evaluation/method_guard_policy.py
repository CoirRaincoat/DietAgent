"""Explicit offline method ablation; not a new serving or safety convention.

The capped policy keeps the planner's existing min(3, dish_count) finishing
breadth tier, not every incidental seed method or its exact frequency. Named
method coverage, scoped requests and known-method dishes stay separate gates.
All frequency/quality trades must remain visible in the source review report.
"""

from collections import Counter
from dataclasses import dataclass
from math import ceil
from typing import Literal

MethodGuardPolicy = Literal["strict_baseline", "capped_balance"]


@dataclass(frozen=True)
class MethodLimits:
    policy: MethodGuardPolicy
    minimum_distinct: int
    known_dishes: int
    maximum_frequency: dict[str, int]
    new_method_frequency: int
    maximum_pair_repetition: int
    maximum_actions: int
    balance_prefix_width: int | None


def method_limits(
    old: Counter[str],
    known_dishes: int,
    dish_count: int,
    policy: MethodGuardPolicy,
) -> MethodLimits:
    if policy not in {"strict_baseline", "capped_balance"}:
        raise ValueError("unknown method_guard_policy")
    if dish_count < 1 or not 0 <= known_dishes <= dish_count:
        raise ValueError("invalid dish/method evidence counts")
    old_pairs = sum(count * (count - 1) // 2 for count in old.values())
    actions = max(dish_count, sum(old.values()))
    if policy == "strict_baseline":
        return MethodLimits(
            policy,
            len(old),
            known_dishes,
            {key: max(1, count) for key, count in old.items()},
            1,
            old_pairs,
            actions,
            None,
        )
    floor = min(len(old), min(3, dish_count))
    cap = max(max(old.values(), default=0), ceil(dish_count / max(1, floor)))
    groups, remainder = divmod(actions, cap)
    pairs = groups * cap * (cap - 1) // 2 + remainder * (remainder - 1) // 2
    return MethodLimits(
        policy,
        floor,
        known_dishes,
        {key: cap for key in old},
        cap,
        max(old_pairs, pairs),
        actions,
        11,
    )
