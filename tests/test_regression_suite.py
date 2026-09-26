"""Versioned synthetic acceptance suite and report contract."""

import json

import httpx
import pytest

from evaluation.regression_suite import (
    DEFAULT_SUITE_PATH,
    CaseDefinition,
    SuiteDefinition,
    TurnDefinition,
    evaluate_turn,
    load_suite,
    run_functional_cases,
    summarize_run,
    write_report_bundle,
)


def _dish(recipe_id: str) -> dict:
    return {
        "slot": 1,
        "recipe_id": recipe_id,
        "name": f"菜品 {recipe_id}",
        "card": {"title": f"菜品 {recipe_id}"},
        "provenance": {
            "recipe_id": recipe_id,
            "source_row": 2,
            "fingerprint": f"sha-{recipe_id}",
        },
        "nutrition": {"recipe_id": recipe_id, "analysis_type": "qualitative"},
    }


def _result(ids: list[str]) -> dict:
    menu = [_dish(recipe_id) | {"slot": index} for index, recipe_id in enumerate(ids, 1)]
    return {
        "status": "ok",
        "menu": menu,
        "replacement_suggestions": [],
        "clarification_questions": [],
        "tool_calls": [
            {"name": "recipe_search"},
            {"name": "health_check"},
            {"name": "nutrition_analysis"},
        ],
        "conversation_state": {
            "constraints": {"people": 2, "meal_type": "晚餐", "dish_count": 3},
            "diners": [{"diner_id": "owner"}, {"diner_id": "guest"}],
            "menu_ids": ids,
            "menu_valid": True,
            "rejected_recipe_ids": [],
        },
        "nutrition_analysis": {"recipe_ids": ids, "analysis_type": "qualitative"},
        "diner_suitability": [
            {
                "diner_id": "owner",
                "hard_constraints_satisfied": True,
                "violations": [],
            },
            {
                "diner_id": "guest",
                "hard_constraints_satisfied": True,
                "violations": [],
            },
        ],
        "timings_ms": {"total": 100.0},
    }


def test_public_suite_is_versioned_and_uses_only_synthetic_profiles() -> None:
    suite = load_suite(DEFAULT_SUITE_PATH)

    assert suite.schema_version == "1.0"
    assert suite.dataset_version == "synthetic-regression-v1"
    assert len(suite.cases) >= 8
    assert {case.user_id for case in suite.cases} <= {900001, 900002, 900003}
    assert {case.rubric for case in suite.cases} == {"basic", "complex", "interaction"}


def test_loader_rejects_non_synthetic_profile_ids(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_version": "bad",
                "data_scope": "synthetic",
                "cases": [
                    {
                        "case_id": "private-user",
                        "rubric": "basic",
                        "user_id": 1,
                        "turns": [{"message": "test", "expect": {"status": "ok"}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="synthetic profile"):
        load_suite(path)


def test_turn_checks_traceability_hard_constraints_and_minimal_change() -> None:
    result = _result(["keep-1", "new-2", "keep-3"])
    outcomes = evaluate_turn(
        result,
        {
            "status": "ok",
            "menu_count": 3,
            "constraints": {"people": 2, "meal_type": "晚餐"},
            "recipe_traceability": True,
            "unique_recipe_ids": True,
            "nutrition_alignment": True,
            "hard_constraints_satisfied": True,
            "diner_count": 2,
            "menu_relation": "only_slots_changed",
            "changed_slots": [2],
            "required_tools": ["health_check"],
        },
        previous_menu_ids=["keep-1", "old-2", "keep-3"],
    )

    assert outcomes
    assert all(outcome["passed"] for outcome in outcomes)


def test_turn_checks_expose_provenance_and_suitability_failures() -> None:
    result = _result(["r1", "r2", "r3"])
    result["menu"][0]["provenance"]["recipe_id"] = "wrong"
    result["diner_suitability"][1]["hard_constraints_satisfied"] = False
    result["diner_suitability"][1]["violations"] = ["花生"]

    outcomes = evaluate_turn(
        result,
        {"recipe_traceability": True, "hard_constraints_satisfied": True},
        previous_menu_ids=None,
    )
    by_name = {outcome["check"]: outcome for outcome in outcomes}

    assert by_name["recipe_traceability"]["passed"] is False
    assert by_name["hard_constraints_satisfied"]["passed"] is False
    assert "花生" in json.dumps(by_name["hard_constraints_satisfied"]["actual"], ensure_ascii=False)


def test_functional_runner_records_invalid_json_instead_of_losing_report() -> None:
    suite = SuiteDefinition(
        schema_version="1.0",
        dataset_version="test",
        data_scope="synthetic",
        description="test",
        cases=(
            CaseDefinition(
                case_id="invalid-json",
                rubric="basic",
                user_id=900001,
                tags=(),
                measure_performance=False,
                turns=(TurnDefinition(message="test", expect={"status": "ok"}),),
            ),
        ),
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not-json", request=request)
    )

    cases, raw = run_functional_cases(
        suite,
        base_url="http://test",
        timeout_seconds=1,
        transport=transport,
    )

    assert cases[0]["passed"] is False
    assert cases[0]["turns"][0]["checks"][0]["check"] == "response_json_object"
    assert raw[0]["response"] == "not-json"


def test_summary_is_internal_and_uses_declared_rubric_weights() -> None:
    cases = [
        {"rubric": "basic", "passed": True},
        {"rubric": "basic", "passed": False},
        {"rubric": "complex", "passed": True},
        {"rubric": "interaction", "passed": True},
    ]
    summary = summarize_run(
        cases,
        performance={
            "ttft": {"status": "excellent"},
            "single_e2e": {"status": "qualified"},
            "multi_average": {"status": "exceeded"},
        },
    )

    assert summary["score_kind"] == "internal_diagnostic_not_official"
    assert summary["rubric_scores"] == {
        "basic": 10.0,
        "complex": 20.0,
        "interaction": 30.0,
        "performance": 15.0,
    }
    assert summary["diagnostic_score"] == 75.0


def test_report_bundle_writes_machine_human_and_raw_outputs(tmp_path) -> None:
    report = {
        "dataset": {"version": "synthetic-regression-v1", "sha256": "abc"},
        "summary": {
            "cases": 1,
            "passed": 1,
            "failed": 0,
            "diagnostic_score": 100.0,
            "score_kind": "internal_diagnostic_not_official",
            "rubric_scores": {
                "basic": 20.0,
                "complex": 20.0,
                "interaction": 30.0,
                "performance": 30.0,
            },
        },
        "cases": [{"case_id": "basic", "rubric": "basic", "passed": True, "turns": []}],
    }
    paths = write_report_bundle(tmp_path, report, [{"case_id": "basic", "response": {}}])

    assert json.loads(paths["json"].read_text(encoding="utf-8"))["summary"]["passed"] == 1
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "仅供内部诊断" in markdown
    assert "basic" in markdown
    assert paths["responses"].read_text(encoding="utf-8").count("\n") == 1
