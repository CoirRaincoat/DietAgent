"""Reviewer-owned context classifications ignore self-certified result tags."""

from typing import Any

import pytest

from evaluation.context_oracle import context_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


def expectation() -> dict[str, Any]:
    return {
        "meal_type": "晚餐",
        "reviewed_context": {
            "adult": {"audience": "ordinary", "meals": ["午餐", "晚餐"]},
            "baby": {"audience": "infant_only", "meals": ["晚餐"]},
            "egg": {"audience": "ordinary", "meals": ["早餐"]},
            "unknown": {"audience": "ordinary", "meals": []},
        },
    }


def items(*ids: str) -> list[dict[str, Any]]:
    return [{"recipe_id": key, "meal_types": ["晚餐"], "audience": "ordinary"} for key in ids]


@pytest.mark.parametrize(
    "key,kind",
    [
        ("baby", "infant_source_in_ordinary_menu"),
        ("egg", "other_meal_evidence"),
        ("unknown", "meal_evidence_missing"),
        ("missing", "source_review_missing"),
    ],
)
def test_context_oracle_does_not_trust_menu_certificates(key: str, kind: str) -> None:
    findings = context_findings(items("adult", key), expectation())
    assert len(findings) == 1 and findings[0]["type"] == kind


def test_positive_review_and_fallback_boundary_are_distinct() -> None:
    assert context_findings(items("adult"), expectation()) == []
    assert (
        context_findings(items("egg", "unknown"), expectation() | {"require_matching_tags": False})
        == []
    )
    assert context_findings(items("baby"), expectation() | {"require_matching_tags": False})
    assert context_findings([], expectation()) == [{"type": "menu_missing"}]
    assert (
        context_findings(items("adult"), expectation(), items("baby"))[0]["output"] == "suggestions"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"meal_type": []},
        {"meal_type": "晚饭"},
        {"require_matching_tags": "yes"},
        {"reviewed_context": None},
        {"reviewed_context": {}},
        {"reviewed_context": {"r": {"audience": [], "meals": []}}},
        {"reviewed_context": {"r": {"audience": "ordinary", "meals": "晚餐"}}},
        {"reviewed_context": {"r": {"audience": "ordinary", "meals": [[]]}}},
    ],
)
def test_invalid_review_fails_closed(bad: dict[str, Any]) -> None:
    expected = {} if not bad else expectation() | bad
    assert context_findings(items("adult"), expected) == [
        {"type": "context_review_missing_or_invalid"}
    ]


def test_context_failure_blocks_quality_observation() -> None:
    checks = evaluate_turn(
        {"menu": items("baby"), "reason": "适配已验证"},
        {"meal_context": expectation()},
        previous_menu_ids=None,
    )
    assert not next(c for c in checks if c["check"] == "independent_meal_context")["passed"]
    checks.extend(
        [
            {"check": "catalog_traceability", "passed": True},
            {"check": "independent_food_constraints", "passed": True},
        ]
    )
    assert _menu_quality_for_turn(["baby"], checks, None) == {
        "status": "unavailable",
        "reason": "menu_validation_failed",
    }
