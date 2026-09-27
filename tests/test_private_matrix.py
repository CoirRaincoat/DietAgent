"""Private profile/dialogue matrix harness contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.private_matrix as private_matrix
from evaluation.private_matrix import (
    CellResult,
    build_matrix,
    load_private_inputs,
    summarize_results,
    validate_official_shape,
    write_report_bundle,
)


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _profiles(path: Path, ids: list[int]) -> Path:
    return _write_json(path, [{"id": profile_id} for profile_id in ids])


def _dialogues(path: Path) -> Path:
    return _write_json(
        path,
        [
            {"id": 1, "turn_count": 1, "user_messages": ["第一组私有文本"]},
            {"id": 2, "turn_count": 2, "user_messages": ["第二组私有文本", "继续"]},
        ],
    )


def test_loader_builds_profile_by_dialogue_cartesian_matrix(tmp_path: Path) -> None:
    inputs = load_private_inputs(
        _profiles(tmp_path / "profiles.json", [101, 202]),
        _dialogues(tmp_path / "dialogues.json"),
    )

    cells = build_matrix(inputs)

    assert inputs.binding_mode == "matrix_without_source_binding"
    assert [(cell.profile_id, cell.dialogue.case_id) for cell in cells] == [
        (101, 1),
        (101, 2),
        (202, 1),
        (202, 2),
    ]
    assert len({cell.session_key for cell in cells}) == 4
    assert sum(cell.dialogue.turn_count for cell in cells) == 6


def test_loader_rejects_duplicate_profiles_and_turn_mismatch(tmp_path: Path) -> None:
    duplicate_profiles = _profiles(tmp_path / "duplicate.json", [101, 101])
    dialogues = _dialogues(tmp_path / "dialogues.json")

    with pytest.raises(ValueError, match="Duplicate profile ID"):
        load_private_inputs(duplicate_profiles, dialogues)

    malformed = _write_json(
        tmp_path / "malformed.json",
        [{"id": 1, "turn_count": 2, "user_messages": ["only one"]}],
    )
    with pytest.raises(ValueError, match="turn_count"):
        load_private_inputs(_profiles(tmp_path / "profiles.json", [101]), malformed)


def test_loader_rejects_partial_or_present_source_bindings(tmp_path: Path) -> None:
    profiles = _profiles(tmp_path / "profiles.json", [101, 202])
    bound_dialogues = _write_json(
        tmp_path / "bound.json",
        [{"id": 1, "user_id": 101, "turn_count": 1, "user_messages": ["private"]}],
    )

    with pytest.raises(ValueError, match="already contains user_id"):
        load_private_inputs(profiles, bound_dialogues)


def test_official_shape_rejects_partial_profile_input(tmp_path: Path) -> None:
    inputs = load_private_inputs(
        _profiles(tmp_path / "profiles.json", [101, 202]),
        _dialogues(tmp_path / "dialogues.json"),
    )
    with pytest.raises(ValueError, match="50 unique"):
        validate_official_shape(inputs)


def test_summary_proves_complete_matrix_coverage() -> None:
    results = [
        CellResult(
            profile_id=profile_id,
            dialogue_case_id=case_id,
            expected_turns=turns,
            completed_turns=turns,
            status_counts={"ok": turns},
            menu_observations=turns,
            source_checks=turns * 3,
            elapsed_ms=10.0,
            failures=(),
        )
        for profile_id in range(1, 51)
        for case_id, turns in ((1, 1), (2, 2))
    ]

    summary = summarize_results(
        results,
        expected_profiles=50,
        expected_dialogues=2,
        expected_turns=150,
    )

    assert summary["matrix_sessions"] == 100
    assert summary["completed_turns"] == 150
    assert summary["coverage_complete"] is True
    assert summary["cells_with_failures"] == 0


def test_each_matrix_cell_starts_without_a_prior_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second profile must not inherit the first profile's conversation."""
    monkeypatch.setattr(
        private_matrix, "ANNOTATIONS", {1: [{"action": "clarify", "clarification": "请补充。"}]}
    )

    class State:
        def __init__(self, session_id: str, user_id: int) -> None:
            self.session_id = session_id
            self.user_id = user_id
            self.revision = 1
            self.constraints = SimpleNamespace(allergies=[], health_goals=[])
            self.menu_valid = False

        def model_dump(self) -> dict[str, object]:
            return {"session_id": self.session_id, "user_id": self.user_id, "revision": 1}

    class Store:
        def __init__(self) -> None:
            self.states: dict[str, State] = {}

        def get(self, session_id: str, user_id: int) -> State | None:
            state = self.states.get(session_id)
            return state if state and state.user_id == user_id else None

    class Agent:
        def __init__(self, store: Store) -> None:
            self.catalog = SimpleNamespace(
                profiles={
                    101: SimpleNamespace(allergies=[], health_goals=[]),
                    202: SimpleNamespace(allergies=[], health_goals=[]),
                }
            )
            self.store = store
            self.llm = None
            self.calls: list[tuple[int, str | None, str]] = []

        async def chat(
            self, user_id: int, message: str, session_id: str | None, request_id: str
        ) -> SimpleNamespace:
            self.calls.append((user_id, session_id, request_id))
            self.llm.intents.popleft()
            state = State(f"session-{request_id}", user_id)
            self.store.states[state.session_id] = state
            return SimpleNamespace(
                status="clarification_required", conversation_state=state, menu=[]
            )

    store = Store()
    agent = Agent(store)
    dialogue = private_matrix.Dialogue(case_id=1, messages=("private",))
    first = asyncio.run(
        private_matrix._run_cell(private_matrix.MatrixCell(101, dialogue), agent, store, {}, "run")
    )
    second = asyncio.run(
        private_matrix._run_cell(private_matrix.MatrixCell(202, dialogue), agent, store, {}, "run")
    )

    assert [call[1] for call in agent.calls] == [None, None]
    assert agent.calls[0][2] != agent.calls[1][2]
    assert first.failures == second.failures == ()


def test_report_bundle_is_redacted_and_rejects_private_payloads(tmp_path: Path) -> None:
    report = {
        "report_schema_version": "private-matrix.v1",
        "data_scope": "private_local_only",
        "binding_mode": "matrix_without_source_binding",
        "sources": {
            "profiles": {"sha256": "a" * 64},
            "dialogues": {"sha256": "b" * 64},
            "recipes": {"sha256": "c" * 64},
        },
        "summary": {
            "profiles": 1,
            "dialogues": 1,
            "matrix_sessions": 1,
            "expected_turns": 1,
            "completed_turns": 1,
            "coverage_complete": True,
            "cells_with_failures": 0,
            "status_counts": {"ok": 1},
        },
        "cells": [
            {
                "profile_id": 101,
                "dialogue_case_id": 1,
                "expected_turns": 1,
                "completed_turns": 1,
                "status_counts": {"ok": 1},
                "menu_observations": 1,
                "source_checks": 3,
                "elapsed_ms": 10.0,
                "failures": [],
            }
        ],
    }

    paths = write_report_bundle(tmp_path, report)

    assert set(paths) == {"json", "markdown", "failures"}
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "matrix_without_source_binding" in markdown
    assert "101" in markdown
    assert "第一组私有文本" not in markdown
    assert paths["failures"].read_text(encoding="utf-8") == ""

    leaked = report | {"user_messages": ["must not be written"]}
    with pytest.raises(ValueError, match="private field"):
        write_report_bundle(tmp_path / "leaked", leaked)

    nested_leak = report | {"cells": [{**report["cells"][0], "health_goals": ["private"]}]}
    with pytest.raises(ValueError, match="private field"):
        write_report_bundle(tmp_path / "nested", nested_leak)

    encoded_leak = report | {"cells": [{**report["cells"][0], "failures": ["allergy_peanut"]}]}
    with pytest.raises(ValueError, match="private field"):
        write_report_bundle(tmp_path / "encoded", encoded_leak)

    value_leak = report | {"summary": {**report["summary"], "matrix_sessions": "private"}}
    with pytest.raises(ValueError, match="private field"):
        write_report_bundle(tmp_path / "value", value_leak)
