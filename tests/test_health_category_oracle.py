"""Author-provided engineering scope reviews, not human quality or holdout labels."""

from copy import deepcopy
from typing import Any

import pytest

from evaluation.health_category_oracle import health_category_ranking_findings


def expected() -> dict[str, Any]:
    return {
        "goals": ["降压"],
        "reviewed_roles": {"a": "protein", "b": "vegetable"},
        "reviewed_rank_categories": {"降压": ["vegetable"]},
        "reviewed_positive_roles": {"降压": []},
    }


def trace(bonus: int) -> dict[str, Any]:
    return {
        "降压": {
            "category_foods": ["胡萝卜"],
            "positive_rank_enabled": True,
            "category_bonus_NOT_health": bonus,
        }
    }


def test_classified_success_bits_and_returned_role_cannot_authorize_cross_role_bonus() -> None:
    result = {"menu": [{"recipe_id": "a", "role": "vegetable", "healthy": True}], "status": "ok"}
    findings = health_category_ranking_findings(result, expected(), {"a": trace(2)})
    assert findings[0]["type"] == "category_scope_mismatch"
    assert findings[0]["reviewed_role"] == "protein"


@pytest.mark.parametrize("key,bonus", [("a", 0), ("b", 2)])
def test_valid_scoped_contributions_pass_without_any_overall_score(key: str, bonus: int) -> None:
    assert (
        health_category_ranking_findings(
            {"menu": [{"recipe_id": key}]}, expected(), {key: trace(bonus)}
        )
        == []
    )


def test_actual_food_and_positive_gate_are_required_even_in_matching_role() -> None:
    row = trace(0)
    row["降压"]["category_foods"] = []
    assert (
        health_category_ranking_findings({"menu": [{"recipe_id": "b"}]}, expected(), {"b": row})
        == []
    )
    row["降压"].update(category_foods=["胡萝卜"], positive_rank_enabled=False)
    review = expected()
    review["reviewed_positive_roles"] = {"降压": ["protein"]}
    assert (
        health_category_ranking_findings({"menu": [{"recipe_id": "b"}]}, review, {"b": row}) == []
    )


def test_suggestions_unknown_ids_and_missing_observations_also_fail() -> None:
    result = {"menu": [{"recipe_id": "b"}], "replacement_suggestions": [{"recipe_id": "a"}]}
    findings = health_category_ranking_findings(result, expected(), {"b": trace(2), "a": trace(2)})
    assert findings[0]["output"] == "suggestions"
    assert (
        health_category_ranking_findings({"menu": [{"recipe_id": "new"}]}, expected(), {})[0][
            "type"
        ]
        == "source_role_review_missing"
    )
    assert (
        health_category_ranking_findings(result, expected(), None)[0]["type"]
        == "category_source_observations_missing"
    )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("goals", []),
        ("goals", ["降压", "降压"]),
        ("reviewed_roles", {}),
        ("reviewed_roles", {"a": []}),
        ("reviewed_rank_categories", {}),
        ("reviewed_rank_categories", {"降压": "vegetable"}),
    ],
)
def test_bad_review_fails_closed_without_mutating_input(field: str, bad: object) -> None:
    review = expected()
    review[field] = bad
    frozen = deepcopy(review)
    assert (
        health_category_ranking_findings({"menu": [{"recipe_id": "a"}]}, review, {"a": trace(0)})[
            0
        ]["type"]
        == "category_review_missing_or_invalid"
    )
    assert review == frozen


@pytest.mark.parametrize(
    "field,bad",
    [
        ("category_foods", None),
        ("category_foods", [""]),
        ("positive_rank_enabled", 1),
        ("category_bonus_NOT_health", True),
        ("category_bonus_NOT_health", 100),
        ("category_bonus_NOT_health", None),
    ],
)
def test_malformed_source_trace_cannot_pass(field: str, bad: object) -> None:
    source = trace(0)
    source["降压"][field] = bad
    assert (
        health_category_ranking_findings({"menu": [{"recipe_id": "a"}]}, expected(), {"a": source})[
            0
        ]["type"]
        == "category_trace_missing_or_invalid"
    )


def test_invalid_or_empty_output_is_not_success() -> None:
    invalid_results: list[dict[str, Any]] = [{}, {"menu": []}, {"menu": [None]}]
    for result in invalid_results:
        assert health_category_ranking_findings(result, expected(), {}) == [
            {"type": "menu_missing_or_invalid"}
        ]


def test_self_reported_disabled_positive_gate_cannot_hide_missing_matching_bonus() -> None:
    source = trace(0)
    source["降压"]["positive_rank_enabled"] = False
    findings = health_category_ranking_findings(
        {"menu": [{"recipe_id": "b"}]}, expected(), {"b": source}
    )
    assert {r["type"] for r in findings} == {
        "positive_role_scope_mismatch",
        "category_scope_mismatch",
    }


@pytest.mark.parametrize(
    "bad", [None, {}, {"降压": "protein"}, {"降压": [[]]}, {"降压": ["protein", "protein"]}]
)
def test_bad_positive_role_review_cannot_pass(bad: object) -> None:
    review = expected()
    review["reviewed_positive_roles"] = bad
    assert health_category_ranking_findings(
        {"menu": [{"recipe_id": "a"}]}, review, {"a": trace(0)}
    ) == [{"type": "category_review_missing_or_invalid"}]
