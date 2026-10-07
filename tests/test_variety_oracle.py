"""Independent count-review fixtures, not blind human or holdout labels."""

from copy import deepcopy
from typing import Any

import pytest

from evaluation.variety_oracle import variety_nonregression_findings


def reviews(methods: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "reviewed_methods": methods,
        "reviewed_declared_families": {k: [] for k in methods},
        "reviewed_focus": {k: [] for k in methods},
    }


def menu(*keys: str) -> dict[str, Any]:
    return {"menu": [{"recipe_id": key} for key in keys]}


def test_unchanged_unknown_menu_is_nonregression_not_quality_gain() -> None:
    assert variety_nonregression_findings(menu("a"), menu("a"), reviews({"a": []})) == []


def test_new_distinct_method_can_reduce_repetition() -> None:
    expected = reviews({"a": ["蒸"], "b": ["蒸"], "c": ["煮"]})
    assert variety_nonregression_findings(menu("a", "b"), menu("a", "c"), expected) == []


def test_unknown_new_source_never_receives_zero_overlap_credit() -> None:
    findings = variety_nonregression_findings(menu("a"), menu("new"), reviews({"a": ["蒸"]}))
    assert len(findings) == 3
    assert all(row["type"] == "source_review_missing" for row in findings)


def test_known_to_unknown_cannot_claim_better_concentration() -> None:
    expected = reviews({"a": ["蒸"], "b": ["蒸"], "c": []})
    findings = variety_nonregression_findings(menu("a", "b"), menu("a", "c"), expected)
    assert [r["type"] for r in findings] == ["known_method_coverage_lost"]


def test_equal_aggregate_counts_still_fail_when_steam_repetition_increases() -> None:
    expected = reviews(
        {"a": ["蒸"], "b": ["蒸"], "c": ["煮"], "d": ["煮"], "e": ["煮"], "f": ["蒸"]}
    )
    findings = variety_nonregression_findings(
        menu("a", "b", "c", "d", "e"), menu("a", "b", "f", "d", "e"), expected
    )
    assert findings == [
        {"type": "individual_method_repetition_increased", "method": "蒸", "before": 2, "after": 3}
    ]


@pytest.mark.parametrize(
    "field", ["reviewed_focus", "reviewed_declared_families", "reviewed_methods"]
)
def test_each_review_checks_whole_menu_pairs(field: str) -> None:
    expected = reviews({"a": [], "b": [], "c": []})
    expected[field] = {"a": ["same"], "b": ["different"], "c": ["same"]}
    findings = variety_nonregression_findings(menu("a", "b"), menu("a", "c"), expected)
    assert {r["type"] for r in findings} >= {"repeated_pairs_increased"}


@pytest.mark.parametrize(
    "bad", [None, {}, {"a": "蒸"}, {"a": ["蒸", "蒸"]}, {"a": [""]}, {"": []}, {"a": [1]}]
)
def test_malformed_review_cannot_pass(bad: object) -> None:
    expected = reviews({"a": ["蒸"]})
    expected["reviewed_methods"] = bad
    assert (
        variety_nonregression_findings(menu("a"), menu("a"), expected)[0]["type"]
        == "variety_review_missing_or_invalid"
    )


@pytest.mark.parametrize(
    "bad", [None, {}, {"menu": []}, {"menu": [None]}, {"menu": [{"recipe_id": ""}]}]
)
@pytest.mark.parametrize("phase", ["before", "after"])
def test_invalid_menus_do_not_pass(bad: Any, phase: str) -> None:
    old = bad if phase == "before" else menu("a")
    new = bad if phase == "after" else menu("a")
    findings = variety_nonregression_findings(old, new, reviews({"a": []}))
    assert findings == [{"type": "menu_missing_or_invalid", "phase": phase}]


def test_count_unique_ids_and_mutation_boundaries() -> None:
    expected = reviews({"a": ["蒸"], "b": ["煮"]})
    frozen = deepcopy(expected)
    assert variety_nonregression_findings(menu("a"), menu("a", "b"), expected) == [
        {"type": "menu_count_changed"}
    ]
    assert variety_nonregression_findings(menu("a", "a"), menu("a", "b"), expected) == [
        {"type": "menu_source_ids_not_unique", "phase": "before"}
    ]
    assert variety_nonregression_findings(menu("a", "b"), menu("a", "a"), expected) == [
        {"type": "menu_source_ids_not_unique", "phase": "after"}
    ]
    assert expected == frozen
