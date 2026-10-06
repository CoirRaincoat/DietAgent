"""Independent authored-focus checks must not trust planner/response assertions."""

from typing import Any

import pytest

from app.domain.models import Ingredient, Recipe
from evaluation.focus_oracle import culinary_focus_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


def test_garnish_families_and_missing_observations_cannot_self_certify() -> None:
    result = {"menu": [{"recipe_id": "cabbage", "families": ["cabbage"], "passed": True}]}
    expected = {"reviewed_focus": {"cabbage": ["cabbage"]}}
    assert culinary_focus_findings(result, expected, {"cabbage": ["cabbage", "pork"]})
    assert not culinary_focus_findings(result, expected, {"cabbage": ["cabbage"]})
    assert (
        culinary_focus_findings(result, expected, None)[0]["type"]
        == "source_focus_observations_missing"
    )


def test_empty_review_is_unsupported_ranking_evidence_not_perfect_novelty() -> None:
    result = {"menu": [{"recipe_id": "cake"}]}
    expected: dict[str, Any] = {"reviewed_focus": {"cake": []}}
    assert not culinary_focus_findings(result, expected, {"cake": []})
    assert culinary_focus_findings(result, expected, {"cake": ["millet"]})


@pytest.mark.parametrize(
    "review",
    [
        None,
        [],
        "bad",
        {},
        {"reviewed_focus": {}},
        {"reviewed_focus": {" ": []}},
        {"reviewed_focus": {1: []}},
        {"reviewed_focus": {"r": "pumpkin"}},
        {"reviewed_focus": {"r": None}},
        {"reviewed_focus": {"r": [None]}},
        {"reviewed_focus": {"r": [""]}},
        {"reviewed_focus": {"r": ["x", "x"]}},
    ],
)
def test_invalid_review_contracts_fail_closed(review: Any) -> None:
    assert (
        culinary_focus_findings({"menu": [{"recipe_id": "r"}]}, review, {"r": []})[0]["type"]
        == "focus_review_missing_or_invalid"
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"menu": None},
        {"menu": []},
        {"menu": "x"},
        {"menu": [None]},
        {"menu": [{"recipe_id": "r"}], "replacement_suggestions": None},
        {"menu": [{"recipe_id": "r"}], "replacement_suggestions": [None]},
    ],
)
def test_invalid_outputs_fail_closed(result: dict[str, Any]) -> None:
    assert (
        culinary_focus_findings(result, {"reviewed_focus": {"r": []}}, {"r": []})[0]["type"]
        == "menu_missing_or_invalid"
    )


@pytest.mark.parametrize("key", [None, "", 7])
def test_invalid_emitted_source_identity_is_not_reviewed(key: Any) -> None:
    assert (
        culinary_focus_findings(
            {"menu": [{"recipe_id": key}]}, {"reviewed_focus": {"r": []}}, {"r": []}
        )[0]["type"]
        == "recipe_id_missing_or_invalid"
    )


def test_missing_reviews_and_focus_and_malformed_observations_fail() -> None:
    result = {"menu": [{"recipe_id": "r"}]}
    assert (
        culinary_focus_findings(result, {"reviewed_focus": {"other": []}}, {"r": []})[0]["type"]
        == "source_review_missing"
    )
    assert (
        culinary_focus_findings(result, {"reviewed_focus": {"r": []}}, {})[0]["type"]
        == "source_focus_unavailable"
    )
    assert (
        culinary_focus_findings(result, {"reviewed_focus": {"r": ["x"]}}, {"r": "x"})[0]["type"]
        == "culinary_focus_mismatch"
    )


def test_every_suggestion_is_checked_not_only_the_main_menu() -> None:
    result = {"menu": [{"recipe_id": "r"}], "replacement_suggestions": [{"recipe_id": "s"}]}
    expected = {"reviewed_focus": {"r": ["pumpkin"], "s": ["wheat"]}}
    findings = culinary_focus_findings(
        result, expected, {"r": ["pumpkin"], "s": ["wheat", "pumpkin"]}
    )
    assert len(findings) == 1 and findings[0]["output"] == "suggestions"


def test_public_focus_gate_reads_source_and_blocks_quality_not_response_focus() -> None:
    source = Recipe(
        recipe_id="tofu",
        name="蒸豆腐",
        raw_ingredients="嫩豆腐",
        steps="蒸熟后装盘。",
        ingredients=[Ingredient(name="嫩豆腐", raw="嫩豆腐")],
        categories=["vegetable"],
        source_row=1,
        fingerprint="synthetic-test",
    )
    payload = {"status": "ok", "menu": [{"recipe_id": "tofu", "focus": ["tofu"], "passed": True}]}
    expected = {"culinary_focus": {"reviewed_focus": {"tofu": ["tofu"]}}}
    checks = evaluate_turn(payload, expected, previous_menu_ids=None, recipes={"tofu": source})
    assert next(c for c in checks if c["check"] == "independent_culinary_focus")["passed"] is False
    checks += [
        {"check": "catalog_traceability", "passed": True},
        {"check": "independent_food_constraints", "passed": True},
    ]
    assert (
        _menu_quality_for_turn(["tofu"], checks, {"tofu": source})["reason"]
        == "menu_validation_failed"
    )
    fixed = source.model_copy(update={"categories": ["protein"]})
    passing = evaluate_turn(payload, expected, previous_menu_ids=None, recipes={"tofu": fixed})
    assert next(c for c in passing if c["check"] == "independent_culinary_focus")["passed"] is True
    missing = evaluate_turn(payload, expected, previous_menu_ids=None)
    assert next(c for c in missing if c["check"] == "independent_culinary_focus")["passed"] is False


@pytest.mark.parametrize("actual", [7, "x", {"x": True}, [None], [""], [1, "x"]])
def test_invalid_observation_values_fail_without_raising(actual: Any) -> None:
    findings = culinary_focus_findings(
        {"menu": [{"recipe_id": "r"}]}, {"reviewed_focus": {"r": ["x"]}}, {"r": actual}
    )
    assert findings


def test_tuple_observation_order_is_not_a_second_focus() -> None:
    assert not culinary_focus_findings(
        {"menu": [{"recipe_id": "r"}]}, {"reviewed_focus": {"r": ["x", "y"]}}, {"r": ("y", "x")}
    )
