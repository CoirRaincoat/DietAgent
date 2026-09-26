"""Client-observed streaming latency and competition threshold tests."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from evaluation.stream_performance import (
    StreamObservation,
    grade_latency,
    measure_stream_turn,
    summarize_observations,
)


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


def sse(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload) + "\n\n").encode()


def chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {
        "choices": [{"delta": delta, "finish_reason": finish_reason}],
    }


@pytest.mark.asyncio
async def test_ttft_is_first_nonempty_content_not_role_chunk() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-session-id": "a" * 32,
                "server-timing": "agent_total;dur=900.50, agent_parse;dur=250.00",
            },
            stream=ChunkStream(
                [
                    sse(chunk({"role": "assistant", "content": ""})),
                    sse(chunk({"content": "第一段"})),
                    sse(chunk({}, "stop")),
                    b"data: [DONE]\n\n",
                ]
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await measure_stream_turn(
            client,
            user_id=900001,
            message="1人晚餐，没有其他忌口",
            request_id="perf-1",
            scenario_id="scenario-1",
            turn=1,
            clock=SequenceClock(10.0, 10.4, 11.2, 13.5),
        )

    assert result.completed
    assert result.time_to_headers_ms == 400.0
    assert result.ttft_ms == 1200.0
    assert result.e2e_ms == 3500.0
    assert result.content_chars == 3
    assert result.session_id == "a" * 32
    assert result.server_timing_ms == {"agent_total": 900.5, "agent_parse": 250.0}


@pytest.mark.asyncio
async def test_stream_without_nonempty_content_is_a_failed_measurement() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(
                [
                    sse(chunk({"role": "assistant", "content": ""})),
                    sse(chunk({}, "stop")),
                    b"data: [DONE]\n\n",
                ]
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await measure_stream_turn(
            client,
            user_id=900001,
            message="测试",
            request_id="perf-empty",
            scenario_id="scenario-empty",
            turn=1,
            clock=SequenceClock(1.0, 1.1, 1.5),
        )

    assert not result.completed
    assert result.ttft_ms is None
    assert result.e2e_ms == 500.0
    assert result.error == "stream_missing_content"


@pytest.mark.parametrize(
    ("metric", "milliseconds", "expected"),
    [
        ("ttft", 1999.99, "excellent"),
        ("ttft", 2000.0, "qualified"),
        ("ttft", 4999.99, "qualified"),
        ("ttft", 5000.0, "exceeded"),
        ("single_e2e", 7999.99, "excellent"),
        ("single_e2e", 8000.0, "qualified"),
        ("single_e2e", 15000.0, "exceeded"),
        ("multi_average", 5999.99, "excellent"),
        ("multi_average", 6000.0, "qualified"),
        ("multi_average", 12000.0, "exceeded"),
    ],
)
def test_competition_thresholds_are_strict(metric: str, milliseconds: float, expected: str) -> None:
    assert grade_latency(metric, milliseconds) == expected


def observation(index: int, ttft_ms: float, e2e_ms: float) -> StreamObservation:
    return StreamObservation(
        scenario_id=f"scenario-{index}",
        turn=1,
        request_id=f"request-{index}",
        status_code=200,
        time_to_headers_ms=ttft_ms - 100,
        ttft_ms=ttft_ms,
        e2e_ms=e2e_ms,
        content_chars=10,
        completed=True,
        session_id="a" * 32,
    )


def test_summary_reports_distribution_failures_and_threshold_status() -> None:
    results = [
        observation(1, 1000.0, 5000.0),
        observation(2, 3000.0, 9000.0),
        StreamObservation(
            scenario_id="failed",
            turn=1,
            request_id="failed",
            status_code=503,
            completed=False,
            error="http_503",
        ),
    ]

    summary = summarize_observations(results, multi_turn=False)

    assert summary["requests"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1
    assert summary["success_rate"] == pytest.approx(2 / 3)
    assert not summary["threshold_result_valid"]
    assert summary["ttft_ms"] == {
        "mean": 2000.0,
        "p50": 2000.0,
        "p95": 2900.0,
        "min": 1000.0,
        "max": 3000.0,
        "status_by_mean": "qualified",
    }
    assert summary["e2e_ms"]["mean"] == 7000.0
    assert summary["e2e_ms"]["status_by_mean"] == "excellent"

    multi = summarize_observations(results[:2], multi_turn=True)
    assert multi["threshold_result_valid"]
    assert multi["e2e_ms"]["status_by_mean"] == "qualified"
