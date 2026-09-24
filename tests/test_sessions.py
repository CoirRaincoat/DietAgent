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
