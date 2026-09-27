"""Local-only replay of every private profile against every supplied dialogue.

The supplied dialogues contain no profile binding. Each matrix cell therefore
has its own conversation, while the fixed human Intent annotations exercise
the real agent, retrieval, rules, planner and SQLite state without an API key.
Only allowlisted diagnostics are written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.agent.service import MealAgent
from app.infrastructure.data import DataCatalog, normalize_profile, normalize_recipes
from app.infrastructure.sessions import SessionStore
from evaluation.offline import ANNOTATED_SOURCE_SHA256, ANNOTATIONS, FixtureLLM
from evaluation.real_data import check_allergens, check_source
from evaluation.reporting import write_bundle

BINDING_MODE = "matrix_without_source_binding"
SCHEMA_VERSION = "private-matrix.v1"
EXPECTED_PROFILES = 50
EXPECTED_DIALOGUES = 20
EXPECTED_DIALOGUE_TURNS = 29
EXPECTED_RECIPES = 2000
_STATUS_CODES = {"ok", "clarification_required", "no_feasible_menu"}
_FAILURE_CODES = {
    "execution_error",
    "session_identity",
    "revision_mismatch",
    "state_persistence",
    "profile_allergy_lost",
    "profile_health_goal_lost",
    "menu_state_mismatch",
    "diner_constraint_violation",
    "nutrition_menu_mismatch",
    "recipe_source_mismatch",
    "excluded_ingredient_in_source",
    "allergen_oracle_incomplete",
    "unresolved_exposed_menu",
    "incomplete_replay",
    "known_allergen_in_source",
    "unknown_composite_with_allergy",
    "spicy_ingredient_with_no_spicy_constraint",
}
_TOP_FIELDS = {
    "report_schema_version",
    "data_scope",
    "binding_mode",
    "started_at",
    "finished_at",
    "git_commit",
    "sources",
    "summary",
    "cells",
    "limitations",
}
_SUMMARY_FIELDS = {
    "profiles",
    "dialogues",
    "matrix_sessions",
    "expected_turns",
    "completed_turns",
    "coverage_complete",
    "cells_with_failures",
    "menu_observations",
    "source_checks",
    "status_counts",
    "failure_codes",
    "local_elapsed_seconds",
}
_CELL_FIELDS = {
    "profile_id",
    "dialogue_case_id",
    "expected_turns",
    "completed_turns",
    "status_counts",
    "menu_observations",
    "source_checks",
    "elapsed_ms",
    "failures",
}
_LIMITATION_CODES = {
    "human_intent_fixture_not_nlu",
    "no_source_profile_binding",
    "offline_timing_not_model_latency",
    "nutrition_amounts_unavailable",
    "allergen_oracle_cannot_check_cross_contact",
}


@dataclass(frozen=True)
class Dialogue:
    """One original conversation, held only in process memory."""

    case_id: int
    messages: tuple[str, ...]

    @property
    def turn_count(self) -> int:
        return len(self.messages)


@dataclass(frozen=True)
class PrivateInputs:
    """Validated source identities and private dialogue content."""

    profile_ids: tuple[int, ...]
    dialogues: tuple[Dialogue, ...]
    profile_path: Path
    dialogue_path: Path
    profile_sha256: str
    dialogue_sha256: str
    binding_mode: str = BINDING_MODE


@dataclass(frozen=True)
class MatrixCell:
    """One profile/dialogue pairing with an isolated logical session."""

    profile_id: int
    dialogue: Dialogue

    @property
    def session_key(self) -> str:
        return f"profile-{self.profile_id}-dialogue-{self.dialogue.case_id}"


@dataclass(frozen=True)
class CellResult:
    """Allowlisted observations for one conversation."""

    profile_id: int
    dialogue_case_id: int
    expected_turns: int
    completed_turns: int
    status_counts: dict[str, int]
    menu_observations: int
    source_checks: int
    elapsed_ms: float
    failures: tuple[str, ...]


def _sha256(path: Path) -> str:
    """Hash source bytes without placing those bytes into the report."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    """Identify the tested checkout without exposing source paths."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _read_array(path: Path, kind: str) -> list[dict[str, Any]]:
    """Read a JSON array of objects with strict container validation."""
    value: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, dict) for item in value)
    ):
        raise ValueError(f"{kind} must be a non-empty JSON array of objects")
    return value


def load_private_inputs(profile_path: Path, dialogue_path: Path) -> PrivateInputs:
    """Validate IDs and turn counts without inferring a profile-dialogue match."""
    profiles = _read_array(profile_path, "Profiles")
    ids: list[int] = []
    for value in profiles:
        profile_id = value.get("id")
        if type(profile_id) is not int or profile_id <= 0:
            raise ValueError("Profile IDs must be positive integers")
        if profile_id in ids:
            raise ValueError("Duplicate profile ID")
        ids.append(profile_id)
    dialogues = _read_array(dialogue_path, "Dialogues")
    cases: list[Dialogue] = []
    case_ids: set[int] = set()
    for value in dialogues:
        if "user_id" in value:
            raise ValueError("Dialogue already contains user_id; review binding before replay")
        case_id = value.get("id")
        messages = value.get("user_messages")
        count = value.get("turn_count")
        if type(case_id) is not int or case_id <= 0 or case_id in case_ids:
            raise ValueError("Dialogue IDs must be positive and unique")
        if (
            not isinstance(messages, list)
            or not messages
            or any(not isinstance(message, str) or not message.strip() for message in messages)
            or type(count) is not int
            or count != len(messages)
        ):
            raise ValueError("Dialogue turn_count must match non-empty user_messages")
        case_ids.add(case_id)
        cases.append(Dialogue(case_id=case_id, messages=tuple(messages)))
    return PrivateInputs(
        profile_ids=tuple(sorted(ids)),
        dialogues=tuple(sorted(cases, key=lambda case: case.case_id)),
        profile_path=profile_path,
        dialogue_path=dialogue_path,
        profile_sha256=_sha256(profile_path),
        dialogue_sha256=_sha256(dialogue_path),
    )


def build_matrix(inputs: PrivateInputs) -> list[MatrixCell]:
    """Create one independent conversation for each possible pairing."""
    return [
        MatrixCell(profile_id=profile_id, dialogue=dialogue)
        for profile_id in inputs.profile_ids
        for dialogue in inputs.dialogues
    ]


def _load_catalog(
    inputs: PrivateInputs, recipe_path: Path
) -> tuple[DataCatalog, dict[int, dict[str, str]]]:
    """Load the source files through the production normalizers."""
    with recipe_path.open(encoding="gb18030", newline="") as stream:
        rows = dict(enumerate(csv.DictReader(stream), start=2))
    if len(rows) != EXPECTED_RECIPES:
        raise ValueError("Expected exactly 2000 source recipe rows")
    recipes = normalize_recipes(rows.values())
    raw_profiles = _read_array(inputs.profile_path, "Profiles")
    profiles = {profile.user_id: profile for profile in map(normalize_profile, raw_profiles)}
    if set(profiles) != set(inputs.profile_ids):
        raise ValueError("Profile normalization changed source IDs")
    return DataCatalog(profiles=profiles, recipes=recipes, quality_report={}), rows


def _validate_annotations(inputs: PrivateInputs) -> None:
    """Fail closed if source turns no longer match the reviewed annotation version."""
    if inputs.dialogue_sha256 != ANNOTATED_SOURCE_SHA256:
        raise ValueError("Dialogue source changed; review and version Intent annotations")
    if set(ANNOTATIONS) != {dialogue.case_id for dialogue in inputs.dialogues}:
        raise ValueError("Annotation case IDs do not match source dialogue IDs")
    for dialogue in inputs.dialogues:
        if len(ANNOTATIONS[dialogue.case_id]) != dialogue.turn_count:
            raise ValueError("Annotation turn count does not match source dialogue")


def validate_official_shape(inputs: PrivateInputs) -> None:
    """Reject an incomplete private input bundle before claiming matrix coverage."""
    if len(inputs.profile_ids) != EXPECTED_PROFILES:
        raise ValueError("Expected exactly 50 unique source profiles")
    if len(inputs.dialogues) != EXPECTED_DIALOGUES:
        raise ValueError("Expected exactly 20 source dialogues")
    if sum(case.turn_count for case in inputs.dialogues) != EXPECTED_DIALOGUE_TURNS:
        raise ValueError("Expected exactly 29 source dialogue turns")


async def _run_cell(
    cell: MatrixCell,
    agent: MealAgent,
    store: SessionStore,
    rows: dict[int, dict[str, str]],
    run_id: str,
) -> CellResult:
    """Replay one cell, capturing only code-level findings."""
    started = perf_counter()
    profile = agent.catalog.profiles[cell.profile_id]
    fixture = FixtureLLM(ANNOTATIONS[cell.dialogue.case_id])
    agent.llm = fixture
    session_id: str | None = None
    statuses: Counter[str] = Counter()
    failures: set[str] = set()
    completed = 0
    menu_observations = 0
    source_checks = 0
    for turn_number, message in enumerate(cell.dialogue.messages, 1):
        request_id = f"private-{run_id}-{cell.session_key}-{turn_number}"
        try:
            response = await agent.chat(
                cell.profile_id, message, session_id=session_id, request_id=request_id
            )
        except Exception:
            failures.add("execution_error")
            break
        completed += 1
        statuses[response.status] += 1
        state = response.conversation_state
        if session_id is None:
            session_id = state.session_id
        if state.session_id != session_id or state.user_id != cell.profile_id:
            failures.add("session_identity")
        if state.revision != turn_number:
            failures.add("revision_mismatch")
        persisted = store.get(session_id, cell.profile_id)
        if persisted is None or persisted.model_dump() != state.model_dump():
            failures.add("state_persistence")
        if not set(profile.allergies).issubset(state.constraints.allergies):
            failures.add("profile_allergy_lost")
        if not set(profile.health_goals).issubset(state.constraints.health_goals):
            failures.add("profile_health_goal_lost")
        if response.status == "ok":
            menu_observations += 1
            ids = [item.recipe_id for item in response.menu]
            if (
                not ids
                or not state.menu_valid
                or ids != state.menu_ids
                or len(ids) != len(set(ids))
                or len(ids) != state.constraints.dish_count
            ):
                failures.add("menu_state_mismatch")
            if not response.diner_suitability or any(
                not diner.hard_constraints_satisfied or diner.violations
                for diner in response.diner_suitability
            ):
                failures.add("diner_constraint_violation")
            if response.nutrition_analysis is None or response.nutrition_analysis.recipe_ids != ids:
                failures.add("nutrition_menu_mismatch")
            for item in [*response.menu, *response.replacement_suggestions]:
                source_checks += 1
                recipe = agent.catalog.recipes.get(item.recipe_id)
                if recipe is None or not check_source(recipe, item, rows):
                    failures.add("recipe_source_mismatch")
                    continue
                row = rows[recipe.source_row]
                source_text = row["食材清单"] + row["烹饪步骤"]
                oracle = check_allergens(
                    source_text,
                    state.constraints.allergies,
                    state.constraints.no_spicy,
                )
                failures.update(oracle["failures"])
                if oracle["unknown_allergy_values"]:
                    failures.add("allergen_oracle_incomplete")
                if any(
                    excluded and excluded in source_text
                    for excluded in state.constraints.excluded_ingredients
                ):
                    failures.add("excluded_ingredient_in_source")
        elif response.menu or state.menu_valid:
            failures.add("unresolved_exposed_menu")
    if fixture.intents or completed != cell.dialogue.turn_count:
        failures.add("incomplete_replay")
    await fixture.aclose()
    return CellResult(
        profile_id=cell.profile_id,
        dialogue_case_id=cell.dialogue.case_id,
        expected_turns=cell.dialogue.turn_count,
        completed_turns=completed,
        status_counts=dict(sorted(statuses.items())),
        menu_observations=menu_observations,
        source_checks=source_checks,
        elapsed_ms=round((perf_counter() - started) * 1000, 3),
        failures=tuple(sorted(failures)),
    )


def summarize_results(
    results: list[CellResult],
    *,
    expected_profiles: int,
    expected_dialogues: int,
    expected_turns: int,
) -> dict[str, Any]:
    """Compute coverage and diagnostics without presenting an official score."""
    pairs = {(cell.profile_id, cell.dialogue_case_id) for cell in results}
    statuses = Counter(
        {status: 0 for status in ("ok", "clarification_required", "no_feasible_menu")}
    )
    codes: Counter[str] = Counter()
    for cell in results:
        statuses.update(cell.status_counts)
        codes.update(cell.failures)
    return {
        "profiles": expected_profiles,
        "dialogues": expected_dialogues,
        "matrix_sessions": len(results),
        "expected_turns": expected_turns,
        "completed_turns": sum(cell.completed_turns for cell in results),
        "coverage_complete": (
            len(results) == expected_profiles * expected_dialogues
            and len(pairs) == len(results)
            and sum(cell.completed_turns for cell in results) == expected_turns
        ),
        "cells_with_failures": sum(bool(cell.failures) for cell in results),
        "menu_observations": sum(cell.menu_observations for cell in results),
        "source_checks": sum(cell.source_checks for cell in results),
        "status_counts": dict(sorted(statuses.items())),
        "failure_codes": dict(sorted(codes.items())),
    }


def _validate_report(report: dict[str, Any]) -> None:
    """Refuse unexpected fields and free-text values before any file is written."""
    if set(report) - _TOP_FIELDS:
        raise ValueError("Report contains private field")
    if report.get("report_schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported private report schema")
    if (
        report.get("data_scope") != "private_local_only"
        or report.get("binding_mode") != BINDING_MODE
    ):
        raise ValueError("Private report scope or binding mode is invalid")
    for field in ("started_at", "finished_at"):
        if field in report:
            try:
                datetime.fromisoformat(report[field])
            except (TypeError, ValueError) as error:
                raise ValueError("Report contains private field") from error
    if "git_commit" in report and not re.fullmatch(r"[0-9a-f]{40}|unknown", report["git_commit"]):
        raise ValueError("Report contains private field")
    sources = report.get("sources", {})
    if set(sources) != {"profiles", "dialogues", "recipes"}:
        raise ValueError("Private report sources are incomplete")
    for source in sources.values():
        if set(source) != {"sha256"}:
            raise ValueError("Report contains private field")
        if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            raise ValueError("Invalid source hash")
    summary = report.get("summary", {})
    if set(summary) - _SUMMARY_FIELDS:
        raise ValueError("Report contains private field")
    count_fields = _SUMMARY_FIELDS - {
        "coverage_complete",
        "status_counts",
        "failure_codes",
        "local_elapsed_seconds",
    }
    if any(
        key in summary and (type(summary[key]) is not int or summary[key] < 0)
        for key in count_fields
    ):
        raise ValueError("Report contains private field")
    if "coverage_complete" in summary and type(summary["coverage_complete"]) is not bool:
        raise ValueError("Report contains private field")
    if "local_elapsed_seconds" in summary and (
        type(summary["local_elapsed_seconds"]) not in (int, float)
        or not isfinite(summary["local_elapsed_seconds"])
        or summary["local_elapsed_seconds"] < 0
    ):
        raise ValueError("Report contains private field")
    if set(summary.get("status_counts", {})) - _STATUS_CODES:
        raise ValueError("Report contains private field")
    if set(summary.get("failure_codes", {})) - _FAILURE_CODES:
        raise ValueError("Report contains private field")
    if any(
        type(value) is not int or value < 0
        for counts in (summary.get("status_counts", {}), summary.get("failure_codes", {}))
        for value in counts.values()
    ):
        raise ValueError("Report contains private field")
    if any(item not in _LIMITATION_CODES for item in report.get("limitations", [])):
        raise ValueError("Report contains private field")
    for cell in report.get("cells", []):
        if set(cell) - _CELL_FIELDS:
            raise ValueError("Report contains private field")
        if type(cell.get("profile_id")) is not int or type(cell.get("dialogue_case_id")) is not int:
            raise ValueError("Private report requires numeric source IDs")
        if any(
            type(cell.get(key)) is not int or cell[key] < 0
            for key in ("expected_turns", "completed_turns", "menu_observations", "source_checks")
        ):
            raise ValueError("Report contains private field")
        if (
            type(cell.get("elapsed_ms")) not in (int, float)
            or not isfinite(cell["elapsed_ms"])
            or cell["elapsed_ms"] < 0
        ):
            raise ValueError("Report contains private field")
        if set(cell.get("status_counts", {})) - _STATUS_CODES:
            raise ValueError("Report contains private field")
        if any(
            type(value) is not int or value < 0 for value in cell.get("status_counts", {}).values()
        ):
            raise ValueError("Report contains private field")
        if set(cell.get("failures", [])) - _FAILURE_CODES:
            raise ValueError("Report contains private field")


def _render_markdown(report: dict[str, Any]) -> str:
    """Render a compact matrix report using only allowlisted diagnostics."""
    summary = report["summary"]
    lines = [
        "# 私有数据矩阵回放报告",
        "",
        "> 本报告是本地工程诊断，不是官方评分。源对话没有绑定用户，采用 "
        "`matrix_without_source_binding` 全组合覆盖。",
        "",
        f"- 档案：{summary['profiles']}；对话：{summary['dialogues']}；"
        f"独立会话：{summary['matrix_sessions']}",
        f"- 完成轮次：{summary['completed_turns']}/{summary['expected_turns']}",
        f"- 完整覆盖：{'是' if summary['coverage_complete'] else '否'}",
        f"- 有失败代码的会话：{summary['cells_with_failures']}",
        f"- 有菜单的响应：{summary['menu_observations']}；菜谱来源核验：{summary['source_checks']}",
        f"- 本地耗时：{summary.get('local_elapsed_seconds', 0)} 秒"
        "（人工意图 fixture，不代表真实模型延迟）",
        "",
        "## 状态与失败代码",
        "",
        f"- 状态：`{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- 失败代码：`{json.dumps(summary.get('failure_codes', {}), ensure_ascii=False)}`",
        "",
        "## 逐会话",
        "",
        "| profile_id | case_id | 轮次 | 状态 | 菜单响应 | 来源核验 | 耗时 ms | 失败代码 |",
        "|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for cell in report["cells"]:
        statuses = ",".join(
            f"{key}:{value}" for key, value in sorted(cell["status_counts"].items())
        )
        lines.append(
            f"| {cell['profile_id']} | {cell['dialogue_case_id']} | "
            f"{cell['completed_turns']}/{cell['expected_turns']} | {statuses or '-'} | "
            f"{cell['menu_observations']} | {cell['source_checks']} | "
            f"{cell['elapsed_ms']} | {','.join(cell['failures']) or '-'} |"
        )
    lines.extend(["", "## 数据版本", "", "| 输入 | SHA-256 |", "|---|---|"])
    for role, source in report["sources"].items():
        lines.append(f"| {role} | `{source['sha256']}` |")
    lines.extend(
        [
            "",
            "未保存健康详情、对话原文或完整响应。过敏词表无法验证品牌配方、"
            "交叉接触；没有可靠份量时不能计算个人定量营养。",
            "逐条失败代码见 `failures.jsonl`。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_bundle(output_dir: Path, report: dict[str, Any]) -> dict[str, Path]:
    """Reuse the PR5 bundle writer with private allowlisting and failure records."""
    _validate_report(report)
    records = (
        {
            "profile_id": cell["profile_id"],
            "dialogue_case_id": cell["dialogue_case_id"],
            "failure_code": code,
        }
        for cell in report["cells"]
        for code in cell["failures"]
    )
    paths = write_bundle(
        output_dir,
        report,
        _render_markdown(report),
        records=records,
        records_name="failures.jsonl",
    )
    return {"json": paths["json"], "markdown": paths["markdown"], "failures": paths["records"]}


async def run_matrix(
    profile_path: Path,
    dialogue_path: Path,
    recipe_path: Path,
    *,
    database_path: Path,
) -> dict[str, Any]:
    """Run all source pairs and return a redacted report ready for writing."""
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    inputs = load_private_inputs(profile_path, dialogue_path)
    validate_official_shape(inputs)
    _validate_annotations(inputs)
    catalog, rows = _load_catalog(inputs, recipe_path)
    store = SessionStore(database_path)
    agent = MealAgent(catalog, store, FixtureLLM([]))
    run_id = uuid4().hex
    results = [await _run_cell(cell, agent, store, rows, run_id) for cell in build_matrix(inputs)]
    summary = summarize_results(
        results,
        expected_profiles=len(inputs.profile_ids),
        expected_dialogues=len(inputs.dialogues),
        expected_turns=len(inputs.profile_ids) * sum(case.turn_count for case in inputs.dialogues),
    )
    summary["local_elapsed_seconds"] = round(perf_counter() - started, 3)
    return {
        "report_schema_version": SCHEMA_VERSION,
        "data_scope": "private_local_only",
        "binding_mode": BINDING_MODE,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "sources": {
            "profiles": {"sha256": inputs.profile_sha256},
            "dialogues": {"sha256": inputs.dialogue_sha256},
            "recipes": {"sha256": _sha256(recipe_path)},
        },
        "summary": summary,
        "cells": [asdict(cell) | {"failures": list(cell.failures)} for cell in results],
        "limitations": sorted(_LIMITATION_CODES),
    }


def main(argv: list[str] | None = None) -> int:
    """Run private replay from explicitly supplied local source paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--dialogues", type=Path, required=True)
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("runtime/private_matrix_reports"))
    args = parser.parse_args(argv)
    run_dir = args.output_root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    report = asyncio.run(
        run_matrix(
            args.profiles,
            args.dialogues,
            args.recipes,
            database_path=run_dir / "sessions.sqlite3",
        )
    )
    paths = write_report_bundle(run_dir, report)
    print(
        json.dumps({"output_dir": str(run_dir), **report["summary"]}, ensure_ascii=False, indent=2)
    )
    print(f"Markdown report: {paths['markdown']}")
    return (
        0
        if report["summary"]["coverage_complete"] and not report["summary"]["cells_with_failures"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
