"""Role audit expectations are authored, not copied from classifier verdicts."""

from typing import Any

import pytest

from app.domain.models import Recipe
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn
from evaluation.role_oracle import primary_role_findings


def payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "menu": [{"recipe_id": "wrapped", "categories": ["protein"]}],
        "replacement_suggestions": [{"recipe_id": "leafy"}],
    }


def review() -> dict[str, Any]:
    return {"reviewed_roles": {"wrapped": "protein", "leafy": "vegetable"}}


def test_catalog_role_not_returned_success_or_category_is_the_observation() -> None:
    assert primary_role_findings(
        payload(), review(), {"wrapped": ["vegetable"], "leafy": ["vegetable"]}
    ) == [
        {
            "output": "menu",
            "recipe_id": "wrapped",
            "type": "primary_role_mismatch",
            "reviewed_role": "protein",
            "observed_roles": ["vegetable"],
        }
    ]
    assert (
        primary_role_findings(payload(), review(), {"wrapped": ["protein"], "leafy": ["vegetable"]})
        == []
    )


@pytest.mark.parametrize("roles", [[], ["protein", "vegetable"], ["dessert"], "protein"])
def test_unknown_multiple_or_non_meal_roles_fail(roles: Any) -> None:
    assert (
        primary_role_findings(payload(), review(), {"wrapped": roles, "leafy": ["vegetable"]})[0][
            "type"
        ]
        == "primary_role_mismatch"
    )


@pytest.mark.parametrize(
    "bad", [None, {}, [], {"wrapped": []}, {"": "protein"}, {7: "protein"}, {"wrapped": "dessert"}]
)
def test_bad_review_contract_fails_closed(bad: Any) -> None:
    assert primary_role_findings(payload(), {"reviewed_roles": bad}, {}) == [
        {"type": "role_review_missing_or_invalid"}
    ]


@pytest.mark.parametrize("bad", [None, [], "invalid"])
def test_invalid_expectation_container_does_not_crash(bad: Any) -> None:
    assert primary_role_findings(payload(), bad, {}) == [{"type": "role_review_missing_or_invalid"}]


@pytest.mark.parametrize("bad", [None, [], [None], "not a menu"])
def test_bad_or_empty_menu_fails(bad: Any) -> None:
    result = payload()
    result["menu"] = bad
    assert primary_role_findings(result, review(), {}) == [{"type": "menu_missing_or_invalid"}]


@pytest.mark.parametrize(
    "mutation",
    ["suggestion", "unreviewed", "missing_id", "invalid_id", "unavailable", "no_observations"],
)
def test_suggestions_and_missing_source_facts_cannot_self_certify(mutation: str) -> None:
    result = payload()
    observed: dict[str, list[str]] | None = {"wrapped": ["protein"], "leafy": ["vegetable"]}
    if mutation == "suggestion":
        result["replacement_suggestions"] = [None]
    elif mutation == "unreviewed":
        result["replacement_suggestions"] = [{"recipe_id": "not_reviewed"}]
    elif mutation == "missing_id":
        result["menu"] = [{}]
    elif mutation == "invalid_id":
        result["menu"] = [{"recipe_id": []}]
    elif mutation == "unavailable":
        observed = {}
    else:
        observed = None
    assert primary_role_findings(result, review(), observed)


def test_absent_optional_suggestion_list_can_pass_only_with_catalog_evidence() -> None:
    assert (
        primary_role_findings(
            {"menu": [{"recipe_id": "wrapped"}]}, review(), {"wrapped": ("protein",)}
        )
        == []
    )


def test_public_validator_blocks_quality_observation_when_reviewed_role_is_wrong() -> None:
    result = {"status": "ok", "menu": [{"recipe_id": "wrapped"}], "replacement_suggestions": []}
    recipe = Recipe(
        recipe_id="wrapped",
        name="荠菜包",
        raw_ingredients="荠菜、肉末、豆腐衣",
        steps="豆腐衣包入肉馅蒸熟。",
        raw_label="晚餐",
        categories=["vegetable"],
        source_row=2,
        fingerprint="authored-synthetic-fixture",
    )
    expected = {"status": "ok", "primary_roles": review()}
    checks = evaluate_turn(result, expected, previous_menu_ids=None, recipes={"wrapped": recipe})
    gate = next(item for item in checks if item["check"] == "independent_primary_roles")
    assert gate["passed"] is False
    checks += [
        {"check": "catalog_traceability", "passed": True},
        {"check": "independent_food_constraints", "passed": True},
    ]
    assert _menu_quality_for_turn(["wrapped"], checks, {"wrapped": recipe}) == {
        "status": "unavailable",
        "reason": "menu_validation_failed",
    }
    corrected = recipe.model_copy(update={"categories": ["protein"]})
    passing = evaluate_turn(
        result, expected, previous_menu_ids=None, recipes={"wrapped": corrected}
    )
    assert (
        next(item for item in passing if item["check"] == "independent_primary_roles")["passed"]
        is True
    )


def test_requested_role_audit_without_catalog_is_not_reported_as_verified() -> None:
    checks = evaluate_turn(payload(), {"primary_roles": review()}, previous_menu_ids=None)
    assert (
        next(item for item in checks if item["check"] == "independent_primary_roles")["passed"]
        is False
    )
