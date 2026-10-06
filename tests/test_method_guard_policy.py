"""Pure method-policy bounds, not human judgments about a menu."""

from collections import Counter

import pytest

from evaluation.method_guard_policy import method_limits


def test_strict_defaults_retain_every_distinct_method_and_frequency() -> None:
    before = Counter({"蒸": 1, "煮": 1, "炒": 1, "烤": 1, "煎": 1, "炖": 1})
    limits = method_limits(before, 6, 6, "strict_baseline")
    assert limits.minimum_distinct == 6
    assert limits.maximum_frequency == dict(before) and limits.new_method_frequency == 1
    assert limits.known_dishes == 6 and limits.maximum_pair_repetition == 0
    assert limits.balance_prefix_width is None


def test_capped_policy_retains_existing_breadth_tier_not_exact_seed_frequency() -> None:
    before = Counter({"蒸": 1, "煮": 1, "炒": 1, "烤": 1, "煎": 1, "炖": 1})
    limits = method_limits(before, 6, 6, "capped_balance")
    assert limits.minimum_distinct == 3 and limits.known_dishes == 6
    assert limits.new_method_frequency == 2 and set(limits.maximum_frequency.values()) == {2}
    assert limits.maximum_pair_repetition == 3 and limits.maximum_actions == 6
    assert limits.balance_prefix_width == 11


@pytest.mark.parametrize(
    "counts,known,size",
    [
        ({"蒸": 1}, 1, 1),
        ({"蒸": 3}, 3, 3),
        ({"蒸": 3, "煮": 1}, 4, 4),
        ({"蒸": 5, "煮": 1}, 6, 6),
        ({}, 0, 6),
    ],
)
def test_capped_bounds_never_make_baseline_infeasible(
    counts: dict[str, int], known: int, size: int
) -> None:
    limits = method_limits(Counter(counts), known, size, "capped_balance")
    assert limits.minimum_distinct <= len(counts)
    assert limits.known_dishes == known
    assert all(value <= limits.maximum_frequency[key] for key, value in counts.items())
    assert (
        sum(value * (value - 1) // 2 for value in counts.values()) <= limits.maximum_pair_repetition
    )
    assert sum(counts.values()) <= limits.maximum_actions


@pytest.mark.parametrize("known,size", [(-1, 3), (4, 3), (0, 0)])
def test_invalid_evidence_shape_is_refused(known: int, size: int) -> None:
    with pytest.raises(ValueError):
        method_limits(Counter(), known, size, "capped_balance")
