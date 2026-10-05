"""R3 uses hand-authored provider envelopes, MockTransport and temporary SQLite.

These fixtures reproduce business and protocol paths, not a captured provider
response or an estimate of real-model parsing accuracy.
"""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import ChatResult, Ingredient, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import LLMOutputError
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings


def synthetic_profile():
    return UserProfile(
        data_scope="synthetic", user_id=900003, age=30, sex="女",
        height_cm=165, weight_kg=55, bmi=20.2, allergies=["花生"],
    )


def envelope(content, finish_reason="stop"):
    return {"choices": [{
        "finish_reason": finish_reason,
        "message": {"content": json.dumps(content, ensure_ascii=False)
                    if isinstance(content, dict) else content},
    }]}


def provider(intents):
    remaining = list(intents)
    observed = []

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        observed.append(payload)
        if "facts" in payload:
            return httpx.Response(200, json=envelope({"reason_ids": list(payload["facts"])}))
        assert remaining, "Unexpected additional parse call"
        return httpx.Response(200, json=envelope(remaining.pop(0)))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DeepSeekLLM("synthetic-key", client=client), client, observed


def synthetic_catalog():
    # Independently supplied sufficient safe candidates; none are source-data claims.
    records = [
        ("合成蒸蛋", "鸡蛋", "protein"), ("合成蒸鸡", "鸡肉", "protein"),
        ("合成青菜", "青菜", "vegetable"), ("合成南瓜", "南瓜", "vegetable"),
        ("合成米饭", "大米", "staple"), ("合成小米饭", "小米", "staple"),
        ("合成萝卜汤", "萝卜", "soup"), ("合成冬瓜汤", "冬瓜", "soup"),
    ]
    recipes = {}
    for index, (name, ingredient, category) in enumerate(records):
        key = f"synthetic-counts-{index}"
        recipes[key] = Recipe(
            recipe_id=key, name=name, raw_ingredients=ingredient + "；水",
            ingredients=[Ingredient(raw=value, name=value) for value in [ingredient, "水"]],
            steps="将食材和水蒸熟。", categories=[category], methods=["蒸"],
            meal_types=["晚餐"], source_row=index + 2, fingerprint=key,
        )
    profile = synthetic_profile()
    return DataCatalog(recipes=recipes, profiles={profile.user_id: profile}, quality_report={})


def count_intent(action="plan", dish_count=2, soup_count=3):
    result = {
        "action": action, "people": 2, "meal_type": "晚餐",
        "dish_count": dish_count, "soup_count": soup_count, "restrictions_confirmed": True,
    }
    if action == "clarify":
        result["clarification"] = "2道菜却要求3道汤，请确认总菜数和汤数。"
    return result


@pytest.mark.parametrize("action", ["plan", "clarify"])
@pytest.mark.parametrize("dish_count,soup_count", [(2, 3), (1, 2)])
async def test_adapter_retains_well_formed_business_conflict(action, dish_count, soup_count):
    # (1, 2) migrates the former test_llm protocol rejection to the new business contract.
    adapter, client, observed = provider([count_intent(action, dish_count, soup_count)])
    async with client:
        intent = await adapter.parse(
            f"2人晚餐，没有其他忌口，要{dish_count}道菜其中{soup_count}道汤",
            SessionState(session_id="a" * 32, user_id=900003), synthetic_profile(),
        )
    assert (intent.dish_count, intent.soup_count) == (dish_count, soup_count)
    assert intent.action == action
    assert len(observed) == 1


@pytest.mark.parametrize("action", ["plan", "clarify"])
def test_api_business_conflict_persists_then_recovers(tmp_path, action):
    adapter, _, observed = provider([count_intent(action), count_intent(dish_count=4, soup_count=1)])
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "isolated.db")
    store = SessionStore(settings.database_path)
    with TestClient(create_app(settings, adapter, synthetic_catalog(), store)) as client:
        first = client.post("/chat", json={
            "user_id": 900003, "message": "2人晚餐，没有其他忌口，要2道菜其中3道汤",
        })
        assert first.status_code == 200
        first = first.json()
        assert first["status"] == "clarification_required"
        state = first["conversation_state"]
        assert state["pending_menu_counts"] == {"dish_count": 2, "soup_count": 3}
        assert state["constraints"]["soup_count"] <= state["constraints"]["dish_count"]
        assert {"people", "meal_type", "restrictions"} <= set(state["confirmed_fields"])
        assert state["constraints"]["people"] == 2
        assert state["constraints"]["meal_type"] == "晚餐"
        assert state["constraints"]["allergies"] == ["花生"]
        assert not state["menu_valid"] and not first["menu"]
        assert first["clarification_questions"] == [{
            "field": "request", "prompt": first["reason"], "options": [],
        }]
        assert "2" in first["reason"] and "3" in first["reason"]
        persisted = store.get(state["session_id"], 900003)
        assert persisted.pending_menu_counts.model_dump() == state["pending_menu_counts"]
        second = client.post("/chat", json={
            "user_id": 900003, "session_id": state["session_id"],
            "message": "纠正为2人晚餐，共4道菜其中1道汤，没有其他忌口",
        })
    assert second.status_code == 200
    second = second.json()
    assert second["status"] == "ok" and len(second["menu"]) == 4
    assert sum("汤" in item["name"] for item in second["menu"]) == 1
    assert second["conversation_state"]["pending_menu_counts"] is None
    assert second["conversation_state"]["menu_valid"]
    assert second["conversation_state"]["revision"] == 2
    assert second["conversation_state"]["constraints"]["allergies"] == ["花生"]
    assert not second["clarification_questions"]
    assert observed[1]["pending_menu_counts"] == {"dish_count": 2, "soup_count": 3}


def test_old_valid_menu_is_history_during_conflict_and_partial_correction(tmp_path):
    adapter, _, _ = provider([
        count_intent(dish_count=4, soup_count=1), count_intent(),
        {"action": "plan"}, {"action": "plan", "soup_count": 1},
    ])
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "isolated.db")
    with TestClient(create_app(settings, adapter, synthetic_catalog())) as client:
        first = client.post("/chat", json={
            "user_id": 900003, "message": "2人晚餐，4菜1汤，没有其他忌口",
        }).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        old_ids = [item["recipe_id"] for item in first["menu"]]
        for message in ["改成2道菜其中3道汤", "继续安排"]:
            response = client.post("/chat", json={
                "user_id": 900003, "session_id": sid, "message": message,
            })
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == "clarification_required" and result["menu"] == []
            assert result["conversation_state"]["menu_ids"] == old_ids
            assert not result["conversation_state"]["menu_valid"]
            assert result["conversation_state"]["constraints"]["dish_count"] == 4
            assert result["conversation_state"]["constraints"]["soup_count"] == 1
            assert result["conversation_state"]["pending_menu_counts"] == {
                "dish_count": 2, "soup_count": 3,
            }
        corrected = client.post("/chat", json={
            "user_id": 900003, "session_id": sid, "message": "那就只要1道汤",
        }).json()
    assert corrected["status"] == "ok" and len(corrected["menu"]) == 2
    assert corrected["conversation_state"]["constraints"]["soup_count"] == 1
    assert corrected["conversation_state"]["pending_menu_counts"] is None


@pytest.mark.parametrize("content,field,category", [
    ('{"action":"plan",', "content", "invalid_json"),
    ('{"action":"plan","action":"clarify"}', "content", "duplicate_key"),
    ({}, "action", "missing_field"),
    ({"action": "invented"}, "action", "invalid_action"),
    ({"action": "plan", "dish_count": "2"}, "dish_count", "invalid_type"),
    ({"action": "plan", "dish_count": True}, "dish_count", "invalid_type"),
    ({"action": "plan", "soup_count": 4}, "soup_count", "out_of_range"),
    ({"action": "plan", "dish_count": 9}, "dish_count", "out_of_range"),
    ({"action": "plan", "secret-field": "secret-value"}, "unknown", "unknown_field"),
])
async def test_invalid_structure_remains_rejected_with_safe_diagnostics(content, field, category):
    adapter, client, observed = provider([content])
    async with client:
        with pytest.raises(LLMOutputError) as caught:
            await adapter.parse(
                "合成请求", SessionState(session_id="b" * 32, user_id=900003), synthetic_profile(),
            )
    assert caught.value.code == "invalid_output"
    details = caught.value.diagnostics()
    assert details["field"] == field and details["category"] == category
    assert details["stage"] in {"intent", "response"}
    assert len(details["request_id"]) == 32
    assert "secret" not in str(details) and "secret" not in str(caught.value)
    assert len(observed) == 1


def test_damaged_response_is_502_without_state_commit(tmp_path, caplog):
    adapter, _, _ = provider(['{"action":"plan", "people":"sensitive-payload"}'])
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "isolated.db")
    store = SessionStore(settings.database_path)
    caplog.set_level(logging.WARNING, logger="app.api.main")
    with TestClient(create_app(settings, adapter, synthetic_catalog(), store)) as client:
        result = client.post("/chat", json={
            "user_id": 900003, "message": "2人晚餐，没有其他忌口", "request_id": "synthetic-r3",
        })
    assert result.status_code == 502
    assert result.json()["detail"]["code"] == "LLM_INVALID_OUTPUT"
    assert len(result.headers["x-request-id"]) == 32
    assert "sensitive-payload" not in result.text
    records = [record for record in caplog.records if record.name == "app.api.main"]
    assert len(records) == 1 and records[0].exc_info is None
    assert records[0].getMessage() == (
        "llm_output_rejected stage=intent field=people category=invalid_type request_id="
        + result.headers["x-request-id"]
    )
    assert "sensitive-payload" not in caplog.text and "synthetic-key" not in caplog.text
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_legacy_synthetic_snapshot_defaults_to_no_count_conflict(tmp_path):
    state = SessionState(session_id="c" * 32, user_id=900003)
    legacy = state.model_dump()
    legacy.pop("pending_menu_counts", None)
    restored = SessionState.model_validate(legacy)
    assert getattr(restored, "pending_menu_counts", "missing-field") is None
    store = SessionStore(tmp_path / "legacy.db")
    store.save(restored, None)
    result = ChatResult(
        status="clarification_required", reason="合成旧会话", conversation_state=restored,
    )
    store.complete(result, 0, "synthetic-legacy", "synthetic-hash")
    cached = result.model_dump()
    cached["conversation_state"].pop("pending_menu_counts", None)
    # Simulate an old serialized session/cache, exclusively in a temporary database.
    with store._connect() as connection:
        connection.execute("UPDATE sessions SET snapshot = ?", (json.dumps(legacy),))
        connection.execute("UPDATE requests SET result = ?", (json.dumps(cached),))
    restarted = SessionStore(tmp_path / "legacy.db")
    assert restarted.get(state.session_id, state.user_id).pending_menu_counts is None
    replayed = restarted.replay(state.session_id, "synthetic-legacy", "synthetic-hash", 0)
    assert replayed.conversation_state.pending_menu_counts is None
    assert replayed.reason == "合成旧会话"


def openai_payload(message, stream, session_id=None):
    payload = {
        "model": "fangtai-meal-agent", "user": "900003", "stream": stream,
        "messages": [{"role": "user", "content": message}],
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def completion_text(response, stream):
    assert response.status_code == 200
    if not stream:
        assert response.json()["choices"][0]["finish_reason"] == "stop"
        return response.json()["choices"][0]["message"]["content"]
    assert response.headers["content-type"].startswith("text/event-stream")
    records = [record for record in response.text.split("\n\n") if record]
    assert records[-1] == "data: [DONE]"
    chunks = [json.loads(record.removeprefix("data: ")) for record in records[:-1]]
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)


@pytest.mark.parametrize("stream", [False, True])
def test_openai_json_and_sse_recover_count_conflict_through_real_adapter(tmp_path, stream):
    """Real adapter validation executes; provider and API transport stay in-process."""
    adapter, _, observed = provider([count_intent(), count_intent(dish_count=4, soup_count=1)])
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "isolated.db")
    store = SessionStore(settings.database_path)
    catalog = synthetic_catalog()
    with TestClient(create_app(settings, adapter, catalog, store)) as client:
        first = client.post("/v1/chat/completions", json=openai_payload(
            "2人晚餐，没有其他忌口，要2道菜其中3道汤", stream,
        ))
        first_text = completion_text(first, stream)
        sid = first.headers["x-session-id"]
        first_state = store.get(sid, 900003)
        assert first_text == first_state.pending_clarification
        assert "2" in first_text and "3" in first_text
        assert "本餐菜单：" not in first_text and not first_state.menu_valid
        assert first_state.pending_menu_counts.model_dump() == {"dish_count": 2, "soup_count": 3}
        assert first_state.constraints.soup_count <= first_state.constraints.dish_count
        second = client.post("/v1/chat/completions", json=openai_payload(
            "纠正为2人晚餐，共4道菜其中1道汤，没有其他忌口", stream, sid,
        ))
        text = completion_text(second, stream)
        final = store.get(sid, 900003)
    assert second.headers["x-session-id"] == sid
    assert final.pending_menu_counts is None and final.menu_valid and final.revision == 2
    assert len(final.menu_ids) == 4
    assert sum("soup" in catalog.recipes[key].categories for key in final.menu_ids) == 1
    assert final.constraints.allergies == ["花生"]
    assert "本餐菜单：" in text
    assert all(catalog.recipes[key].name in text for key in final.menu_ids)
    assert len(observed) == 3  # Two parse calls and one verified-facts explanation, no retry.
    assert observed[1]["pending_menu_counts"] == {"dish_count": 2, "soup_count": 3}


@pytest.mark.parametrize("stream", [False, True])
def test_openai_invalid_structure_remains_json_502_and_has_safe_correlated_log(
    tmp_path, caplog, stream,
):
    adapter, _, observed = provider([{"action": "plan", "people": "sensitive-payload"}])
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "isolated.db")
    store = SessionStore(settings.database_path)
    caplog.set_level(logging.WARNING, logger="app.api.main")
    with TestClient(create_app(settings, adapter, synthetic_catalog(), store)) as client:
        result = client.post("/v1/chat/completions", json=openai_payload(
            "2人晚餐，没有其他忌口", stream,
        ))
    assert result.status_code == 502
    assert result.headers["content-type"].startswith("application/json")
    assert set(result.json()) == {"error"}
    assert result.json()["error"] == {
        "message": "模型未返回可验证的需求结构，请重试。", "type": "server_error",
        "param": None, "code": "llm_invalid_output",
    }
    correlation = result.headers["x-request-id"]
    assert len(correlation) == 32
    records = [record for record in caplog.records if record.name == "app.api.main"]
    assert len(records) == 1 and records[0].exc_info is None
    assert records[0].getMessage() == (
        "llm_output_rejected stage=intent field=people category=invalid_type request_id="
        + correlation
    )
    for sensitive in ["sensitive-payload", "synthetic-key", "2人晚餐，没有其他忌口"]:
        assert sensitive not in caplog.text and sensitive not in result.text
    assert len(observed) == 1
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
