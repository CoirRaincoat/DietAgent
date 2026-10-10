"""Opt-in request-local timings. Never retain prompts, headers or model text."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.infrastructure.llm.base import LLMUnavailable

logger = logging.getLogger("uvicorn.error.dietagent.latency")
_REQUEST_SPANS = {"capacity_wait", "session_lock_wait", "state_read", "replay_read", "state_save", "result_commit"}
_MODEL_PHASES = {"prepare", "http", "decode", "validate"}
_USAGE_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
_TRACE_EVENTS = {
    "connection.connect_tcp": "connect_tcp",
    "connection.connect_unix_socket": "connect_unix_socket",
    "connection.start_tls": "tls",
    **{f"{protocol}.{operation}": stage for protocol in ("http11", "http2")
       for operation, stage in (("send_request_headers", "send_headers"),
                                ("send_request_body", "send_body"),
                                ("receive_response_headers", "receive_headers"),
                                ("receive_response_body", "receive_body"))},
}


def digest_identifier(value: Any) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= 4096:
        return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
    return None


def elapsed(started: float) -> float:
    return round(max(0.0, (perf_counter() - started) * 1000), 3)


def outcome(error: BaseException | None) -> str:
    if error is None:
        return "ok"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, LLMUnavailable):
        return error.code if error.code in LLMUnavailable._MESSAGES else "error"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "error"


@dataclass
class ModelDiagnostics:
    stage: str
    offset_ms: float
    started: float = field(default_factory=lambda: perf_counter())
    phases_ms: dict[str, float] = field(default_factory=dict)
    transport_ms: dict[str, float] = field(default_factory=dict)
    transport_counts: dict[str, int] = field(default_factory=dict)
    transport_outcomes: dict[str, str] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    request_bytes: int | None = None
    response_bytes: int | None = None
    status_code: int | None = None
    provider_request_id_sha256: str | None = None
    http_attempted: bool = False
    http_started: float | None = None
    response_headers_ms: float | None = None
    http_trace_available: bool = False
    connect_seen: bool = False
    _trace_starts: dict[str, float] = field(default_factory=dict)

    async def trace(self, name: str, info: dict) -> None:
        # info may include secret headers, exception text or connection objects.
        # Only known event names and a local clock are used.
        if not isinstance(name, str) or "." not in name:
            return
        base, event = name.rsplit(".", 1)
        stage = _TRACE_EVENTS.get(base)
        if stage is None or event not in {"started", "complete", "failed"}:
            return
        self.http_trace_available = True
        if stage in {"connect_tcp", "connect_unix_socket"}:
            self.connect_seen = True
        if event == "started":
            self._trace_starts[base] = perf_counter()
        else:
            started = self._trace_starts.pop(base, None)
            if started is not None:
                self.transport_ms[stage] = round(self.transport_ms.get(stage, 0) + elapsed(started), 3)
                self.transport_counts[stage] = self.transport_counts.get(stage, 0) + 1
                self.transport_outcomes[stage] = event
            if stage == "receive_headers" and event == "complete" and self.http_started is not None:
                self.response_headers_ms = elapsed(self.http_started)

    def snapshot(self, error: BaseException | None) -> dict:
        connection = "unavailable"
        if self.http_trace_available:
            connection = "connect_seen" if self.connect_seen else "no_connect_event_seen"
        return {
            "stage": self.stage, "offset_ms": self.offset_ms, "total_ms": elapsed(self.started),
            "outcome": outcome(error), "phases_ms": dict(self.phases_ms),
            "http_attempted": self.http_attempted, "status_code": self.status_code,
            "request_bytes": self.request_bytes, "response_bytes": self.response_bytes,
            "provider_request_id_sha256": self.provider_request_id_sha256,
            "usage": dict(self.usage), "http_trace_available": self.http_trace_available,
            "connection_observation": connection, "response_headers_ms": self.response_headers_ms,
            "transport_ms": dict(self.transport_ms), "transport_counts": dict(self.transport_counts),
            "transport_outcomes": dict(self.transport_outcomes),
            "unfinished_transport_phases": sorted({_TRACE_EVENTS[name] for name in self._trace_starts}),
        }


@dataclass
class RequestDiagnostics:
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=lambda: perf_counter())
    client_request_id_sha256: str | None = None
    spans_ms: dict[str, float] = field(default_factory=dict)
    span_outcomes: dict[str, str] = field(default_factory=dict)
    model_calls: list[dict] = field(default_factory=list)
    record: dict | None = None


_request: ContextVar[RequestDiagnostics | None] = ContextVar("latency_request", default=None)
_model: ContextVar[ModelDiagnostics | None] = ContextVar("latency_model", default=None)


@contextmanager
def request_diagnostics(enabled: bool, client_request_id: str | None = None) -> Iterator[RequestDiagnostics | None]:
    request = RequestDiagnostics(client_request_id_sha256=digest_identifier(client_request_id)) if enabled else None
    token = _request.set(request)
    model_token = _model.set(None)
    error = None
    try:
        yield request
    except BaseException as caught:
        error = caught
        raise
    finally:
        _model.reset(model_token)
        _request.reset(token)
        if request is not None:
            request.record = {
                "schema_version": "latency.v1", "correlation_id": request.correlation_id,
                "client_request_id_sha256": request.client_request_id_sha256,
                "api_agent_total_ms": elapsed(request.started), "outcome": outcome(error),
                "spans_ms": dict(request.spans_ms), "span_outcomes": dict(request.span_outcomes),
                "model_calls": list(request.model_calls),
            }
            try:
                logger.info("latency_diagnostics %s", json.dumps(request.record, ensure_ascii=True, allow_nan=False))
            except Exception:
                # Diagnostics must never change a business result or retry it.
                pass


@contextmanager
def timed(name: str) -> Iterator[None]:
    request = _request.get()
    if request is None or name not in _REQUEST_SPANS:
        yield
        return
    started, error = perf_counter(), None
    try:
        yield
    except BaseException as caught:
        error = caught
        raise
    finally:
        request.spans_ms[name] = round(request.spans_ms.get(name, 0) + elapsed(started), 3)
        request.span_outcomes[name] = outcome(error)


@asynccontextmanager
async def timed_lock(lock: asyncio.Lock) -> AsyncIterator[None]:
    if _request.get() is None:
        async with lock:
            yield
        return
    with timed("session_lock_wait"):
        await lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def model_call(stage: str) -> Iterator[None]:
    request = _request.get()
    if request is None or stage not in {"parse", "explanation"}:
        yield
        return
    call = ModelDiagnostics(stage=stage, offset_ms=elapsed(request.started))
    token = _model.set(call)
    error = None
    try:
        yield
    except BaseException as caught:
        error = caught
        raise
    finally:
        _model.reset(token)
        request.model_calls.append(call.snapshot(error))


@contextmanager
def model_phase(name: str) -> Iterator[None]:
    call = _model.get()
    if call is None or name not in _MODEL_PHASES:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        call.phases_ms[name] = round(call.phases_ms.get(name, 0) + elapsed(started), 3)


def prepare_request(request: Any) -> None:
    call = _model.get()
    if call is not None:
        call.request_bytes = len(request.content)
        request.extensions["trace"] = call.trace


def mark_http_attempt() -> None:
    call = _model.get()
    if call is not None:
        call.http_attempted = True
        call.http_started = perf_counter()


def record_response(response: Any) -> None:
    call = _model.get()
    if call is not None:
        call.status_code = response.status_code
        call.response_bytes = len(response.content)
        call.provider_request_id_sha256 = digest_identifier(response.headers.get("x-request-id"))


def record_envelope(envelope: Any) -> None:
    call = _model.get()
    if call is not None and isinstance(envelope, dict):
        if call.provider_request_id_sha256 is None:
            call.provider_request_id_sha256 = digest_identifier(envelope.get("id"))
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            call.usage = {key: value for key, value in usage.items() if key in _USAGE_KEYS
                          and type(value) is int and 0 <= value <= 2**63 - 1}
