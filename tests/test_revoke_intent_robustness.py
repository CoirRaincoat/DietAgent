"""Revoke-intent paraphrase robustness tests (offline only).

These pin, without any real model, that the intent contract documents the
paraphrase families (P1-P8 positive, N1-N6 negative) and that the
deterministic state machine stays precise and safe for partial, state-change,
conditional and nonexistent-target revokes.
"""

from pathlib import Path

import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.domain.models import Intent

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "configs" / "intent_prompt.txt"


# --- Prompt fixture: the contract must document each paraphrase family. ---

@pytest.mark.parametrize("family,markers", [
    # P1 direct revocation.
    ("P1 direct", ["取消鸡蛋忌口", "去掉牛奶限制", "把鸡蛋和牛奶从忌口里删掉"]),
    # P2 historical reference (the R1 failure mode).
    ("P2 historical reference", ["取消刚才", "把之前的鸡蛋限制取消"]),
    # P3 state change ("now I can eat it").
    ("P3 state change", ["我现在可以吃鸡蛋了", "不用再避开"]),
    # P4 correction of an earlier statement.
    ("P4 correction", ["刚才说错了"]),
    # P5 mixed preserve (revoke one, keep another).
    ("P5 mixed preserve", ["继续不吃"]),
    # P8 partial revoke (only the named target, no over-delete).
    ("P8 partial revoke", ["只取消鸡蛋", "不得多删"]),
    # The general semantic rule that makes long-form (P6/P7) work.
    ("P6/P7 semantic rule", ["不再适用"]),
])
def test_intent_prompt_documents_positive_paraphrase_families(family, markers):
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    for marker in markers:
        assert marker in text, f"{family}: missing {marker!r} in intent_prompt.txt"


@pytest.mark.parametrize("family,markers", [
    # N1 conditional.
    ("N1 conditional", ["如果以后可以吃鸡蛋"]),
    # N2 question.
    ("N2 question", ["可以取消鸡蛋忌口吗"]),
    # N3 example / quoted reference.
    ("N3 example/reference", ["只是举例"]),
    # N4 allergy / health hard constraints never revoke.
    ("N4 allergy/health", ["取消花生过敏", "糖尿病饮食限制不要了"]),
    # N5 nonexistent target.
    ("N5 nonexistent target", ["不在当前 excluded_ingredients"]),
    # N6 a mere preference is not a revoke.
    ("N6 preference", ["单纯想吃"]),
    # Hard-constraint exclusion rule.
    ("hard-constraint rule", ["绝不填入 revoke_exclusions"]),
])
def test_intent_prompt_documents_negative_families(family, markers):
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    for marker in markers:
        assert marker in text, f"{family}: missing {marker!r} in intent_prompt.txt"


# --- Offline full-flow: fake LLM turns drive the deterministic state machine. ---

def test_synthetic_historical_revoke_enters_pending_only(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "取消刚才“不吃鸡蛋”这一条，我愿意吃鸡蛋了；海鲜和花生过敏仍然保持，其他要求不变。",
        }).json()

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋"]
    # Egg stays until confirmation; allergies are untouched.
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]
    assert "花生" in second["conversation_state"]["constraints"]["allergies"]


def test_synthetic_historical_revoke_confirm_removes_only_egg(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "取消刚才“不吃鸡蛋”这一条，我愿意吃鸡蛋了；其他要求不变。",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    assert third["status"] == "ok"
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == []
    assert "花生" in third["conversation_state"]["constraints"]["allergies"]
    assert third["conversation_state"]["pending_revoke_exclusion"] is None


def test_synthetic_state_change_revoke_enters_pending(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "我现在可以吃鸡蛋了",
        }).json()

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋"]
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]


def test_synthetic_partial_revoke_targets_only_named(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋", "牛奶"]),
        Intent(revoke_exclusions=["鸡蛋"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋和牛奶",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "鸡蛋可以恢复，牛奶继续不吃",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    # Only 鸡蛋 is targeted; 牛奶 must not enter the pending targets.
    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋"]
    # After confirm, only 鸡蛋 is removed and 牛奶 is preserved.
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == ["牛奶"]


def test_synthetic_conditional_revoke_does_not_land(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "如果以后可以吃鸡蛋，就取消鸡蛋忌口",
        }).json()

    assert second["conversation_state"]["pending_revoke_exclusion"] is None
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]


def test_synthetic_example_reference_revoke_does_not_land(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "“取消鸡蛋忌口”只是举个例子",
        }).json()

    assert second["conversation_state"]["pending_revoke_exclusion"] is None
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]


def test_synthetic_nonexistent_target_confirm_removes_nothing(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["牛奶"]),
        Intent(revoke_exclusions=["鸡蛋"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃牛奶",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "鸡蛋现在可以吃了",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    # No existing 鸡蛋 exclusion: nothing is fabricated or removed.
    assert "未找到" in second["reason"]
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == ["牛奶"]
    assert third["conversation_state"]["pending_revoke_exclusion"] is None
