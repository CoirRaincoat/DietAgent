"""Synthetic, offline diagnostics must preserve strict provider behavior."""

import asyncio
import hashlib
import json
import logging

import httpx
import pytest

from app.domain.models import SessionState, UserProfile
from app.infrastructure import diagnostics
from app.infrastructure.diagnostics import request_diagnostics
from app.infrastructure.llm.base import LLMOutputError, LLMUnavailable
from app.infrastructure.llm.deepseek import DeepSeekLLM


@pytest.fixture
def inputs():
    profile = UserProfile(user_id=900001, data_scope="synthetic", age=30, sex="未指定",
                          height_cm=170, weight_kg=65, bmi=22.49, preferences=["SENSITIVE-preference"],
                          raw={"private": "SENSITIVE-raw"}, measurements={"private": "SENSITIVE-measurement"})
    return profile, SessionState(session_id="synthetic-session", user_id=900001)


def envelope(content, **metadata):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(content)}}], **metadata}


async def test_parse_is_measured_and_local_explanation_never_creates_a_model_call(inputs, caplog):
    profile, state = inputs
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        answer = {"reason_ids": ["opening"]} if "facts" in payload else {"action": "plan"}
        return httpx.Response(200, headers={"x-request-id": "SENSITIVE-provider-id"}, json=envelope(
            answer, usage={"prompt_tokens": 128, "completion_tokens": 8, "total_tokens": 136,
                           "prompt_cache_hit_tokens": 16, "prompt_cache_miss_tokens": 112,
                           "extra": "SENSITIVE-usage"}))

    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("SENSITIVE-key", client=client)
        with request_diagnostics(True, "SENSITIVE-client-id") as observed:
            assert (await adapter.parse("SENSITIVE-message", state, profile)).action == "plan"
            assert await adapter.explain({"opening": "SENSITIVE-fact"}) == ["opening"]
    calls = observed.record["model_calls"]
    assert [call["stage"] for call in calls] == ["parse"]
    assert len(requests) == 1
    assert "facts" not in json.loads(json.loads(requests[0].content)["messages"][1]["content"])
    assert all(call["http_attempted"] and call["status_code"] == 200 for call in calls)
    assert all(set(call["phases_ms"]) == {"prepare", "http", "decode", "validate"} for call in calls)
    assert all(call["request_bytes"] == len(request.content) for call, request in zip(calls, requests))
    assert calls[0]["usage"] == {"prompt_tokens": 128, "completion_tokens": 8, "total_tokens": 136,
                                 "prompt_cache_hit_tokens": 16, "prompt_cache_miss_tokens": 112}
    assert calls[0]["provider_request_id_sha256"] == hashlib.sha256(b"SENSITIVE-provider-id").hexdigest()
    assert calls[0]["connection_observation"] == "unavailable"
    assert calls[0]["response_headers_ms"] is None and calls[0]["transport_ms"] == {}
    assert "SENSITIVE-" not in json.dumps(observed.record) + caplog.text
    assert "ttft" not in json.dumps(observed.record).lower()


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


async def test_delayed_transport_is_assigned_to_http_and_nested_trace_is_not_added_twice(inputs, monkeypatch):
    profile, state = inputs
    clock = Clock()
    monkeypatch.setattr(diagnostics, "perf_counter", clock)

    async def handler(request):
        trace = request.extensions["trace"]
        for event, delay in [("connection.connect_tcp", .010), ("connection.start_tls", .020),
                             ("http11.send_request_body", .005), ("http11.receive_response_headers", .400),
                             ("http11.receive_response_body", .065)]:
            await trace(event + ".started", {"headers": "SENSITIVE-trace-info"})
            clock.advance(delay)
            await trace(event + ".complete", {"return_value": "SENSITIVE-trace-info"})
        await trace("unknown.SENSITIVE-event.complete", {"exception": "SENSITIVE-exception"})
        return httpx.Response(200, json=envelope({"action": "plan"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with request_diagnostics(True) as observed:
            await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    call = observed.record["model_calls"][0]
    assert call["phases_ms"] == {"prepare": 0, "http": 500, "decode": 0, "validate": 0}
    assert call["total_ms"] == 500 and call["response_headers_ms"] == 435
    assert call["transport_ms"] == {"connect_tcp": 10, "tls": 20, "send_body": 5,
                                    "receive_headers": 400, "receive_body": 65}
    assert call["connection_observation"] == "connect_seen"
    assert "SENSITIVE-" not in json.dumps(call)


async def test_http_events_without_connect_do_not_claim_a_verified_reused_connection(inputs):
    profile, state = inputs

    async def handler(request):
        await request.extensions["trace"]("http2.send_request_headers.started", {})
        await request.extensions["trace"]("http2.send_request_headers.complete", {})
        return httpx.Response(200, json=envelope({"action": "plan"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with request_diagnostics(True) as observed:
            await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    call = observed.record["model_calls"][0]
    assert call["http_trace_available"] and call["connection_observation"] == "no_connect_event_seen"
    assert call["response_headers_ms"] is None


@pytest.mark.parametrize("status,code", [(401, "authentication"), (429, "rate_limited"), (503, "upstream"), (400, "request_rejected")])
async def test_rejections_keep_original_error_and_record_one_attempt_without_body(status, code, inputs):
    profile, state = inputs
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": "SENSITIVE-provider-error"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMUnavailable) as caught:
            with request_diagnostics(True) as observed:
                await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    assert count == 1 and caught.value.code == code
    call = observed.record["model_calls"][0]
    assert call["outcome"] == code and call["status_code"] == status
    assert "SENSITIVE-provider-error" not in json.dumps(observed.record)


@pytest.mark.parametrize("failure,expected", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network"), (asyncio.CancelledError, "cancelled")])
async def test_timeout_network_and_cancel_have_independent_metrics_and_no_retry(failure, expected, inputs):
    profile, state = inputs
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        await request.extensions["trace"]("http11.receive_response_body.started", {})
        await request.extensions["trace"]("http11.receive_response_body.failed", {"exception": "SENSITIVE-error"})
        raise failure("SENSITIVE-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError if expected == "cancelled" else LLMUnavailable):
            with request_diagnostics(True) as observed:
                await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    call = observed.record["model_calls"][0]
    assert call["outcome"] == observed.record["outcome"] == expected and count == 1
    assert call["status_code"] is None and call["usage"] == {}
    assert call["transport_outcomes"] == {"receive_body": "failed"}
    assert "SENSITIVE-error" not in json.dumps(observed.record)


@pytest.mark.parametrize("usage", [None, "SENSITIVE-usage", [], {"prompt_tokens": True, "completion_tokens": -1,
    "total_tokens": "SENSITIVE-usage", "prompt_cache_hit_tokens": 2**64, "unknown": 1}])
async def test_missing_or_malformed_usage_is_unknown_and_does_not_invalidate_a_valid_intent(usage, inputs):
    profile, state = inputs
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json=envelope({"action": "plan"}, usage=usage)))) as client:
        with request_diagnostics(True) as observed:
            assert (await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)).action == "plan"
    assert observed.record["model_calls"][0]["usage"] == {}


async def test_invalid_intent_still_fails_strict_validation_and_is_measured(inputs):
    profile, state = inputs
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json=envelope({"action": "plan", "people": "2"})))) as client:
        with pytest.raises(LLMOutputError):
            with request_diagnostics(True) as observed:
                await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    assert observed.record["model_calls"][0]["outcome"] == "invalid_output"
    assert "validate" in observed.record["model_calls"][0]["phases_ms"]


@pytest.mark.parametrize("content", ["not json", '{"action":"plan","action":"explain"}', '{"action":"plan","people":NaN}'])
async def test_invalid_json_and_duplicate_keys_still_fail_in_the_decode_phase(content, inputs):
    profile, state = inputs
    body = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        with pytest.raises(LLMOutputError):
            with request_diagnostics(True) as observed:
                await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
    call = observed.record["model_calls"][0]
    assert call["outcome"] == "invalid_output" and "decode" in call["phases_ms"]
    assert "validate" not in call["phases_ms"]


async def test_original_profile_parse_uses_only_allowlist_and_empty_explanation_is_local(inputs):
    profile, state = inputs
    observed_requests = []

    def handler(request):
        observed_requests.append(request)
        return httpx.Response(200, json=envelope({"action": "plan"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("synthetic-key", client=client)
        with request_diagnostics(True) as observed:
            assert (await adapter.parse("测试", state, profile.model_copy(update={"data_scope": "original"}))).action == "plan"
            assert await adapter.explain({}) == []
    assert len(observed_requests) == 1
    assert "SENSITIVE-" not in observed_requests[0].content.decode()
    assert [call["stage"] for call in observed.record["model_calls"]] == ["parse"]
    assert observed.record["model_calls"][0]["http_attempted"] is True


async def test_concurrent_requests_on_one_adapter_cannot_mix_usage_or_trace_records(inputs):
    profile, state = inputs
    entered, release = 0, asyncio.Event()

    async def handler(request):
        nonlocal entered
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        entered += 1
        if entered == 2:
            release.set()
        await asyncio.wait_for(release.wait(), 1)
        return httpx.Response(200, json=envelope({"action": "plan"}, usage={
            "prompt_tokens": 11 if payload["message"] == "A" else 22}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("synthetic-key", client=client)

        async def run(message):
            with request_diagnostics(True, message) as observed:
                await adapter.parse(message, state, profile)
            return observed.record

        first, second = await asyncio.gather(run("A"), run("B"))
    assert first["correlation_id"] != second["correlation_id"]
    assert first["model_calls"][0]["usage"] == {"prompt_tokens": 11}
    assert second["model_calls"][0]["usage"] == {"prompt_tokens": 22}


async def test_disabled_scope_has_no_trace_or_logs_even_inside_an_enabled_scope(inputs, caplog):
    profile, state = inputs
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=envelope({"action": "plan"}))

    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with request_diagnostics(True) as outer:
            with request_diagnostics(False) as disabled:
                await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)
            assert disabled is None and caplog.records == []
    assert "trace" not in requests[0].extensions and outer.record["model_calls"] == []


async def test_unusual_provider_identifier_and_broken_log_sink_do_not_change_result(inputs, monkeypatch):
    profile, state = inputs
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, content=json.dumps(envelope({"action": "plan"}, id="\ud800")).encode())

    def broken_sink(*args, **kwargs):
        raise RuntimeError("SENSITIVE-logger-error")

    monkeypatch.setattr(diagnostics.logger, "info", broken_sink)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with request_diagnostics(True, "\ud800") as observed:
            assert (await DeepSeekLLM("synthetic-key", client=client).parse("测试", state, profile)).action == "plan"
    assert count == 1 and len(observed.record["model_calls"][0]["provider_request_id_sha256"]) == 64
