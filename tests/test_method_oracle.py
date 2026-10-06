"""Independent finishing reviews must reject source/response self-certification."""

from typing import Any

import pytest

from app.domain.models import Ingredient, Recipe
from evaluation.method_oracle import cooking_method_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


@pytest.mark.parametrize(
    "bad", [None, {}, {"r": "蒸"}, {"r": ["蒸", "蒸"]}, {"": []}, {"r": [None]}]
)
def test_bad_review_contracts_fail_closed(bad: Any) -> None:
    assert cooking_method_findings(
        {"menu": [{"recipe_id": "r"}]}, {"reviewed_methods": bad}, {"r": ["蒸"]}
    )


def test_response_success_and_labels_do_not_override_the_source_observation() -> None:
    payload = {"menu": [{"recipe_id": "r", "methods": ["蒸"], "passed": True}]}
    expected = {"reviewed_methods": {"r": ["蒸"]}}
    assert cooking_method_findings(payload, expected, {"r": ["烤", "蒸"]})
    assert not cooking_method_findings(payload, expected, {"r": ["蒸"]})
    assert cooking_method_findings(payload, expected, None)


def test_unknown_methods_are_not_an_absence_of_cooking_or_novelty_grade() -> None:
    expected: dict[str, Any] = {"reviewed_methods": {"r": []}}
    assert not cooking_method_findings({"menu": [{"recipe_id": "r"}]}, expected, {"r": []})
    assert cooking_method_findings({"menu": [{"recipe_id": "r"}]}, expected, {})


def test_every_suggestion_requires_an_independent_source_review() -> None:
    payload = {"menu": [{"recipe_id": "r"}], "replacement_suggestions": [{"recipe_id": "s"}]}
    findings = cooking_method_findings(
        payload, {"reviewed_methods": {"r": ["蒸"]}}, {"r": ["蒸"], "s": ["烤"]}
    )
    assert findings[0]["output"] == "suggestions" and findings[0]["type"] == "source_review_missing"


def test_public_gate_blocks_quality_when_only_tags_support_a_claim() -> None:
    record = Recipe(
        recipe_id="r",
        name="蒸豆腐",
        raw_ingredients="豆腐",
        ingredients=[Ingredient(name="豆腐", raw="豆腐")],
        steps="食材放在蒸烤架上备用。",
        methods=["蒸"],
        categories=["protein"],
        source_row=1,
        fingerprint="authored",
    )
    payload = {"status": "ok", "menu": [{"recipe_id": "r", "methods": ["蒸"]}]}
    expected = {"cooking_methods": {"reviewed_methods": {"r": ["蒸"]}}}
    checks = evaluate_turn(payload, expected, previous_menu_ids=None, recipes={"r": record})
    assert (
        next(check for check in checks if check["check"] == "independent_cooking_methods")["passed"]
        is False
    )
    checks += [
        {"check": "catalog_traceability", "passed": True},
        {"check": "independent_food_constraints", "passed": True},
    ]
    assert (
        _menu_quality_for_turn(["r"], checks, {"r": record})["reason"] == "menu_validation_failed"
    )
    source = record.model_copy(update={"steps": "豆腐蒸熟后装盘。"})
    passed = evaluate_turn(payload, expected, previous_menu_ids=None, recipes={"r": source})
    assert (
        next(check for check in passed if check["check"] == "independent_cooking_methods")["passed"]
        is True
    )
    missing = evaluate_turn(payload, expected, previous_menu_ids=None)
    assert (
        next(check for check in missing if check["check"] == "independent_cooking_methods")[
            "passed"
        ]
        is False
    )
