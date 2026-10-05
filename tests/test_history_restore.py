"""Field-scoped constraint history restore (J17) tests.

Pins the record rule, resolver, apply semantics, intent contract and the
original-J17 four-turn flow using only fake providers — never a real model.
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.history_restore import (
    apply_constraint_restore,
    record_constraint_revision,
    resolve_constraint_restore,
)
from app.domain.models import (
    Constraints,
    Intent,
    PendingRevokeExclusion,
    RestoreConstraint,
    SessionState,
    UserProfile,
)
from app.infrastructure.llm.base import LLMOutputError
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "configs" / "intent_prompt.txt"


def _state(**kwargs):
    state = SessionState(
        session_id="s", user_id=1,
        constraints=Constraints(),
        meal_constraints=Constraints(dish_count=3),
    )
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def _restore_original():
    return Intent(
        restore_constraints=[RestoreConstraint(field="dish_count", reference="original")]
    )


# --- H1-H8 history / resolver / apply unit tests ---

def test_h1_resolve_original_value():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 5)
    assert resolve_constraint_restore(state.constraint_history, "dish_count", "original") == 3


def test_h2_resolve_original_across_multiple_changes():
    state = _state()
    for value in (3, 5, 4, 6):
        record_constraint_revision(state, "dish_count", value)
    assert resolve_constraint_restore(state.constraint_history, "dish_count", "original") == 3


def test_consecutive_duplicates_not_reappended():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 3)
    assert [r.value for r in state.constraint_history] == [3]


def test_h4_no_history_resolves_none():
    assert resolve_constraint_restore([], "dish_count", "original") is None


def test_h4_apply_without_history_clarifies_and_leaves_state():
    state = _state()
    issue = apply_constraint_restore(state, _restore_original())
    assert issue is not None
    assert state.meal_constraints.dish_count == 3  # unchanged


def test_h5_idempotent_when_current_equals_original():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 5)
    record_constraint_revision(state, "dish_count", 3)
    state.meal_constraints.dish_count = 3
    issue = apply_constraint_restore(state, _restore_original())
    assert issue is None
    assert state.meal_constraints.dish_count == 3


def test_h6_restore_preserves_allergies_and_goals():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 5)
    state.meal_constraints.allergies = ["海鲜"]
    state.meal_constraints.health_goals = ["控糖"]
    state.meal_constraints.excluded_ingredients = ["鸡蛋"]
    state.meal_constraints.dish_count = 5
    issue = apply_constraint_restore(state, _restore_original())
    assert issue is None
    assert state.meal_constraints.dish_count == 3
    assert state.meal_constraints.allergies == ["海鲜"]
    assert state.meal_constraints.health_goals == ["控糖"]
    assert state.meal_constraints.excluded_ingredients == ["鸡蛋"]


def test_h7_restore_preserves_pending_revoke():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 5)
    state.pending_revoke_exclusion = PendingRevokeExclusion(targets=["鸡蛋"])
    state.meal_constraints.dish_count = 5
    apply_constraint_restore(state, _restore_original())
    assert state.meal_constraints.dish_count == 3
    assert state.pending_revoke_exclusion is not None
    assert state.pending_revoke_exclusion.targets == ["鸡蛋"]


def test_h8_restore_preserves_rejected_ids():
    state = _state()
    record_constraint_revision(state, "dish_count", 3)
    record_constraint_revision(state, "dish_count", 5)
    state.rejected_recipe_ids = ["r1", "r2"]
    state.meal_constraints.dish_count = 5
    apply_constraint_restore(state, _restore_original())
    assert state.meal_constraints.dish_count == 3
    assert state.rejected_recipe_ids == ["r1", "r2"]


# --- Offline full-flow: original J17 four turns ---

def test_synthetic_j17_four_turn_flow(tmp_path, catalog):
    llm = ScriptedLLM([
        Intent(people=2, meal_type="午餐", dish_count=3, soup_count=0,
               excluded_ingredients=["猪肉"], restrictions_confirmed=True),
        Intent(dish_count=4),
        Intent(excluded_ingredients=["辣椒"]),
        _restore_original(),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        t1 = client.post("/chat", json={
            "user_id": 3, "message": "2个人吃午餐，总共3道菜，不要汤，不吃猪肉，没有过敏，也没有其他忌口。",
        }).json()
        sid = t1["conversation_state"]["session_id"]
        t2 = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "菜数改成4道，其他不变。",
        }).json()
        t3 = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "再加上不能放辣椒。",
        }).json()
        t4 = client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "菜数恢复成我最开始说的数量，后来追加的不放辣椒继续保留，其他要求也不变。",
        }).json()

    assert t1["status"] == "ok"
    assert t1["conversation_state"]["constraints"]["dish_count"] == 3
    assert t2["conversation_state"]["constraints"]["dish_count"] == 4
    assert t3["conversation_state"]["constraints"]["dish_count"] == 4
    assert "辣椒" in t3["conversation_state"]["constraints"]["excluded_ingredients"]

    assert t4["status"] == "ok"
    c4 = t4["conversation_state"]["constraints"]
    assert c4["dish_count"] == 3
    assert c4["people"] == 2
    assert c4["meal_type"] == "午餐"
    assert c4["soup_count"] == 0
    assert "猪肉" in c4["excluded_ingredients"]
    assert "辣椒" in c4["excluded_ingredients"]


def test_restore_without_history_clarifies_full_flow(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), _restore_original()])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "恢复最初菜数",
        }).json()

    # No explicit dish_count was ever set, so history is empty; state unchanged.
    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["constraints"]["dish_count"] == first["conversation_state"]["constraints"]["dish_count"]


# --- Persistence and backward compatibility ---

def test_restore_survives_restart(tmp_path, catalog):
    first_llm = ScriptedLLM([complete_intent(dish_count=3), Intent(dish_count=5)])
    with client_for(tmp_path, catalog, first_llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "改成5道菜",
        }).json()

    second_llm = ScriptedLLM([_restore_original()])
    with client_for(tmp_path, catalog, second_llm) as client:
        restored = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "恢复最初菜数",
        }).json()

    assert restored["status"] == "ok"
    assert restored["conversation_state"]["constraints"]["dish_count"] == 3


def test_legacy_snapshot_defaults_to_empty_constraint_history(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    state = SessionState(session_id=uuid4().hex, user_id=3, revision=1)
    snapshot = state.model_dump(exclude={"constraint_history"})
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (state.session_id, 3, 1, json.dumps(snapshot)),
        )
    assert store.get(state.session_id, 3).constraint_history == []


# --- Idempotency ---

def test_restore_request_replay_is_idempotent(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(dish_count=3),
        Intent(dish_count=5),
        _restore_original(),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "改成5道菜",
        }).json()
        body = {"user_id": 3, "session_id": sid, "request_id": "req-restore-1",
                "message": "恢复最初菜数"}
        once = client.post("/chat", json=body).json()
        twice = client.post("/chat", json=body).json()

    assert once["conversation_state"]["constraints"]["dish_count"] == 3
    assert twice["conversation_state"]["constraints"]["dish_count"] == 3
    history_once = once["conversation_state"]["constraint_history"]
    history_twice = twice["conversation_state"]["constraint_history"]
    assert [r["value"] for r in history_once] == [3, 5]
    assert history_once == history_twice


# --- Intent contract: prompt and adapter ---

def completion(content):
    return {"choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
    }]}


def adapter_for(body):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    return DeepSeekLLM(api_key="fake-test-secret", client=client), client


@pytest.fixture
def profile():
    return UserProfile(
        data_scope="synthetic", user_id=1, age=30, sex="女", height_cm=160,
        weight_kg=55, bmi=21.5, allergies=["花生"],
    )


@pytest.fixture
def state():
    return SessionState(
        session_id="test-session", user_id=1,
        constraints=Constraints(), meal_constraints=Constraints(dish_count=5),
    )


def test_intent_prompt_documents_restore_contract():
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "restore_constraints" in text
    assert "dish_count" in text
    assert "original" in text
    assert "恢复最初的菜数" in text
    assert "恢复之前的菜单" in text


async def test_contract_accepts_restore_json(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan",
        "restore_constraints": [{"field": "dish_count", "reference": "original"}],
    }))
    async with client:
        intent = await adapter.parse("恢复最初的菜数", state, profile)
    assert intent.restore_constraints == [
        RestoreConstraint(field="dish_count", reference="original")
    ]


async def test_contract_rejects_restore_with_explicit_dish_count(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "dish_count": 4,
        "restore_constraints": [{"field": "dish_count", "reference": "original"}],
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("菜数恢复成最初，同时改成4道", state, profile)


async def test_contract_rejects_unknown_restore_field(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan",
        "restore_constraints": [{"field": "soup_count", "reference": "original"}],
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("恢复最初的汤数", state, profile)
