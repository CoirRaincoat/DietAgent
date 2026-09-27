"""Strict self-assessment scorecard contract."""

import json

import pytest

from evaluation.scorecard import build_scorecard, main, write_scorecard


def _regression_report(*, performance: bool = True, hard_failure: bool = False) -> dict:
    checks = [
        {"check": "hard_constraints_satisfied", "passed": not hard_failure},
        {"check": "independent_food_constraints", "passed": True},
        {"check": "catalog_traceability", "passed": True},
        {"check": "recipe_traceability", "passed": True},
    ]
    report = {
        "git_commit": "abc123",
        "dataset": {"version": "synthetic-v2", "case_count": 1, "sha256": "suite"},
        "summary": {
            "cases": 1,
            "passed": 1,
            "failed": 0,
            "functional_score": 70.0,
            "diagnostic_score_valid": performance,
            "diagnostic_score": 100.0 if performance else None,
            "rubric_scores": {
                "basic": 20.0,
                "complex": 20.0,
                "interaction": 30.0,
                "performance": 30.0 if performance else None,
            },
        },
        "cases": [
            {
                "case_id": "safe-menu",
                "rubric": "basic",
                "passed": not hard_failure,
                "turns": [{"turn": 1, "passed": not hard_failure, "checks": checks}],
            }
        ],
    }
    if performance:
        report["performance"] = {
            "threshold_result_valid": True,
            "counts": {
                "scheduled": 3,
                "requests": 3,
                "successful": 3,
                "failed": 0,
                "not_executed": 0,
            },
            "ttft": {"status": "excellent"},
            "single_e2e": {"status": "qualified"},
            "multi_average": {"status": "excellent"},
        }
    return report


def _private_report(*, failures: int = 0) -> dict:
    return {
        "summary": {
            "coverage_complete": True,
            "matrix_sessions": 1000,
            "expected_turns": 1450,
            "completed_turns": 1450,
            "cells_with_failures": failures,
        }
    }


def test_complete_evidence_produces_validated_score() -> None:
    scorecard = build_scorecard(_regression_report(), _private_report())

    assert scorecard["acceptance_status"] == "pass"
    assert scorecard["score_valid"] is True
    assert scorecard["validated_score"] == 100.0
    assert all(gate["status"] == "pass" for gate in scorecard["quality_gates"])


def test_missing_performance_invalidates_total_without_erasing_functional_score() -> None:
    scorecard = build_scorecard(_regression_report(performance=False))

    assert scorecard["acceptance_status"] == "incomplete"
    assert scorecard["score_valid"] is False
    assert scorecard["validated_score"] is None
    assert scorecard["functional_score"] == 70.0


def test_hard_constraint_failure_blocks_misleading_high_score() -> None:
    report = _regression_report()
    report["cases"][0]["turns"][0]["checks"][0]["passed"] = False
    scorecard = build_scorecard(report)

    assert scorecard["observed_diagnostic_score"] == 100.0
    assert scorecard["validated_score"] is None
    assert scorecard["acceptance_status"] == "fail"
    safety = next(
        gate for gate in scorecard["quality_gates"] if gate["name"] == "hard_constraint_safety"
    )
    assert safety["evidence"]["failures"] == [
        {"case_id": "safe-menu", "turn": 1, "check": "hard_constraints_satisfied"}
    ]


def test_private_matrix_failure_is_a_separate_gate() -> None:
    scorecard = build_scorecard(_regression_report(), _private_report(failures=2))

    assert scorecard["acceptance_status"] == "fail"
    assert scorecard["validated_score"] is None


def test_missing_critical_checks_is_incomplete() -> None:
    report = _regression_report()
    report["cases"][0]["turns"][0]["checks"] = []
    scorecard = build_scorecard(report)

    assert scorecard["acceptance_status"] == "incomplete"
    assert scorecard["validated_score"] is None


def test_writer_uses_utf8_and_cli_returns_gate_status(tmp_path) -> None:
    regression_path = tmp_path / "regression.json"
    regression_path.write_text(
        json.dumps(_regression_report(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "scorecard"

    assert main(["--regression-report", str(regression_path), "--output-dir", str(output)]) == 0
    generated = json.loads((output / "assessment.json").read_text(encoding="utf-8"))
    assert generated["sources"]["regression_sha256"]
    assert "接纳状态" in (output / "assessment.md").read_text(encoding="utf-8")


def test_loader_rejects_non_object_report(tmp_path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        main(["--regression-report", str(source), "--output-dir", str(tmp_path / "out")])


def test_writer_returns_stable_artifact_names(tmp_path) -> None:
    scorecard = build_scorecard(_regression_report())
    paths = write_scorecard(tmp_path, scorecard)

    assert paths == {
        "json": tmp_path / "assessment.json",
        "markdown": tmp_path / "assessment.md",
    }
