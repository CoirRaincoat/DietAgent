"""Reviewer-authored source kinds, independent of production dish classifiers."""

from typing import Any

import pytest

from evaluation.composition_oracle import composition_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


def expectation() -> dict[str, Any]:
    """Synthetic source review: garnish pork is meat, eggs permit no-meat."""
    return {
        "meat": 1,
        "vegetarian": 1,
        "reviewed_recipe_kinds": {
            "cabbage_with_pork": "meat",
            "plain_cabbage": "vegetarian",
            "eggs": "vegetarian",
            "broth": "soup",
            "unknown_sauce": "other",
        },
    }


def menu(*ids: str) -> list[dict[str, Any]]:
    return [{"recipe_id": key, "categories": ["vegetarian"]} for key in ids]


def test_oracle_ignores_returned_role_labels_and_self_reported_counts() -> None:
    expected = expectation()
    assert composition_findings(menu("cabbage_with_pork", "eggs", "broth"), expected) == []
    result: dict[str, Any] = {
        "menu": menu("plain_cabbage", "eggs", "broth"),
        "conversation_state": {"constraints": {"meat_dish_count": 1}},
        "reason": "荤菜1道，素菜1道，全部满足。",
    }
    checks = evaluate_turn(result, {"dish_composition": expected}, previous_menu_ids=None)
    quantity = next(c for c in checks if c["check"] == "independent_dish_composition")
    assert quantity["passed"] is False
    assert quantity["actual"][0] == {
        "output": "menu",
        "type": "quantity_mismatch",
        "kind": "meat",
        "expected": 1,
        "actual": 0,
    }
    gates = [
        *checks,
        {"check": "catalog_traceability", "passed": True},
        {"check": "independent_food_constraints", "passed": True},
    ]
    assert _menu_quality_for_turn([d["recipe_id"] for d in result["menu"]], gates, None) == {
        "status": "unavailable",
        "reason": "menu_validation_failed",
    }


def test_oracle_rejects_first_slot_proposal_that_breaks_quantities() -> None:
    findings = composition_findings(
        menu("cabbage_with_pork", "eggs", "broth"), expectation(), menu("plain_cabbage")
    )
    assert all(f["output"] == "suggestion_1" for f in findings)
    assert [f["actual"] for f in findings] == [0, 2]


@pytest.mark.parametrize("index", [{}, None, [], {"r": "plant"}, {"r": []}])
def test_oracle_invalid_source_review_is_a_failure_not_a_crash(index: Any) -> None:
    assert composition_findings(menu("r"), {"reviewed_recipe_kinds": index}) == [
        {"type": "review_index_missing_or_invalid"}
    ]


def test_oracle_requires_review_for_every_id_including_proposals() -> None:
    findings = composition_findings(
        menu("cabbage_with_pork", "eggs", "broth"), expectation(), menu("unreviewed")
    )
    assert findings == [
        {"output": "suggestion_1", "type": "source_review_missing", "recipe_ids": ["unreviewed"]}
    ]


def test_oracle_does_not_count_soup_unknown_or_staple_as_entree() -> None:
    expected = expectation() | {"meat": 0, "vegetarian": 2}
    findings = composition_findings(menu("broth", "unknown_sauce", "eggs"), expected)
    assert findings == [
        {
            "output": "menu",
            "type": "quantity_mismatch",
            "kind": "vegetarian",
            "expected": 2,
            "actual": 1,
        }
    ]


def test_oracle_empty_menu_cannot_pass_regression_gate() -> None:
    findings = composition_findings([], expectation(), menu("eggs"))
    assert any(f["type"] == "suggestion_without_menu" for f in findings)
    checks = evaluate_turn(
        {"menu": []}, {"dish_composition": expectation()}, previous_menu_ids=None
    )
    assert (
        next(c for c in checks if c["check"] == "independent_dish_composition")["passed"] is False
    )


def test_oracle_unrequested_kind_has_no_invented_quota() -> None:
    expected = expectation()
    expected.pop("meat")
    assert composition_findings(menu("unknown_sauce", "eggs", "broth"), expected) == []
