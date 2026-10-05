"""LLM intent contract and offline full-flow tests for revoke_exclusion.

These pin the LLM contract (field names, prompt, payload) and the deterministic
revocation chain end-to-end, using only fake providers — never a real model.
"""

import json
from pathlib import Path

import httpx
import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.domain.models import (
    Constraints,
    Intent,
    PendingRevokeExclusion,
    SessionState,
    UserProfile,
)
from app.infrastructure.llm.base import LLMOutputError
from app.infrastructure.llm.deepseek import DeepSeekLLM

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "configs" / "intent_prompt.txt"


@pytest.fixture
def profile():
    return UserProfile(
        data_scope="synthetic", user_id=1, age=30, sex="女", height_cm=160,
        weight_kg=55, bmi=21.5, allergies=["花生"], health_goals=["控糖"],
        preferences=["清淡"],
    )


@pytest.fixture
def state():
    return SessionState(
        session_id="test-session", user_id=1,
        constraints=Constraints(allergies=["花生"]),
        meal_constraints=Constraints(excluded_ingredients=["鸡蛋", "牛奶", "香菜"]),
    )


def completion(content, finish_reason="stop"):
    return {"choices": [{
        "finish_reason": finish_reason,
        "message": {
            "role": "assistant",
            "content": json.dumps(content, ensure_ascii=False)
            if isinstance(content, dict) else content,
        },
    }]}


def adapter_for(body, status=200):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    return DeepSeekLLM(api_key="fake-test-secret", client=client), client


# --- Prompt fixture: the contract must document the revoke fields and semantics. ---

def test_intent_prompt_documents_revoke_contract():
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    # Field names.
    for field in ("revoke_exclusions", "revoke_confirmed", "revoke_cancelled"):
        assert field in text
    assert "pending_revoke_exclusion" in text
    # Semantics: ordinary exclusions only, never hard constraints.
    assert "普通忌口" in text
    assert "绝不填入 revoke_exclusions" in text
    # Positive examples.
    assert "取消鸡蛋忌口" in text
    assert "把鸡蛋和牛奶从忌口里删掉" in text
    # Confirmation / denial signals.
    assert "确认" in text
    assert "算了" in text
    # Negative examples: question, conditional, and hard-constraint revocation.
    assert "可以取消鸡蛋忌口吗" in text
    assert "如果以后可以吃鸡蛋" in text
    assert "取消花生过敏" in text


# --- Adapter contract: fake provider JSON must survive strict parsing. ---

async def test_contract_accepts_ordinary_revoke_json(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "revoke_exclusions": ["鸡蛋", "牛奶"],
    }))
    async with client:
        intent = await adapter.parse("取消鸡蛋和牛奶的限制，其他保持不变", state, profile)
    assert intent.action == "plan"
    assert intent.revoke_exclusions == ["鸡蛋", "牛奶"]
    assert not intent.revoke_confirmed


async def test_contract_accepts_subject_revoke_via_diner_update(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan",
        "diner_updates": [{
            "diner": "我妈", "aliases": ["妈妈"], "revoke_exclusions": ["牛奶"],
        }],
    }))
    async with client:
        intent = await adapter.parse("取消我妈的牛奶忌口", state, profile)
    assert intent.diner_updates[0].diner == "我妈"
    assert intent.diner_updates[0].revoke_exclusions == ["牛奶"]


async def test_contract_rejects_conflicting_confirm_and_cancel(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "revoke_confirmed": True, "revoke_cancelled": True,
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("确认取消", state, profile)


async def test_contract_still_rejects_unknown_fields_alongside_revoke(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "revoke_exclusions": ["鸡蛋"], "delete_all_constraints": True,
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("取消鸡蛋", state, profile)


async def test_parse_sends_pending_revoke_context(profile, state):
    state.pending_revoke_exclusion = PendingRevokeExclusion(targets=["鸡蛋"])
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=completion({
            "action": "plan", "revoke_confirmed": True,
        }))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        intent = await adapter.parse("确认", state, profile)

    assert intent.revoke_confirmed is True
    context = json.loads(json.loads(observed[0].content)["messages"][1]["content"])
    assert context["pending_revoke_exclusion"] == {"subject": None, "targets": ["鸡蛋"]}


# --- Offline full-flow: fake LLM turns drive the deterministic state machine. ---

def test_synthetic_revoke_then_confirm(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋", "牛奶", "香菜"]),
        Intent(revoke_exclusions=["鸡蛋", "牛奶"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋、牛奶、香菜",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "取消鸡蛋和牛奶的限制，其他保持不变",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    assert first["status"] == "ok"
    assert first["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋", "牛奶", "香菜"]

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋", "牛奶"]
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋", "牛奶", "香菜"]

    assert third["status"] == "ok"
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == ["香菜"]
    assert "花生" in third["conversation_state"]["constraints"]["allergies"]
    assert third["conversation_state"]["pending_revoke_exclusion"] is None


def test_synthetic_revoke_then_deny(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋", "牛奶"]),
        Intent(revoke_exclusions=["鸡蛋"]),
        Intent(revoke_cancelled=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋和牛奶",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "取消鸡蛋",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "算了",
        }).json()

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋"]
    assert third["status"] == "ok"
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋", "牛奶"]
    assert third["conversation_state"]["pending_revoke_exclusion"] is None


def test_synthetic_allergy_revoke_is_protected(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([
        complete_intent(),
        Intent(revoke_exclusions=["花生"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "取消花生过敏",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    assert "花生" in third["conversation_state"]["constraints"]["allergies"]


def test_synthetic_question_does_not_revoke(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(action="clarify", clarification="请确认是否要取消鸡蛋忌口。"),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "可以取消鸡蛋忌口吗？",
        }).json()

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"] is None
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]


def test_question_revoke_request_does_not_land(tmp_path, catalog):
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
            "user_id": 3, "session_id": sid, "message": "可以取消鸡蛋忌口吗？",
        }).json()

    assert second["conversation_state"]["pending_revoke_exclusion"] is None
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]


def test_conditional_revoke_request_does_not_land(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋", "牛奶"]),
        Intent(revoke_exclusions=["鸡蛋"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，不吃鸡蛋和牛奶",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "如果以后可以吃鸡蛋，就取消鸡蛋忌口",
        }).json()

    assert second["conversation_state"]["pending_revoke_exclusion"] is None
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋", "牛奶"]
