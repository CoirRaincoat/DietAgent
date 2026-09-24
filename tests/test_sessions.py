import json
import sqlite3
from uuid import uuid4

import pytest

from app.domain.models import ChatResult, SessionState
from app.infrastructure.sessions import SessionConflict, SessionStore


def test_completed_request_replay_and_conflict(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    state = SessionState(session_id=uuid4().hex, user_id=3, revision=1)
    store.save(state, None)
    result = ChatResult(status="no_feasible_menu", reason="test", conversation_state=state)
    store.complete(result, 1, "r1", "hash1")
    assert store.replay(state.session_id, "r1", "hash1", 1).reason == "test"
    with pytest.raises(SessionConflict):
        store.replay(state.session_id, "r1", "hash2", 1)
    with pytest.raises(SessionConflict):
        store.replay(state.session_id, "r1", "hash1", 2)
    with pytest.raises(SessionConflict):
        store.get(state.session_id, 4)


def test_state_survives_restart_and_stale_update_fails(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    state = SessionState(session_id=uuid4().hex, user_id=3, revision=1)
    state.constraints.excluded_ingredients = ["花生"]
    SessionStore(path).save(state, None)
    restarted = SessionStore(path)
    loaded = restarted.get(state.session_id, 3)
    assert loaded.constraints.excluded_ingredients == ["花生"]
    loaded.revision = 2
    restarted.save(loaded, 1)
    with pytest.raises(SessionConflict):
        restarted.save(loaded, 1)


def test_legacy_snapshot_and_completed_request_default_to_empty_rejection_memory(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    store = SessionStore(path)
    state = SessionState(session_id=uuid4().hex, user_id=3, revision=1)
    result = ChatResult(status="no_feasible_menu", reason="legacy", conversation_state=state)
    snapshot = state.model_dump(exclude={"rejected_recipe_ids"})
    legacy_result = result.model_dump(exclude={"conversation_state": {"rejected_recipe_ids"}})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (state.session_id, 3, 1, json.dumps(snapshot)),
        )
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
            (state.session_id, "old", "hash", 1, json.dumps(legacy_result)),
        )
    assert store.get(state.session_id, 3).rejected_recipe_ids == []
    cached = store.replay(state.session_id, "old", "hash", 1)
    assert cached.conversation_state.rejected_recipe_ids == []
