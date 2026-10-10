"""Synthetic final-menu rendering and independent SSE client counterexamples."""

import asyncio
import codecs
import json
import re
from pathlib import Path

import pytest
from starlette.responses import StreamingResponse

from app.api import openai_compat
from app.domain.models import ChatResult, MenuItem, MenuRevision, Recipe, SessionState

TAIL_START = "\n\n【菜谱JSON】\n```json\n"


def recipe_tail(content):
    """Parse the anchored final block only, after the complete answer is assembled."""
    _, marker, suffix = content.rpartition(TAIL_START)
    assert marker and suffix.endswith("\n```")
    items = json.loads(suffix[:-4])
    assert isinstance(items, list)
    assert all(list(item) == ["recipe_id", "name"] for item in items)
    return items


def decode_sse(byte_parts):
    """A client decoder independent of the production SSE renderer/chunker."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    records = []
    for part in byte_parts:
        pending += decoder.decode(part)
        while "\n\n" in pending:
            event, pending = pending.split("\n\n", 1)
            # SSE lines end with CR/LF, not Unicode U+2028/U+2029 inside JSON.
            data = "\n".join(line[6:].removesuffix("\r") for line in event.split("\n")
                             if line.startswith("data: "))
            assert data
            assert not records or records[-1] != "[DONE]"
            records.append("[DONE]" if data == "[DONE]" else json.loads(data))
    pending += decoder.decode(b"", final=True)
    assert not pending
    assert records.count("[DONE]") == 1 and records[-1] == "[DONE]"
    return records[:-1]


@pytest.fixture
def verified_result():
    menu = [
        MenuItem(slot=1, recipe_id="synthetic_z", name="同名菜", ingredients=[], steps="合成步骤"),
        MenuItem(slot=2, recipe_id="synthetic_a", name="同名菜", ingredients=[], steps="合成步骤"),
        MenuItem(
            slot=3, recipe_id="generated_synthetic_final", name="新生成蔬菜方案",
            ingredients=[], steps="合成步骤", source="本地新生成方案（待试做，非原菜谱库）",
        ),
    ]
    state = SessionState(
        session_id="a" * 32, user_id=900001, menu_valid=True,
        menu_ids=[item.recipe_id for item in menu],
        menu_history=[MenuRevision(revision_id="old", turn_index=1, recipe_ids=["old_menu_id"])],
        generated_recipes={"generated_synthetic_unused": Recipe(
            recipe_id="generated_synthetic_unused", name="未采用的合成提案",
            raw_ingredients="合成青菜", steps="合成步骤", source_row=0,
            fingerprint="synthetic-unused-fingerprint",
        )},
    )
    return ChatResult(
        status="ok", menu=menu, reason="本轮菜单已经核验。", conversation_state=state,
        replacement_suggestions=[menu[0].model_copy(update={"recipe_id": "unused_candidate"})],
    )


def expected_summary(result):
    return [{"recipe_id": item.recipe_id, "name": item.name} for item in result.menu]


def test_final_menu_order_same_name_generated_id_and_unused_candidates(verified_result):
    result = verified_result
    before = result.model_dump_json()
    content = openai_compat.assistant_text(result)
    assert recipe_tail(content) == expected_summary(result)
    assert [item["recipe_id"] for item in recipe_tail(content)] == [
        "synthetic_z", "synthetic_a", "generated_synthetic_final",
    ]
    assert content.startswith(
        "本餐菜单：\n1. 同名菜（菜谱ID：synthetic_z；来源：方太菜谱库）\n"
    )
    assert content.split(TAIL_START)[0].endswith(result.reason)
    assert all(key not in content for key in (
        "unused_candidate", "old_menu_id", "generated_synthetic_unused",
    ))
    assert content.count(TAIL_START) == 1
    assert openai_compat.assistant_text(result) == content
    assert result.model_dump_json() == before


def test_final_menu_is_not_sorted_by_name_id_or_slot(verified_result):
    result = verified_result
    result.menu = [
        result.menu[2].model_copy(update={"name": "丙菜"}),
        result.menu[0].model_copy(update={"name": "甲菜"}),
        result.menu[1].model_copy(update={"name": "乙菜"}),
    ]
    result.conversation_state.menu_ids = [item.recipe_id for item in result.menu]
    content = openai_compat.assistant_text(result)
    assert recipe_tail(content) == [
        {"recipe_id": "generated_synthetic_final", "name": "丙菜"},
        {"recipe_id": "synthetic_z", "name": "甲菜"},
        {"recipe_id": "synthetic_a", "name": "乙菜"},
    ]
    assert content.index("3. 丙菜") < content.index("1. 甲菜") < content.index("2. 乙菜")


def test_special_names_and_body_markers_round_trip_without_removing_prose(verified_result):
    name = '中文"双引号"\\路径\n换行\r\t雪❄🌿𠮷\u2028\u2029【菜谱JSON】\n```json\n[]\n```'
    result = verified_result
    result.menu[0].name = name
    result.reason = '正文中的合法例子：' + TAIL_START + '[]\n```\n请保留这段说明。'
    before = result.model_dump_json()
    body = openai_compat.completion_body(result, "chatcmpl-synthetic", 100)
    content = json.loads(json.dumps(body))["choices"][0]["message"]["content"]
    assert recipe_tail(content) == expected_summary(result)
    assert recipe_tail(content)[0]["name"] == name
    assert result.reason in content
    assert '\\"双引号\\"' in content and '\\路径' in content and '\\n换行' in content
    records = decode_sse([
        "".join(openai_compat.stream_events(result, "chatcmpl-synthetic", 100)).encode("utf-8")
    ])
    assert "".join(record["choices"][0]["delta"].get("content", "") for record in records) == content
    assert result.model_dump_json() == before


@pytest.mark.parametrize("status", ["clarification_required", "no_feasible_menu"])
@pytest.mark.parametrize("historical_menu_valid", [False, True])
def test_no_current_menu_ignores_even_a_carried_historical_menu(
    verified_result, status, historical_menu_valid,
):
    result = verified_result
    result.status = status
    result.conversation_state.menu_valid = historical_menu_valid
    result.reason = "请确认用餐人数。" if status == "clarification_required" else "已知要求不可行。"
    # A stale/legacy item must not become a new success or a presentation failure.
    result.menu[0] = result.menu[0].model_copy(update={"recipe_id": None})
    before = result.model_dump(mode="python")
    content = openai_compat.assistant_text(result)
    assert content.startswith(result.reason + "\n本次未返回菜单。")
    assert recipe_tail(content) == []
    assert "本餐菜单：" not in content and "同名菜" not in content
    assert result.model_dump(mode="python") == before


@pytest.mark.parametrize("field", ["recipe_id", "name"])
@pytest.mark.parametrize("bad_value", [None, 123, False, [], "", " \t\r\n", "\ud800"])
def test_invalid_final_identity_fails_before_a_stream_is_created(
    verified_result, monkeypatch, field, bad_value,
):
    result = verified_result
    result.menu[0] = result.menu[0].model_copy(update={field: bad_value})
    rendered = []
    monkeypatch.setattr(openai_compat, "_sse", lambda payload: rendered.append(payload))
    for render in (openai_compat.completion_body, openai_compat.stream_events):
        with pytest.raises(openai_compat.OpenAIRequestError) as rejected:
            render(result, "chatcmpl-synthetic", 100)
        assert rejected.value.status_code == 500
        assert rejected.value.code == "invalid_menu_result"
        assert rejected.value.error_type == "server_error"
    assert rendered == []


@pytest.mark.parametrize("field", ["recipe_id", "name"])
def test_missing_final_identity_is_not_invented(verified_result, field):
    del verified_result.menu[0].__dict__[field]
    with pytest.raises(openai_compat.OpenAIRequestError):
        openai_compat.stream_events(verified_result, "chatcmpl-synthetic", 100)


@pytest.mark.parametrize("problem", ["unverified", "empty", "different_state", "state_order"])
def test_ok_with_an_inconsistent_final_menu_is_not_a_successful_empty_array(
    verified_result, problem,
):
    if problem == "unverified":
        verified_result.conversation_state.menu_valid = False
    elif problem == "empty":
        verified_result.menu = []
    elif problem == "different_state":
        verified_result.conversation_state.menu_ids = ["old_menu_id"]
    else:
        verified_result.conversation_state.menu_ids.reverse()
    with pytest.raises(openai_compat.OpenAIRequestError) as rejected:
        openai_compat.completion_body(verified_result, "chatcmpl-synthetic", 100)
    assert rejected.value.status_code == 500


@pytest.mark.parametrize("network_read_size", [1, 2, 3, 7, 23])
def test_fragmented_sse_utf8_json_escapes_and_nonstreaming_are_identical(
    verified_result, monkeypatch, network_read_size,
):
    result = verified_result
    result.menu[0].name = '合成"菜\\名"\n🌿𠮷\u2028\u2029【菜谱JSON】'
    # Force inner JSON strings and escape pairs to span content chunks too.
    monkeypatch.setattr(openai_compat, "_text_chunks", lambda text: iter(text))
    before = result.model_dump_json()
    wire = "".join(openai_compat.stream_events(result, "chatcmpl-synthetic", 100)).encode("utf-8")
    records = decode_sse(
        wire[index:index + network_read_size] for index in range(0, len(wire), network_read_size)
    )
    assert records[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert all(record["id"] == "chatcmpl-synthetic" and record["created"] == 100
               and record["model"] == "fangtai-meal-agent" for record in records)
    assert all(record["choices"][0]["finish_reason"] is None for record in records[:-1])
    assert records[-1]["choices"][0]["delta"] == {}
    assert records[-1]["choices"][0]["finish_reason"] == "stop"
    content = "".join(record["choices"][0]["delta"].get("content", "") for record in records)
    assert content == openai_compat.completion_body(result, "chatcmpl-synthetic", 100)[
        "choices"
    ][0]["message"]["content"]
    assert recipe_tail(content) == expected_summary(result)
    assert content.count(TAIL_START) == 1
    assert result.model_dump_json() == before


@pytest.mark.parametrize("status", ["clarification_required", "no_feasible_menu"])
def test_empty_tail_and_nonstreaming_are_identical(verified_result, status):
    verified_result.status = status
    wire = "".join(openai_compat.stream_events(verified_result, "chatcmpl-synthetic", 100))
    records = decode_sse([wire.encode("utf-8")])
    content = "".join(record["choices"][0]["delta"].get("content", "") for record in records)
    assert recipe_tail(content) == []
    assert content == openai_compat.completion_body(verified_result, "chatcmpl-synthetic", 100)[
        "choices"
    ][0]["message"]["content"]


@pytest.mark.parametrize("interruption", [RuntimeError("synthetic failure"), asyncio.CancelledError()])
def test_midstream_error_or_cancellation_never_fabricates_completion(
    verified_result, monkeypatch, interruption,
):
    def interrupted_chunks(text):
        yield text[:text.index(TAIL_START) + len(TAIL_START)]
        raise interruption

    monkeypatch.setattr(openai_compat, "_text_chunks", interrupted_chunks)
    received = []
    with pytest.raises(type(interruption)):
        for record in openai_compat.stream_events(verified_result, "chatcmpl-synthetic", 100):
            received.append(record)
    assert len(received) == 2
    assert not any('[DONE]' in record or '"finish_reason":"stop"' in record for record in received)
    content = json.loads(received[-1][6:])["choices"][0]["delta"]["content"]
    assert content.endswith(TAIL_START)


def test_closing_stream_does_not_generate_more_content(verified_result):
    stream = openai_compat.stream_events(verified_result, "chatcmpl-synthetic", 100)
    received = [next(stream), next(stream)]
    stream.close()
    assert list(stream) == []
    assert not any('[DONE]' in record or '"finish_reason":"stop"' in record for record in received)


@pytest.mark.asyncio
async def test_asgi_disconnect_cancels_output_without_a_fake_tail_or_done(verified_result):
    disconnected = asyncio.Event()
    received = []

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        received.append(message)
        if message["type"] == "http.response.body" and sum(
            item["type"] == "http.response.body" for item in received
        ) == 2:
            disconnected.set()
            await asyncio.Event().wait()

    response = StreamingResponse(
        openai_compat.stream_events(verified_result, "chatcmpl-synthetic", 100),
        media_type="text/event-stream",
    )
    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    observed = len(received)
    await asyncio.sleep(0)
    assert len(received) == observed
    body = b"".join(message.get("body", b"") for message in received)
    assert b"[DONE]" not in body and b'"finish_reason":"stop"' not in body
    assert "【菜谱JSON】".encode("utf-8") not in body
    assert all(message.get("more_body", True) for message in received)


@pytest.mark.parametrize("document", ["API.md", "PRELIMINARY_API_TECHNICAL_DRAFT_20261007.md"])
def test_documented_compatibility_requests_use_placeholders_and_valid_identity(document):
    text = (Path(__file__).resolve().parents[1] / "docs" / document).read_text(encoding="utf-8")
    examples = re.findall(r"```http\n(.*?)\n```", text, flags=re.DOTALL)
    assert len(examples) == (2 if document == "API.md" else 1)
    for example in examples:
        header_text, body_text = example.split("\n\n", 1)
        lines = header_text.splitlines()
        assert lines[0] == "POST /v1/chat/completions HTTP/1.1"
        headers = dict(line.split(": ", 1) for line in lines[1:])
        assert headers["Authorization"] == "Bearer <PUBLIC_API_TOKEN>"
        assert headers["Host"] == "<部署域名或地址>"
        raw_body = json.loads(body_text)
        assert raw_body["user"] == "<已授权的测试用户ID>"
        body = {**raw_body, "user": "900001"}
        request = openai_compat.ChatCompletionsRequest.model_validate(body)
        header_session = "a" * 32 if "X-Session-ID" in headers else None
        resolved = openai_compat.resolve_agent_request(request, None, header_session, None)
        assert request.stream is True and resolved.user_id == 900001
        assert len(request.messages) == 1 and resolved.session_id == header_session


def test_documented_answers_and_outer_json_are_parseable_and_match_the_fixture(verified_result):
    text = (Path(__file__).resolve().parents[1] / "docs/API.md").read_text(encoding="utf-8")
    answers = re.findall(r"````text\n(.*?)\n````", text, flags=re.DOTALL)
    assert len(answers) == 2
    assert answers[0] == openai_compat.assistant_text(verified_result)
    assert recipe_tail(answers[0]) == expected_summary(verified_result)
    empty = verified_result.model_copy(update={
        "status": "clarification_required", "reason": "请确认用餐人数。",
    })
    assert answers[1] == openai_compat.assistant_text(empty)
    assert recipe_tail(answers[1]) == []
    json_blocks = re.findall(r"(?m)^```json\n(.*?)\n```$", text, flags=re.DOTALL)
    completions = [body for block in json_blocks if isinstance(body := json.loads(block), dict)
                   and body.get("object") == "chat.completion"]
    assert len(completions) == 1
    assert completions[0] == openai_compat.completion_body(verified_result, "chatcmpl-synthetic", 100)
