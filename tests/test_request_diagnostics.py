"""Synthetic API/store/cancellation checks for default-off diagnostics."""

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import SessionState
from app.infrastructure import diagnostics
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.diagnostics import request_diagnostics, timed_lock
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.infrastructure.synthetic import synthetic_profiles


@pytest.fixture
def catalog():
    recipes = normalize_recipes([{"名称": name, "食材清单": food, "烹饪步骤": "处理食材后装盘。", "label": "晚餐"}
                                 for name, food in [("清蒸鸡", "鸡肉100克"), ("清炒青菜", "青菜100克"), ("米饭", "大米100克")]])
    return DataCatalog(profiles=synthetic_profiles(), recipes=recipes, quality_report={})


def provider(request):
    body = json.loads(json.loads(request.content)["messages"][1]["content"])
    content = {"reason_ids": ["opening"]} if "facts" in body else {
        "action": "plan", "people": 1, "meal_type": "晚餐", "restrictions_confirmed": True}
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(content)}}]})


def logged_records(caplog):
    return [json.loads(record.getMessage().split("latency_diagnostics ", 1)[1])
            for record in caplog.records if record.name == diagnostics.logger.name]


def test_settings_default_off_and_disabled_api_has_no_diagnostic_log(tmp_path, catalog, caplog, monkeypatch):
    monkeypatch.delenv("LATENCY_DIAGNOSTICS_ENABLED", raising=False)
    settings = Settings(_env_file=None, session_db=tmp_path / "disabled.db")
    assert settings.latency_diagnostics_enabled is False
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(settings, llm=DeepSeekLLM("synthetic-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    with TestClient(app) as client:
        response = client.post("/chat", json={"user_id": 900001, "message": "1人晚餐，无忌口", "request_id": "default"})
    asyncio.run(upstream.aclose())
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert logged_records(caplog) == []
    assert set(response.json()["timings_ms"]) == {"parse", "retrieval_rules_planning", "explanation", "total"}


@pytest.mark.parametrize("path", ["/chat", "/v1/chat/completions"])
def test_both_api_routes_capture_parse_and_db_commit_without_sending_explanation(path, tmp_path, catalog, caplog):
    settings = Settings(_env_file=None, session_db=tmp_path / "enabled.db", latency_diagnostics_enabled=True)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(settings, llm=DeepSeekLLM("SENSITIVE-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    payload = {"user_id": 900001, "message": "1人晚餐，无忌口", "request_id": "SENSITIVE-client-id"}
    if path != "/chat":
        payload = {"model": "fangtai-meal-agent", "messages": [{"role": "user", "content": "1人晚餐，无忌口"}],
                   "user": "900001", "request_id": "SENSITIVE-client-id"}
    with TestClient(app) as client:
        response = client.post(path, json=payload)
    asyncio.run(upstream.aclose())
    assert response.status_code == 200
    record = logged_records(caplog)[0]
    assert set(record["spans_ms"]) == {"capacity_wait", "session_lock_wait", "state_read", "state_save", "result_commit"}
    assert [call["stage"] for call in record["model_calls"]] == ["parse"]
    assert record["outcome"] == "ok" and "SENSITIVE-" not in json.dumps(record)
    assert "latency.v1" not in response.text


def test_environment_can_enable_diagnostics_without_changing_env_files(monkeypatch):
    monkeypatch.setenv("LATENCY_DIAGNOSTICS_ENABLED", "true")
    assert Settings(_env_file=None).latency_diagnostics_enabled is True


async def test_enabled_and_disabled_diagnostics_preserve_exact_upstream_requests_and_results(catalog):
    requests, results = [], []

    def handler(request):
        requests.append(request)
        return provider(request)

    state = SessionState(session_id="synthetic-session", user_id=900001)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        adapter = DeepSeekLLM("synthetic-key", client=upstream)
        for enabled in (False, True):
            with request_diagnostics(enabled):
                results.append(((await adapter.parse("1人晚餐，无忌口", state, catalog.profiles[900001])).model_dump(),
                                await adapter.explain({"opening": "合成的已验证事实"})))
    assert results[0] == results[1] and len(requests) == 2
    for plain, instrumented in zip(requests[:1], requests[1:]):
        assert plain.content == instrumented.content
        assert plain.method == instrumented.method == "POST" and plain.url == instrumented.url
        assert plain.headers == instrumented.headers
        assert plain.extensions["timeout"] == instrumented.extensions["timeout"]
        assert "trace" not in plain.extensions and "trace" in instrumented.extensions


@pytest.mark.parametrize("path", ["/chat", "/v1/chat/completions"])
@pytest.mark.parametrize("upstream_status,expected_status,expected_outcome", [
    (500, 503, "upstream"), (200, 502, "invalid_output")])
def test_parse_rejection_preserves_api_error_and_records_one_attempt(
    path, upstream_status, expected_status, expected_outcome, tmp_path, catalog, caplog,
):
    requests = []

    def handler(request):
        requests.append(request)
        if upstream_status != 200:
            return httpx.Response(upstream_status, text="SENSITIVE-provider-error")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"action":"plan","people":"SENSITIVE-invalid"}'}}]})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(_env_file=None, session_db=tmp_path / "failure.db", latency_diagnostics_enabled=True),
                     llm=DeepSeekLLM("SENSITIVE-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    payload = {"user_id": 900001, "message": "1人晚餐，无忌口"}
    if path != "/chat":
        payload = {"model": "fangtai-meal-agent", "messages": [{"role": "user", "content": "1人晚餐，无忌口"}],
                   "user": "900001"}
    with TestClient(app) as client:
        response = client.post(path, json=payload)
        assert app.state.capacity._value == 8
    asyncio.run(upstream.aclose())
    record = logged_records(caplog)[0]
    assert response.status_code == expected_status and len(requests) == 1
    assert record["outcome"] == expected_outcome
    assert len(record["model_calls"]) == 1 and record["model_calls"][0]["stage"] == "parse"
    assert record["model_calls"][0]["outcome"] == expected_outcome
    assert "state_save" not in record["spans_ms"] and "result_commit" not in record["spans_ms"]
    assert "SENSITIVE" not in json.dumps(record) and "SENSITIVE" not in response.text


@pytest.mark.parametrize("explanation_status,expected_outcome", [(500, "upstream"), (200, "invalid_output")])
def test_local_explanation_keeps_verified_menu_without_contacting_failing_provider(
    explanation_status, expected_outcome, tmp_path, catalog, caplog,
):
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(json.loads(request.content)["messages"][1]["content"])
        if "facts" not in body:
            return provider(request)
        if explanation_status != 200:
            return httpx.Response(explanation_status, text="SENSITIVE-explanation-error")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"reason_ids":["SENSITIVE-unknown-fact"]}'}}]})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(_env_file=None, session_db=tmp_path / "fallback.db", latency_diagnostics_enabled=True),
                     llm=DeepSeekLLM("synthetic-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    with TestClient(app) as client:
        response = client.post("/chat", json={"user_id": 900001, "message": "1人晚餐，无忌口"})
    asyncio.run(upstream.aclose())
    body, record = response.json(), logged_records(caplog)[0]
    assert response.status_code == 200 and body["status"] == "ok"
    assert body["menu"] and body["conversation_state"]["menu_valid"] is True
    assert body["explanation_source"] == "verified_template" and len(requests) == 1
    assert all("facts" not in json.loads(json.loads(request.content)["messages"][1]["content"]) for request in requests)
    assert record["outcome"] == "ok" and "result_commit" in record["spans_ms"]
    assert [call["outcome"] for call in record["model_calls"]] == ["ok"]
    assert "SENSITIVE" not in json.dumps(record) and "SENSITIVE" not in response.text


def test_idempotent_replay_keeps_response_exact_and_has_no_new_model_attempt(tmp_path, catalog, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return provider(request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(_env_file=None, session_db=tmp_path / "replay.db", latency_diagnostics_enabled=True)
    app = create_app(settings, llm=DeepSeekLLM("synthetic-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    payload = {"user_id": 900001, "message": "1人晚餐，无忌口", "request_id": "replay"}
    with TestClient(app) as client:
        first = client.post("/chat", json=payload)
        second = client.post("/chat", json=payload)
    asyncio.run(upstream.aclose())
    assert first.json() == second.json() and len(requests) == 1
    records = logged_records(caplog)
    assert records[1]["model_calls"] == [] and "replay_read" in records[1]["spans_ms"]
    assert "result_commit" not in records[1]["spans_ms"]
    assert records[0]["correlation_id"] != records[1]["correlation_id"]


def test_read_save_and_final_commit_delays_are_separate_from_model_http(tmp_path, catalog, caplog, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(diagnostics, "perf_counter", lambda: now[0])
    for method, delay in [("get", .020), ("save", .030), ("complete", .040)]:
        original = getattr(SessionStore, method)

        def measured(self, *args, _original=original, _delay=delay, **kwargs):
            now[0] += _delay
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(SessionStore, method, measured)

    def handler(request):
        now[0] += .100
        return provider(request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(_env_file=None, session_db=tmp_path / "delayed.db", latency_diagnostics_enabled=True),
                     llm=DeepSeekLLM("synthetic-key", client=upstream), catalog=catalog)
    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    with TestClient(app) as client:
        response = client.post("/chat", json={"user_id": 900001, "message": "1人晚餐，无忌口"})
    asyncio.run(upstream.aclose())
    assert response.status_code == 200
    record = logged_records(caplog)[0]
    assert record["spans_ms"]["state_read"] == 20
    assert record["spans_ms"]["state_save"] == 30 and record["spans_ms"]["result_commit"] == 40
    assert [call["phases_ms"]["http"] for call in record["model_calls"]] == [100]
    assert record["api_agent_total_ms"] == 190


async def test_cancelled_session_waiter_does_not_unlock_the_holder_or_contaminate_next_request():
    lock = asyncio.Lock()
    await lock.acquire()
    captured = []

    async def waiting():
        with request_diagnostics(True) as record:
            captured.append(record)
            async with timed_lock(lock):
                pytest.fail("Cancelled waiter must not enter the critical section")

    waiter = asyncio.create_task(waiting())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert lock.locked()
    assert captured[0].record["span_outcomes"]["session_lock_wait"] == "cancelled"
    lock.release()
    with request_diagnostics(True) as healthy:
        async with timed_lock(lock):
            assert lock.locked()
    assert not lock.locked() and healthy.record["outcome"] == "ok"
    assert healthy.record["span_outcomes"] == {"session_lock_wait": "ok"}


async def test_capacity_timeout_does_not_release_unacquired_slot_or_invoke_provider(tmp_path, catalog, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return provider(request)

    caplog.set_level(logging.INFO, logger=diagnostics.logger.name)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(Settings(_env_file=None, session_db=tmp_path / "capacity.db", latency_diagnostics_enabled=True),
                         llm=DeepSeekLLM("synthetic-key", client=upstream), catalog=catalog)
        async with app.router.lifespan_context(app):
            app.state.capacity = asyncio.Semaphore(0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic") as client:
                response = await client.post("/chat", json={"user_id": 900001, "message": "1人晚餐，无忌口"})
            assert response.status_code == 429 and app.state.capacity.locked()
    record = logged_records(caplog)[0]
    assert requests == [] and record["model_calls"] == []
    assert record["span_outcomes"] == {"capacity_wait": "timeout"}
