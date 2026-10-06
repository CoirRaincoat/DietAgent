"""Independent reviews retain broth/garnish facts regardless of returned labels."""

from typing import Any

import pytest

from evaluation.diet_oracle import diet_findings
from evaluation.regression_suite import _menu_quality_for_turn, evaluate_turn


def expectation(mode: str = "vegan") -> dict[str, Any]:
    return {
        "mode": mode,
        "reviewed_source_kinds": {
            "cabbage": "plant",
            "rice": "plant",
            "winter_soup": "plant",
            "egg": "egg_dairy_honey",
            "pork_soup": "meat",
            "pork_garnish": "meat",
            "unknown_broth": "unknown",
        },
    }


def menu(*ids: str) -> list[dict[str, Any]]:
    return [{"recipe_id": key, "categories": ["vegetarian"], "diet_mode": "vegan"} for key in ids]


@pytest.mark.parametrize("key", ["pork_soup", "pork_garnish", "unknown_broth", "egg"])
def test_diet_oracle_ignores_positive_service_labels(key: str) -> None:
    findings = diet_findings(menu("cabbage", key), expectation())
    assert len(findings) == 1 and findings[0]["recipe_id"] == key


def test_soup_and_staple_count_for_whole_meal_and_lacto_allows_egg() -> None:
    assert diet_findings(menu("cabbage", "rice", "winter_soup"), expectation()) == []
    assert diet_findings(menu("egg", "winter_soup"), expectation("ovo_lacto_vegetarian")) == []


@pytest.mark.parametrize("index", [None, {}, [], {"r": []}, {"r": "vegetarian"}])
def test_bad_source_review_fails_closed(index: Any) -> None:
    assert diet_findings(menu("r"), {"mode": "vegan", "reviewed_source_kinds": index}) == [
        {"type": "diet_review_missing_or_invalid"}
    ]


def test_unrecognized_mode_and_unreviewed_ids_cannot_pass() -> None:
    assert diet_findings(menu("cabbage"), expectation("unknown"))
    assert diet_findings(menu("cabbage"), expectation() | {"mode": []})
    assert diet_findings(menu("unreviewed"), expectation())[0]["type"] == "source_review_missing"
    assert diet_findings([], expectation())[0]["type"] == "menu_missing"


def test_bad_replacement_proposal_fails_even_when_menu_is_valid() -> None:
    finding = diet_findings(menu("cabbage", "winter_soup"), expectation(), menu("pork_garnish"))
    assert finding[0]["output"] == "suggestions"


def test_diet_failure_blocks_downstream_quality_observation() -> None:
    checks = evaluate_turn(
        {"menu": menu("cabbage", "pork_soup"), "reason": "整餐纯素已满足"},
        {"whole_meal_diet": expectation()},
        previous_menu_ids=None,
    )
    assert not next(c for c in checks if c["check"] == "independent_whole_meal_diet")["passed"]
    gates = checks + [
        {"check": "catalog_traceability", "passed": True},
        {"check": "independent_food_constraints", "passed": True},
    ]
    assert _menu_quality_for_turn(["cabbage", "pork_soup"], gates, None) == {
        "status": "unavailable",
        "reason": "menu_validation_failed",
    }
