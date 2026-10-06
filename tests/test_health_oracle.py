"""Reviewer requirements cannot pass just because production says 'matched'."""

from copy import deepcopy
from typing import Any

import pytest

from evaluation.health_oracle import health_reporting_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


def expectation() -> dict[str, Any]:
    """Authored salt requirement from an explicit fictional source declaration."""
    return {
        "goals": ["降压"],
        "reviewed_goals": {
            "salted": {"降压": {"cautions": ["盐"], "configured": True}},
            "plain": {"降压": {"cautions": [], "configured": True}},
        },
    }


def payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "menu": [{"recipe_id": "salted"}],
        "replacement_suggestions": [],
        "reason": "仍有盐来源；未判断个人摄入量。",
        "nutrition_analysis": {
            "goal_matches": [
                {
                    "goal": "降压",
                    "status": "caution",
                    "ingredient_names": ["盐"],
                    "reasons": ["源配料声明盐。"],
                    "limitation": "没有个人分餐量，不能判定低钠。",
                }
            ]
        },
    }


def test_explicit_caution_and_boundary_pass_without_a_quality_score() -> None:
    assert health_reporting_findings(payload(), expectation()) == []


@pytest.mark.parametrize("status", ["preference_match", "insufficient_data", "fulfilled"])
def test_returned_positive_or_unknown_status_cannot_hide_reviewed_salt(status: str) -> None:
    result = payload()
    result["nutrition_analysis"]["goal_matches"][0]["status"] = status
    findings = health_reporting_findings(result, expectation())
    assert any(f["type"] == "source_caution_not_reported" for f in findings)


def test_missing_salt_evidence_fails_even_when_caution_flag_is_present() -> None:
    result = payload()
    result["nutrition_analysis"]["goal_matches"][0].update(ingredient_names=[], reasons=[])
    assert any(
        f["type"] == "source_caution_not_reported"
        for f in health_reporting_findings(result, expectation())
    )


@pytest.mark.parametrize(
    "mutation", ["review", "goals", "configured", "cautions", "duplicate_goals"]
)
def test_bad_authored_reviews_fail_closed(mutation: str) -> None:
    expected = expectation()
    if mutation == "review":
        expected["reviewed_goals"] = {}
    elif mutation == "goals":
        expected["goals"] = []
    elif mutation == "configured":
        expected["reviewed_goals"]["salted"]["降压"]["configured"] = "yes"
    elif mutation == "cautions":
        expected["reviewed_goals"]["salted"]["降压"]["cautions"] = [None]
    else:
        expected["goals"] *= 2
    assert health_reporting_findings(payload(), expected) == [
        {"type": "health_review_missing_or_invalid"}
    ]


def test_unreviewed_source_cannot_pass_based_on_its_returned_nutrition() -> None:
    result = payload()
    result["menu"][0]["recipe_id"] = "unreviewed"
    assert any(
        f["type"] == "source_review_missing"
        for f in health_reporting_findings(result, expectation())
    )


def test_suggestion_has_its_own_goal_caution_check() -> None:
    result = payload()
    bad = deepcopy(result["nutrition_analysis"])
    bad["goal_matches"][0]["status"] = "preference_match"
    result["replacement_suggestions"] = [{"recipe_id": "salted", "nutrition": bad}]
    assert any(
        f["output"] == "suggestions" and f["type"] == "source_caution_not_reported"
        for f in health_reporting_findings(result, expectation())
    )


def test_unconfigured_goal_and_missing_limitations_are_not_reported_as_matches() -> None:
    expected = expectation()
    for reviewed in expected["reviewed_goals"].values():
        reviewed["降压"] = {"configured": False, "cautions": []}
    result = payload()
    result["nutrition_analysis"]["goal_matches"][0]["limitation"] = ""
    types = {f["type"] for f in health_reporting_findings(result, expected)}
    assert {"unconfigured_goal_claim", "health_scope_missing"} <= types


@pytest.mark.parametrize(
    "mutation", ["empty", "invalid_record", "missing_nutrition", "missing_goal", "success_claim"]
)
def test_missing_outputs_and_explicit_attainment_claims_fail(mutation: str) -> None:
    result = payload()
    if mutation == "empty":
        result["menu"] = []
    elif mutation == "invalid_record":
        result["menu"] = [None]
    elif mutation == "missing_nutrition":
        del result["nutrition_analysis"]
    elif mutation == "missing_goal":
        result["nutrition_analysis"]["goal_matches"] = []
    else:
        result["reason"] = "本餐健康目标已达标"
    assert health_reporting_findings(result, expectation())


def test_public_validator_runs_the_independent_gate() -> None:
    result = payload()
    expected = {"status": "ok", "health_reporting": expectation()}
    outcomes = evaluate_turn(result, expected, previous_menu_ids=None)
    gate = next(item for item in outcomes if item["check"] == "independent_health_reporting")
    assert gate["passed"] is True
    result["nutrition_analysis"]["goal_matches"][0]["status"] = "preference_match"
    outcomes = evaluate_turn(result, expected, previous_menu_ids=None)
    assert (
        next(item for item in outcomes if item["check"] == "independent_health_reporting")["passed"]
        is False
    )
    outcomes.extend(
        [
            {"check": "catalog_traceability", "passed": True},
            {"check": "independent_food_constraints", "passed": True},
        ]
    )
    assert _menu_quality_for_turn(["salted"], outcomes, None) == {
        "status": "unavailable",
        "reason": "menu_validation_failed",
    }
