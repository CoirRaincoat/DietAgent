"""Build a strict, auditable scorecard from existing evaluation reports.

The scorecard never invents a score.  It preserves the regression harness's
internal diagnostic score, then separately decides whether the evidence is
complete enough for that score to be used as an acceptance signal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

GateStatus = Literal["pass", "fail", "not_run"]

SAFETY_CHECKS = {
    "constraints",
    "diner_identity_coverage",
    "hard_constraints_satisfied",
    "independent_food_constraints",
}
TRACEABILITY_CHECKS = {"catalog_traceability", "recipe_traceability"}
SAFE_PERFORMANCE_STATUSES = {"excellent", "qualified"}
PERFORMANCE_METRICS = ("ttft", "single_e2e", "multi_average")


def _read_json(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON object and reject other top-level shapes."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matches_check(name: str, names: set[str]) -> bool:
    includes_diner_facts = "diner_identity_coverage" in names
    return name in names or (name.startswith("diner_facts:") and includes_diner_facts)


def _check_evidence(cases: list[dict[str, Any]], names: set[str]) -> dict[str, Any]:
    observed = 0
    passed = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        for turn in case.get("turns", []):
            for check in turn.get("checks", []):
                name = str(check.get("check", ""))
                if not _matches_check(name, names):
                    continue
                observed += 1
                if check.get("passed") is True:
                    passed += 1
                else:
                    failures.append(
                        {
                            "case_id": case.get("case_id"),
                            "turn": turn.get("turn"),
                            "check": name,
                        }
                    )
    return {
        "observed": observed,
        "passed": passed,
        "failed": observed - passed,
        "failures": failures,
    }


def _gate(name: str, status: GateStatus, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "status": status, "evidence": evidence}


def _regression_gate(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    dataset = report.get("dataset") or {}
    reported = int(summary.get("cases", 0))
    expected = int(dataset.get("case_count", reported))
    passed = int(summary.get("passed", 0))
    failed = int(summary.get("failed", max(0, reported - passed)))
    complete = expected > 0 and reported == expected and passed + failed == reported
    successful = complete and failed == 0
    return _gate(
        "functional_regression",
        "pass" if successful else "fail",
        {"expected": expected, "reported": reported, "passed": passed, "failed": failed},
    )


def _check_gate(name: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if not evidence["observed"]:
        status: GateStatus = "not_run"
    else:
        status = "pass" if not evidence["failed"] else "fail"
    return _gate(name, status, evidence)


def _performance_gate(report: dict[str, Any]) -> dict[str, Any]:
    performance = report.get("performance")
    if not isinstance(performance, dict):
        return _gate("performance", "not_run", {"reason": "performance_report_missing"})
    counts = performance.get("counts") or {}
    statuses = {
        metric: (performance.get(metric) or {}).get("status") for metric in PERFORMANCE_METRICS
    }
    valid = performance.get("threshold_result_valid") is True
    acceptable = valid and all(value in SAFE_PERFORMANCE_STATUSES for value in statuses.values())
    return _gate(
        "performance",
        "pass" if acceptable else "fail",
        {"threshold_result_valid": valid, "counts": counts, "statuses": statuses},
    )


def _private_gate(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return _gate("private_matrix", "not_run", {"reason": "not_requested"})
    summary = report.get("summary") or {}
    complete = summary.get("coverage_complete") is True
    failures = int(summary.get("cells_with_failures", 0))
    return _gate(
        "private_matrix",
        "pass" if complete and failures == 0 else "fail",
        {
            "coverage_complete": complete,
            "matrix_sessions": summary.get("matrix_sessions"),
            "expected_turns": summary.get("expected_turns"),
            "completed_turns": summary.get("completed_turns"),
            "cells_with_failures": failures,
        },
    )


def _overall_status(gates: list[dict[str, Any]], *, private_required: bool) -> str:
    required = [gate for gate in gates if gate["name"] != "private_matrix" or private_required]
    if any(gate["status"] == "fail" for gate in required):
        return "fail"
    if any(gate["status"] == "not_run" for gate in required):
        return "incomplete"
    return "pass"


def build_scorecard(
    regression_report: dict[str, Any],
    private_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a gate-aware self-assessment from regression and private reports."""
    cases = regression_report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Regression report must contain a cases list")
    summary = regression_report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Regression report must contain a summary object")

    safety = _check_evidence(cases, SAFETY_CHECKS)
    traceability = _check_evidence(cases, TRACEABILITY_CHECKS)
    gates = [
        _regression_gate(regression_report),
        _check_gate("hard_constraint_safety", safety),
        _check_gate("recipe_traceability", traceability),
        _performance_gate(regression_report),
        _private_gate(private_report),
    ]
    private_required = private_report is not None
    status = _overall_status(gates, private_required=private_required)
    diagnostic_score = summary.get("diagnostic_score")
    score_valid = (
        status == "pass"
        and summary.get("diagnostic_score_valid") is True
        and isinstance(diagnostic_score, int | float)
    )
    return {
        "schema_version": "self-assessment-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "score_kind": "internal_diagnostic_not_official",
        "acceptance_status": status,
        "score_valid": score_valid,
        "validated_score": diagnostic_score if score_valid else None,
        "observed_diagnostic_score": diagnostic_score,
        "functional_score": summary.get("functional_score"),
        "rubric_scores": summary.get("rubric_scores", {}),
        "quality_gates": gates,
        "evidence": {
            "dataset": regression_report.get("dataset", {}),
            "git_commit": regression_report.get("git_commit", "unknown"),
            "private_matrix_included": private_required,
        },
        "limitations": [
            "Internal diagnostic evidence, not an official competition score.",
            "Synthetic checks do not replace hidden tests or expert review.",
            "Private matrix uses reviewed intent fixtures and does not score model NLU.",
        ],
    }


def render_markdown(scorecard: dict[str, Any]) -> str:
    """Render a compact human-readable scorecard."""
    score = scorecard["validated_score"]
    lines = [
        "# 一体化自测评分卡",
        "",
        "> 本评分仅用于内部版本比较，不是评委官方成绩。",
        "",
        f"- 接纳状态：**{scorecard['acceptance_status']}**",
        f"- 有效内部总分：**{f'{score}/100' if score is not None else '未生成'}**",
        f"- 已观测诊断分：{scorecard.get('observed_diagnostic_score')}",
        f"- 功能诊断分：{scorecard.get('functional_score')}/70",
        "",
        "## 分项得分",
        "",
        "| 分项 | 得分 |",
        "|---|---:|",
    ]
    labels = {
        "basic": "基础推荐",
        "complex": "复杂组合",
        "interaction": "多轮交互",
        "performance": "性能",
    }
    for key in ("basic", "complex", "interaction", "performance"):
        value = scorecard.get("rubric_scores", {}).get(key)
        lines.append(f"| {labels[key]} | {value if value is not None else '不计分'} |")
    lines.extend(["", "## 质量门禁", "", "| 门禁 | 状态 | 证据 |", "|---|---|---|"])
    for gate in scorecard["quality_gates"]:
        evidence = json.dumps(gate["evidence"], ensure_ascii=False, separators=(",", ":"))
        lines.append(f"| `{gate['name']}` | **{gate['status']}** | `{evidence}` |")
    lines.extend(
        [
            "",
            "## 解释",
            "",
            "- `pass`：所有必需证据完整且门禁通过，可用于内部版本接纳。",
            "- `fail`：存在功能、硬约束、真实性、性能或私有矩阵失败。",
            "- `incomplete`：缺少性能或关键核验，观察分不能视为有效总分。",
            "",
        ]
    )
    return "\n".join(lines)


def write_scorecard(output_dir: Path, scorecard: dict[str, Any]) -> dict[str, Path]:
    """Write JSON and Markdown scorecard artifacts using UTF-8."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output_dir / "assessment.json",
        "markdown": output_dir / "assessment.md",
    }
    paths["json"].write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths["markdown"].write_text(render_markdown(scorecard), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    """Load component reports, write a scorecard and return its gate result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression-report", type=Path, required=True)
    parser.add_argument("--private-report", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    regression = _read_json(args.regression_report)
    private = _read_json(args.private_report) if args.private_report else None
    scorecard = build_scorecard(regression, private)
    scorecard["sources"] = {
        "regression_sha256": _sha256(args.regression_report),
        "private_sha256": _sha256(args.private_report) if args.private_report else None,
    }
    paths = write_scorecard(args.output_dir, scorecard)
    print(json.dumps(scorecard, ensure_ascii=False, indent=2))
    print(f"Markdown assessment: {paths['markdown']}")
    return 0 if scorecard["acceptance_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
