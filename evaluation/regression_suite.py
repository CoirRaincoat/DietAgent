"""Run the public synthetic regression suite and write auditable reports.

The suite deliberately uses only handwritten synthetic profiles. Functional
checks use the structured ``/chat`` response. A small declared subset is also
replayed through the OpenAI-compatible SSE endpoint to measure client-observed
TTFT and end-to-end latency.
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import httpx

from app.domain.models import Recipe
from app.infrastructure.data import RECIPE_PATH
from app.infrastructure.synthetic import load_synthetic_catalog
from evaluation.stream_performance import (
    StreamObservation,
    measure_stream_turn,
    summarize_observations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_PATH = Path(__file__).with_name("cases") / "regression_v2.json"
VALIDATOR_VERSION = "synthetic-validator-v2"
SYNTHETIC_USER_IDS = {900001, 900002, 900003}
RUBRIC_WEIGHTS = {"basic": 20.0, "complex": 20.0, "interaction": 30.0}
PERFORMANCE_METRICS = ("ttft", "single_e2e", "multi_average")
PERFORMANCE_POINTS = {"excellent": 10.0, "qualified": 5.0, "exceeded": 0.0, "no_data": 0.0}
Rubric = Literal["basic", "complex", "interaction"]


@dataclass(frozen=True)
class TurnDefinition:
    """One synthetic user turn and its observable expectations."""

    message: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class CaseDefinition:
    """An independent conversation in the public regression suite."""

    case_id: str
    rubric: Rubric
    user_id: int
    tags: tuple[str, ...]
    measure_performance: bool
    turns: tuple[TurnDefinition, ...]


@dataclass(frozen=True)
class SuiteDefinition:
    """Validated, versioned synthetic regression data."""

    schema_version: str
    dataset_version: str
    data_scope: str
    description: str
    cases: tuple[CaseDefinition, ...]


def load_suite(path: Path = DEFAULT_SUITE_PATH) -> SuiteDefinition:
    """Load and validate a public synthetic suite without production data."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") not in {"1.0", "2.0"}:
        raise ValueError("Unsupported regression suite schema version")
    if raw.get("data_scope") != "synthetic":
        raise ValueError("Regression suite must declare synthetic data scope")
    case_ids: set[str] = set()
    cases: list[CaseDefinition] = []
    for value in raw.get("cases", []):
        case_id = str(value.get("case_id", "")).strip()
        if not case_id or case_id in case_ids:
            raise ValueError("Regression case IDs must be non-empty and unique")
        case_ids.add(case_id)
        rubric = value.get("rubric")
        if rubric not in RUBRIC_WEIGHTS:
            raise ValueError(f"Unsupported rubric for case {case_id}")
        user_id = value.get("user_id")
        if user_id not in SYNTHETIC_USER_IDS:
            raise ValueError(f"Case {case_id} must use a supported synthetic profile")
        turns = tuple(
            TurnDefinition(message=str(turn["message"]).strip(), expect=dict(turn["expect"]))
            for turn in value.get("turns", [])
        )
        if not turns or any(not turn.message for turn in turns):
            raise ValueError(f"Case {case_id} must contain non-empty turns")
        if raw["schema_version"] == "2.0":
            for turn in turns:
                if turn.expect.get("status") == "ok" and (
                    not turn.expect.get("catalog_traceability")
                    or "independent_food_rules" not in turn.expect
                ):
                    raise ValueError(f"Case {case_id} requires independent recipe checks")
                for rule in turn.expect.get("independent_food_rules", []):
                    if not rule.get("label") or not rule.get("forbidden_terms"):
                        raise ValueError(f"Case {case_id} has an empty food oracle rule")
        cases.append(
            CaseDefinition(
                case_id=case_id,
                rubric=rubric,
                user_id=user_id,
                tags=tuple(str(tag) for tag in value.get("tags", [])),
                measure_performance=bool(value.get("measure_performance", False)),
                turns=turns,
            )
        )
    if not cases:
        raise ValueError("Regression suite contains no cases")
    return SuiteDefinition(
        schema_version=raw["schema_version"],
        dataset_version=str(raw["dataset_version"]),
        data_scope=raw["data_scope"],
        description=str(raw.get("description", "")),
        cases=tuple(cases),
    )


def _check(name: str, expected: Any, actual: Any, passed: bool) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "expected": expected, "actual": actual}


def _menu_ids(result: dict[str, Any]) -> list[str]:
    return [str(item.get("recipe_id", "")) for item in result.get("menu", [])]


def _traceability_failures(result: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in result.get("menu", []):
        recipe_id = item.get("recipe_id")
        provenance = item.get("provenance") or {}
        card = item.get("card") or {}
        nutrition = item.get("nutrition") or {}
        valid = bool(
            recipe_id
            and item.get("name")
            and card.get("title") == item.get("name")
            and provenance.get("recipe_id") == recipe_id
            and isinstance(provenance.get("source_row"), int)
            and provenance["source_row"] >= 2
            and provenance.get("fingerprint")
            and nutrition.get("recipe_id") == recipe_id
        )
        if not valid:
            failures.append({"recipe_id": recipe_id, "slot": item.get("slot")})
    return failures


def _comparable(value: Any) -> Any:
    return sorted(value) if isinstance(value, list) else value


def _diner_checks(result: dict[str, Any], expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare authored personal facts, including absence, rather than service verdicts."""
    diners = (result.get("conversation_state") or {}).get("diners", [])
    checks = []
    matched_ids = []
    for person in expected:
        selector = "profile_owner" if person.get("profile_owner") else "display_name"
        wanted_identity = person[selector]
        matches = [diner for diner in diners if diner.get(selector) == wanted_identity]
        fields = {key: value for key, value in person.items() if key != selector}
        actual = (
            {key: matches[0].get(key) for key in fields} if len(matches) == 1 else None
        )
        passed = actual is not None and all(
            _comparable(actual[key]) == _comparable(value) for key, value in fields.items()
        )
        if len(matches) == 1:
            matched_ids.append(matches[0].get("diner_id"))
        checks.append(_check(f"diner_facts:{selector}={wanted_identity}", fields, actual, passed))
    checks.append(_check(
        "diner_identity_coverage", "one distinct diner per expected identity", matched_ids,
        len(matched_ids) == len(expected) and None not in matched_ids
        and len(set(matched_ids)) == len(matched_ids),
    ))
    return checks


def _independent_recipe_checks(
    result: dict[str, Any],
    expected: dict[str, Any],
    recipes: dict[str, Recipe] | None,
) -> list[dict[str, Any]]:
    """Audit source recipes and all visible suggestions using frozen case vocabulary.

    This intentionally does not call RuleEngine or trust returned constraints,
    suitability flags, ingredients, or fingerprints as an independent source.
    The finite vocabulary is a regression oracle, not complete food-safety coverage.
    """
    trace_failures = []
    food_failures = []
    items = [("menu", item) for item in result.get("menu", [])]
    items += [("replacement_suggestions", item)
              for item in result.get("replacement_suggestions", [])]
    for location, item in items:
        recipe_id = item.get("recipe_id")
        recipe = (recipes or {}).get(recipe_id)
        evidence = {"location": location, "recipe_id": recipe_id}
        if recipe is None:
            trace_failures.append(evidence | {"reason": "recipe_missing_from_local_catalog"})
            food_failures.append(evidence | {"reason": "cannot_audit_unknown_recipe"})
            continue
        provenance = item.get("provenance") or {}
        if (item.get("name") != recipe.name
                or provenance.get("recipe_id") != recipe.recipe_id
                or provenance.get("source_row") != recipe.source_row
                or provenance.get("fingerprint") != recipe.fingerprint):
            trace_failures.append(evidence | {"reason": "source_identity_mismatch"})
        # Include recipe steps: optional and step-only additions remain in scope.
        source_text = recipe.raw_ingredients + "\n" + recipe.steps
        visible_text = json.dumps({key: item.get(key) for key in (
            "ingredients", "ingredient_details", "steps", "cooking_steps"
        )}, ensure_ascii=False)
        for rule in expected.get("independent_food_rules", []):
            for origin, raw_text in (("catalog", source_text), ("response", visible_text)):
                text = re.sub(r"\s+", "", raw_text).casefold()
                for ignored in rule.get("ignore_phrases", []):
                    text = text.replace(ignored.casefold(), " ")
                matched = [term for term in rule["forbidden_terms"] if term.casefold() in text]
                if matched:
                    food_failures.append(evidence | {
                        "origin": origin, "rule": rule["label"], "matched": matched,
                    })
    return [
        _check("catalog_traceability", "menu and suggestions match local recipe source",
               trace_failures, bool(result.get("menu")) and recipes is not None
               and not trace_failures),
        _check("independent_food_constraints", "authored food rules pass for menu and suggestions",
               food_failures, bool(result.get("menu")) and recipes is not None
               and not food_failures),
    ]


def evaluate_turn(
    result: dict[str, Any],
    expected: dict[str, Any],
    *,
    previous_menu_ids: list[str] | None,
    recipes: dict[str, Recipe] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate declared response facts plus the optional independent source oracle."""
    outcomes: list[dict[str, Any]] = []
    menu_ids = _menu_ids(result)
    state = result.get("conversation_state") or {}
    constraints = state.get("constraints") or {}
    tools = [event.get("name") for event in result.get("tool_calls", [])]

    if "status" in expected:
        outcomes.append(
            _check(
                "status",
                expected["status"],
                result.get("status"),
                result.get("status") == expected["status"],
            )
        )
    if "menu_count" in expected:
        outcomes.append(
            _check(
                "menu_count",
                expected["menu_count"],
                len(menu_ids),
                len(menu_ids) == expected["menu_count"],
            )
        )
    if "constraints" in expected:
        actual = {key: constraints.get(key) for key in expected["constraints"]}
        outcomes.append(
            _check(
                "constraints", expected["constraints"], actual,
                all(_comparable(actual[key]) == _comparable(value)
                    for key, value in expected["constraints"].items())
            )
        )
    if "expected_diners" in expected:
        outcomes.extend(_diner_checks(result, expected["expected_diners"]))
    if expected.get("catalog_traceability") or "independent_food_rules" in expected:
        outcomes.extend(_independent_recipe_checks(result, expected, recipes))
    if "clarification_fields" in expected:
        actual = sorted(
            question.get("field") for question in result.get("clarification_questions", [])
        )
        wanted = sorted(expected["clarification_fields"])
        outcomes.append(_check("clarification_fields", wanted, actual, actual == wanted))
    if expected.get("no_planning"):
        actual = {"menu_count": len(menu_ids), "tool_count": len(tools)}
        outcomes.append(
            _check(
                "no_planning",
                {"menu_count": 0, "tool_count": 0},
                actual,
                not menu_ids and not tools,
            )
        )
    if expected.get("recipe_traceability"):
        failures = _traceability_failures(result)
        outcomes.append(
            _check(
                "recipe_traceability",
                "all menu items traceable",
                failures,
                bool(menu_ids) and not failures,
            )
        )
    if expected.get("unique_recipe_ids"):
        outcomes.append(
            _check(
                "unique_recipe_ids",
                "all unique",
                menu_ids,
                bool(menu_ids) and len(menu_ids) == len(set(menu_ids)),
            )
        )
    if expected.get("nutrition_alignment"):
        nutrition_ids = (result.get("nutrition_analysis") or {}).get("recipe_ids")
        outcomes.append(
            _check(
                "nutrition_alignment",
                menu_ids,
                nutrition_ids,
                bool(menu_ids) and nutrition_ids == menu_ids,
            )
        )
    if expected.get("hard_constraints_satisfied"):
        suitability = result.get("diner_suitability", [])
        failures = [
            {"diner_id": item.get("diner_id"), "violations": item.get("violations", [])}
            for item in suitability
            if not item.get("hard_constraints_satisfied") or item.get("violations")
        ]
        outcomes.append(
            _check(
                "hard_constraints_satisfied",
                "all diners pass",
                failures,
                bool(suitability) and not failures,
            )
        )
    if "diner_count" in expected:
        count = len(state.get("diners", []))
        outcomes.append(
            _check("diner_count", expected["diner_count"], count, count == expected["diner_count"])
        )
    if "menu_valid" in expected:
        actual = state.get("menu_valid")
        outcomes.append(
            _check("menu_valid", expected["menu_valid"], actual, actual is expected["menu_valid"])
        )
    if "required_tools" in expected:
        missing = sorted(set(expected["required_tools"]) - set(tools))
        outcomes.append(
            _check(
                "required_tools",
                expected["required_tools"],
                {"tools": tools, "missing": missing},
                not missing,
            )
        )
    if "forbidden_tools" in expected:
        present = sorted(set(expected["forbidden_tools"]) & set(tools))
        outcomes.append(
            _check(
                "forbidden_tools",
                expected["forbidden_tools"],
                {"tools": tools, "present": present},
                not present,
            )
        )
    if "menu_relation" in expected:
        relation = expected["menu_relation"]
        changed = []
        if previous_menu_ids is not None and len(previous_menu_ids) == len(menu_ids):
            changed = [
                index
                for index, pair in enumerate(zip(previous_menu_ids, menu_ids), 1)
                if pair[0] != pair[1]
            ]
        if relation == "unchanged":
            passed = previous_menu_ids is not None and menu_ids == previous_menu_ids
            wanted: Any = "same recipe IDs in the same slots"
        elif relation == "disjoint":
            passed = previous_menu_ids is not None and not set(menu_ids).intersection(
                previous_menu_ids
            )
            wanted = "no recipe ID reused from previous menu"
        elif relation == "only_slots_changed":
            wanted = expected.get("changed_slots", [])
            passed = previous_menu_ids is not None and changed == wanted
        else:
            raise ValueError(f"Unknown menu relation: {relation}")
        outcomes.append(
            _check(
                "menu_relation",
                wanted,
                {"previous": previous_menu_ids, "current": menu_ids, "changed_slots": changed},
                passed,
            )
        )
    if expected.get("rejected_previous_absent"):
        rejected = set(state.get("rejected_recipe_ids", []))
        visible = set(menu_ids)
        visible.update(
            str(item.get("recipe_id", "")) for item in result.get("replacement_suggestions", [])
        )
        previous = set(previous_menu_ids or [])
        actual = {"persisted": sorted(previous & rejected), "visible": sorted(previous & visible)}
        outcomes.append(
            _check(
                "rejected_previous_absent",
                sorted(previous),
                actual,
                bool(previous) and previous <= rejected and not previous.intersection(visible),
            )
        )
    return outcomes


def _dataset_metadata(path: Path, suite: SuiteDefinition) -> dict[str, Any]:
    return {
        "version": suite.dataset_version,
        "validator_version": VALIDATOR_VERSION,
        "schema_version": suite.schema_version,
        "data_scope": suite.data_scope,
        "path": path.relative_to(PROJECT_ROOT).as_posix()
        if path.is_relative_to(PROJECT_ROOT)
        else str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "case_count": len(suite.cases),
        "turn_count": sum(len(case.turns) for case in suite.cases),
    }


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_functional_cases(
    suite: SuiteDefinition,
    *,
    base_url: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
    recipes: dict[str, Recipe] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute every conversation independently against the structured API."""
    if recipes is None and any(
        turn.expect.get("catalog_traceability") or "independent_food_rules" in turn.expect
        for case in suite.cases for turn in case.turns
    ):
        recipes = load_synthetic_catalog().recipes
    case_reports: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    run_id = uuid4().hex[:12]
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        trust_env=False,
        transport=transport,
    ) as client:
        for case in suite.cases:
            session_id: str | None = None
            previous_menu_ids: list[str] | None = None
            turn_reports: list[dict[str, Any]] = []
            for number, turn in enumerate(case.turns, 1):
                request_id = f"reg-{run_id}-{case.case_id}-{number}"
                payload: dict[str, Any] = {
                    "user_id": case.user_id,
                    "message": turn.message,
                    "request_id": request_id,
                }
                if session_id:
                    payload["session_id"] = session_id
                started = perf_counter()
                try:
                    response = client.post("/chat", json=payload)
                    elapsed_ms = round((perf_counter() - started) * 1000, 3)
                except httpx.HTTPError as error:
                    checks = [_check("http_200", 200, type(error).__name__, False)]
                    turn_reports.append(
                        {
                            "turn": number,
                            "message": turn.message,
                            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                            "passed": False,
                            "checks": checks,
                        }
                    )
                    raw_responses.append(
                        {
                            "case_id": case.case_id,
                            "turn": number,
                            "request_id": request_id,
                            "transport_error": type(error).__name__,
                        }
                    )
                    break
                if response.status_code != 200:
                    checks = [_check("http_200", 200, response.status_code, False)]
                    try:
                        body: Any = response.json()
                    except (json.JSONDecodeError, ValueError):
                        body = response.text[:2000]
                    turn_reports.append(
                        {
                            "turn": number,
                            "message": turn.message,
                            "http_status": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "passed": False,
                            "checks": checks,
                        }
                    )
                    raw_responses.append(
                        {
                            "case_id": case.case_id,
                            "turn": number,
                            "request_id": request_id,
                            "http_status": response.status_code,
                            "response": body,
                        }
                    )
                    break
                try:
                    result = response.json()
                except (json.JSONDecodeError, ValueError):
                    result = None
                if not isinstance(result, dict):
                    checks = [
                        _check("response_json_object", "JSON object", type(result).__name__, False)
                    ]
                    turn_reports.append(
                        {
                            "turn": number,
                            "message": turn.message,
                            "http_status": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "passed": False,
                            "checks": checks,
                        }
                    )
                    raw_responses.append(
                        {
                            "case_id": case.case_id,
                            "turn": number,
                            "request_id": request_id,
                            "http_status": response.status_code,
                            "response": response.text[:2000],
                        }
                    )
                    break
                checks = [_check("http_200", 200, response.status_code, True)]
                checks.extend(
                    evaluate_turn(result, turn.expect, previous_menu_ids=previous_menu_ids, recipes=recipes)
                )
                state = result.get("conversation_state") or {}
                response_session = state.get("session_id")
                if session_id is None:
                    session_id = response_session
                checks.append(
                    _check(
                        "session_continuity",
                        session_id,
                        response_session,
                        bool(session_id) and response_session == session_id,
                    )
                )
                current_ids = _menu_ids(result)
                turn_report = {
                    "turn": number,
                    "message": turn.message,
                    "http_status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "status": result.get("status"),
                    "menu_ids": current_ids,
                    "server_timings_ms": result.get("timings_ms", {}),
                    "passed": all(check["passed"] for check in checks),
                    "checks": checks,
                }
                turn_reports.append(turn_report)
                raw_responses.append(
                    {
                        "case_id": case.case_id,
                        "turn": number,
                        "request_id": request_id,
                        "http_status": response.status_code,
                        "response": result,
                    }
                )
                if current_ids:
                    previous_menu_ids = current_ids
            complete = len(turn_reports) == len(case.turns)
            case_reports.append(
                {
                    "case_id": case.case_id,
                    "rubric": case.rubric,
                    "tags": list(case.tags),
                    "user_id": case.user_id,
                    "passed": complete and all(turn["passed"] for turn in turn_reports),
                    "turns": turn_reports,
                }
            )
    return case_reports, raw_responses


async def run_performance_cases(
    suite: SuiteDefinition,
    *,
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Replay declared cases over SSE for client-observed timing."""
    observations: list[StreamObservation] = []
    scheduled = {case.case_id: len(case.turns) for case in suite.cases if case.measure_performance}
    single_ids: set[str] = set()
    multi_ids: set[str] = set()
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False
    ) as client:
        for case in suite.cases:
            if not case.measure_performance:
                continue
            (single_ids if len(case.turns) == 1 else multi_ids).add(case.case_id)
            session_id: str | None = None
            for number, turn in enumerate(case.turns, 1):
                observation = await measure_stream_turn(
                    client,
                    user_id=case.user_id,
                    message=turn.message,
                    request_id=f"perf-{uuid4().hex}",
                    scenario_id=case.case_id,
                    turn=number,
                    session_id=session_id,
                )
                if observation.completed and len(case.turns) > 1 and (
                    not observation.session_id
                    or (session_id is not None and observation.session_id != session_id)
                ):
                    observation = replace(
                        observation, completed=False, error="invalid_session_continuity"
                    )
                observations.append(observation)
                if observation.session_id:
                    session_id = observation.session_id
                if not observation.completed:
                    break
    all_summary = summarize_observations(
        observations, multi_turn=True, expected_requests=sum(scheduled.values())
    )
    single_summary = summarize_observations(
        [item for item in observations if item.scenario_id in single_ids], multi_turn=False,
        expected_requests=sum(scheduled[key] for key in single_ids),
    )
    multi_summary = summarize_observations(
        [item for item in observations if item.scenario_id in multi_ids], multi_turn=True,
        expected_requests=sum(scheduled[key] for key in multi_ids),
    )

    def metric(summary: dict[str, Any], key: str) -> dict[str, Any]:
        distribution = summary[key]
        return {
            "mean_ms": distribution["mean"],
            "p50_ms": distribution["p50"],
            "p95_ms": distribution["p95"],
            "status": distribution["status_by_mean"],
            "successful_only_status": distribution["successful_only_status_by_mean"],
        }

    return {
        "thresholds_are_strict_less_than": True,
        "threshold_result_valid": all(
            summary["threshold_result_valid"]
            for summary in (all_summary, single_summary, multi_summary)
        ),
        "counts": {key: all_summary[key] for key in (
            "scheduled", "requests", "successful", "failed", "not_executed"
        )},
        "measurement_scope": "successful_request_latency; invalid groups are not scored",
        "ttft": metric(all_summary, "ttft_ms"),
        "single_e2e": metric(single_summary, "e2e_ms"),
        "multi_average": metric(multi_summary, "e2e_ms"),
        "observations": [asdict(item) for item in observations],
    }


def summarize_run(
    cases: list[dict[str, Any]],
    *,
    performance: dict[str, Any] | None,
) -> dict[str, Any]:
    """Calculate a transparent internal diagnostic score, never an official score."""
    rubric_scores: dict[str, float | None] = {}
    for rubric, weight in RUBRIC_WEIGHTS.items():
        selected = [case for case in cases if case.get("rubric") == rubric]
        passed = sum(bool(case.get("passed")) for case in selected)
        rubric_scores[rubric] = round(weight * passed / len(selected), 2) if selected else 0.0
    functional_score = round(sum(value for value in rubric_scores.values() if value is not None), 2)
    performance_status = "not_run" if performance is None else "invalid"
    performance_score = None
    if performance and performance.get("threshold_result_valid") and all(
        performance.get(metric, {}).get("status") in {"excellent", "qualified", "exceeded"}
        for metric in PERFORMANCE_METRICS
    ):
        performance_status = "valid"
        performance_score = sum(
            PERFORMANCE_POINTS[performance[metric]["status"]] for metric in PERFORMANCE_METRICS
        )
    rubric_scores["performance"] = performance_score
    total = len(cases)
    passed_cases = sum(bool(case.get("passed")) for case in cases)
    return {
        "score_kind": "internal_diagnostic_not_official",
        "cases": total,
        "passed": passed_cases,
        "failed": total - passed_cases,
        "rubric_scores": rubric_scores,
        "functional_score": functional_score,
        "performance_status": performance_status,
        "diagnostic_score_valid": performance_status == "valid",
        "diagnostic_score": (
            round(functional_score + performance_score, 2) if performance_score is not None else None
        ),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    dataset = report["dataset"]
    lines = [
        "# 合成回归测试报告",
        "",
        "> 本报告及分数仅供内部诊断和版本对比，不是评委官方评分。",
        "",
        f"- 运行时间（UTC）：{report.get('started_at', 'unknown')}",
        f"- Git 提交：`{report.get('git_commit', 'unknown')}`",
        f"- 数据集：`{dataset['version']}`",
        f"- 验证器：`{dataset.get('validator_version', 'legacy/unspecified')}`",
        f"- 数据集 SHA-256：`{dataset['sha256']}`",
        f"- 功能场景：{summary['passed']}/{summary['cases']} 通过",
        f"- 功能诊断分：{summary.get('functional_score', '未记录')}/70",
        f"- 性能有效性：{summary.get('performance_status', '未记录')}",
        (f"- 内部诊断分：{summary['diagnostic_score']}/100"
         if summary['diagnostic_score'] is not None else
         "- 内部诊断分：未生成（性能无效或未执行，不参与接纳评分）"),
        "",
        "## 分项诊断",
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
        score = summary["rubric_scores"][key]
        lines.append(f"| {labels[key]} | {score if score is not None else '不计分'} |")
    lines.extend(
        ["", "## 场景运行情况", "", "| 场景 | 分项 | 结果 | 失败检查 |", "|---|---|---|---|"]
    )
    for case in report["cases"]:
        failed = [
            check["check"]
            for turn in case.get("turns", [])
            for check in turn.get("checks", [])
            if not check.get("passed")
        ]
        lines.append(
            f"| `{case['case_id']}` | {case['rubric']} | {'通过' if case['passed'] else '失败'} | {('、'.join(failed) if failed else '-')} |"
        )
    performance = report.get("performance")
    if performance:
        lines.extend(
            [
                "",
                "## 客户端性能观测",
                "",
                f"阈值结果有效：{performance.get('threshold_result_valid', False)}。"
                "以下延迟仅统计成功请求；无效组的耗时不能作为整体达标证据。",
                "",
                "计划 / 已执行 / 成功 / 失败 / 未执行：" + " / ".join(
                    str(performance.get("counts", {}).get(key, "未记录"))
                    for key in ("scheduled", "requests", "successful", "failed", "not_executed")
                ),
                "",
                "| 指标 | 平均值（ms） | P50 | P95 | 档位 |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for key in PERFORMANCE_METRICS:
            value = performance[key]
            lines.append(
                f"| {key} | {value['mean_ms']} | {value['p50_ms']} | {value['p95_ms']} | {value['status']} |"
            )
    lines.extend(["", "完整逐项断言见 `report.json`，逐轮结构化响应见 `responses.jsonl`。", ""])
    return "\n".join(lines)


def write_report_bundle(
    output_dir: Path,
    report: dict[str, Any],
    raw_responses: list[dict[str, Any]],
) -> dict[str, Path]:
    """Write machine-readable, human-readable and raw synthetic outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    responses_path = output_dir / "responses.jsonl"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    with responses_path.open("w", encoding="utf-8") as stream:
        for response in raw_responses:
            stream.write(json.dumps(response, ensure_ascii=False) + "\n")
    return {"json": json_path, "markdown": markdown_path, "responses": responses_path}


def _validate_base_url(value: str) -> str:
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.username or url.password:
        raise ValueError("Base URL must be HTTP(S) without embedded credentials")
    return value.rstrip("/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--output-root", type=Path, default=Path("runtime/regression_reports"))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--skip-performance", action="store_true")
    args = parser.parse_args(argv)

    started_at = datetime.now(timezone.utc)
    suite_path = args.suite.resolve()
    suite = load_suite(suite_path)
    base_url = _validate_base_url(args.base_url)
    cases, raw_responses = run_functional_cases(
        suite, base_url=base_url, timeout_seconds=args.timeout
    )
    performance = None
    if not args.skip_performance:
        performance = asyncio.run(
            run_performance_cases(suite, base_url=base_url, timeout_seconds=args.timeout)
        )
    summary = summarize_run(cases, performance=performance)
    finished_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "report_schema_version": "2.0",
        "validator_version": VALIDATOR_VERSION,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
        "git_commit": _git_commit(),
        "base_url": base_url,
        "dataset": _dataset_metadata(suite_path, suite),
        "recipe_source": {
            "path": RECIPE_PATH.as_posix(),
            "sha256": hashlib.sha256((PROJECT_ROOT / RECIPE_PATH).read_bytes()).hexdigest(),
        },
        "summary": summary,
        "cases": cases,
    }
    if performance is not None:
        report["performance"] = performance
    run_directory = args.output_root / started_at.strftime("%Y%m%dT%H%M%SZ")
    paths = write_report_bundle(run_directory, report, raw_responses)
    print(json.dumps({"output_dir": str(run_directory), **summary}, ensure_ascii=False, indent=2))
    print(f"Markdown report: {paths['markdown']}")
    performance_valid = performance is None or (
        performance["threshold_result_valid"]
        and all(
            performance[metric]["status"] in {"excellent", "qualified"}
            for metric in PERFORMANCE_METRICS
        )
    )
    return 0 if summary["failed"] == 0 and performance_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
