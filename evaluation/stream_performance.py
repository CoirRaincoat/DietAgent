"""Measure client-observed SSE latency against competition thresholds.

The judge controls official timing. This harness measures the same observable
boundary: immediately before the HTTP request, the first non-empty assistant
content delta, and the terminal ``[DONE]`` event.
"""

import argparse
import asyncio
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal
from uuid import uuid4

import httpx

MODEL_ID = "fangtai-meal-agent"
DEFAULT_SINGLE_MESSAGE = "1人晚餐，没有其他忌口"
DEFAULT_MULTI_MESSAGES = (
    "帮我安排一餐。",
    "2个人，晚餐。",
    "没有其他忌口，安排三道菜。",
)
THRESHOLDS_MS = {
    "ttft": (2000.0, 5000.0),
    "single_e2e": (8000.0, 15000.0),
    "multi_average": (6000.0, 12000.0),
}
LatencyMetric = Literal["ttft", "single_e2e", "multi_average"]
LatencyGrade = Literal["excellent", "qualified", "exceeded", "no_data"]


@dataclass(frozen=True)
class StreamObservation:
    """One HTTP turn measured at the external client boundary."""

    scenario_id: str
    turn: int
    request_id: str
    status_code: int
    time_to_headers_ms: float | None = None
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    content_chars: int = 0
    completed: bool = False
    session_id: str | None = None
    error: str | None = None
    server_timing_ms: dict[str, float] = field(default_factory=dict)


def grade_latency(metric: str, milliseconds: float) -> LatencyGrade:
    """Apply the strict less-than thresholds stated in the competition brief."""
    if metric not in THRESHOLDS_MS:
        raise ValueError(f"Unknown latency metric: {metric}")
    excellent_limit, qualified_limit = THRESHOLDS_MS[metric]
    if milliseconds < excellent_limit:
        return "excellent"
    if milliseconds < qualified_limit:
        return "qualified"
    return "exceeded"


def parse_server_timing(value: str | None) -> dict[str, float]:
    """Parse the duration subset of the standard Server-Timing header."""
    parsed: dict[str, float] = {}
    if not value:
        return parsed
    for item in value.split(","):
        parts = [part.strip() for part in item.split(";")]
        name = parts[0]
        for parameter in parts[1:]:
            if not parameter.startswith("dur="):
                continue
            try:
                duration = float(parameter.removeprefix("dur="))
            except ValueError:
                continue
            if name and math.isfinite(duration) and duration >= 0:
                parsed[name] = duration
    return parsed


async def measure_stream_turn(
    client: httpx.AsyncClient,
    *,
    user_id: int,
    message: str,
    request_id: str,
    scenario_id: str,
    turn: int,
    session_id: str | None = None,
    clock: Callable[[], float] = perf_counter,
) -> StreamObservation:
    """Measure headers, first non-empty content, and terminal SSE event."""
    payload: dict[str, object] = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": message}],
        "user": str(user_id),
        "stream": True,
        "request_id": request_id,
    }
    if session_id:
        payload["session_id"] = session_id

    started = clock()
    try:
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            headers_at = clock()
            headers_ms = _milliseconds(headers_at - started)
            response_session = response.headers.get("x-session-id")
            server_timing = parse_server_timing(response.headers.get("server-timing"))
            if response.status_code != 200:
                await response.aread()
                return StreamObservation(
                    scenario_id=scenario_id,
                    turn=turn,
                    request_id=request_id,
                    status_code=response.status_code,
                    time_to_headers_ms=headers_ms,
                    e2e_ms=_milliseconds(clock() - started),
                    completed=False,
                    session_id=response_session,
                    error=f"http_{response.status_code}",
                    server_timing_ms=server_timing,
                )

            first_content_at: float | None = None
            content_chars = 0
            done_at: float | None = None
            protocol_error: str | None = None
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    done_at = clock()
                    break
                try:
                    event = json.loads(data)
                    content = event["choices"][0]["delta"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
                    protocol_error = "invalid_sse_event"
                    break
                if content:
                    content_chars += len(content)
                    if first_content_at is None:
                        first_content_at = clock()

            if done_at is None:
                done_at = clock()
                protocol_error = protocol_error or "stream_missing_done"
            if first_content_at is None:
                protocol_error = protocol_error or "stream_missing_content"
            return StreamObservation(
                scenario_id=scenario_id,
                turn=turn,
                request_id=request_id,
                status_code=response.status_code,
                time_to_headers_ms=headers_ms,
                ttft_ms=(
                    _milliseconds(first_content_at - started)
                    if first_content_at is not None
                    else None
                ),
                e2e_ms=_milliseconds(done_at - started),
                content_chars=content_chars,
                completed=protocol_error is None,
                session_id=response_session,
                error=protocol_error,
                server_timing_ms=server_timing,
            )
    except (httpx.HTTPError, TimeoutError) as error:
        return StreamObservation(
            scenario_id=scenario_id,
            turn=turn,
            request_id=request_id,
            status_code=0,
            e2e_ms=_milliseconds(clock() - started),
            completed=False,
            error=f"transport_{type(error).__name__}",
        )


def summarize_observations(observations: Sequence[StreamObservation], *, multi_turn: bool) -> dict:
    """Summarize only completed observations while keeping failures explicit."""
    successful = [
        item
        for item in observations
        if item.completed and item.ttft_ms is not None and item.e2e_ms is not None
    ]
    ttft = _distribution([item.ttft_ms for item in successful if item.ttft_ms is not None])
    e2e = _distribution([item.e2e_ms for item in successful if item.e2e_ms is not None])
    if ttft["mean"] is not None:
        ttft["status_by_mean"] = grade_latency("ttft", ttft["mean"])
    else:
        ttft["status_by_mean"] = "no_data"
    e2e_metric = "multi_average" if multi_turn else "single_e2e"
    if e2e["mean"] is not None:
        e2e["status_by_mean"] = grade_latency(e2e_metric, e2e["mean"])
    else:
        e2e["status_by_mean"] = "no_data"
    total = len(observations)
    return {
        "requests": total,
        "successful": len(successful),
        "failed": total - len(successful),
        "success_rate": len(successful) / total if total else 0.0,
        "threshold_result_valid": total > 0 and len(successful) == total,
        "ttft_ms": ttft,
        "e2e_ms": e2e,
    }


async def run_benchmark(
    *,
    base_url: str,
    user_id: int,
    mode: Literal["single", "multi"],
    sessions: int,
    concurrency: int,
    timeout_seconds: float,
    single_message: str = DEFAULT_SINGLE_MESSAGE,
) -> dict:
    """Run independent synthetic sessions with bounded request concurrency."""
    messages = (single_message,) if mode == "single" else DEFAULT_MULTI_MESSAGES
    semaphore = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:

        async def run_session(number: int) -> list[StreamObservation]:
            scenario_id = f"{mode}-{number:03d}"
            session_id: str | None = None
            results: list[StreamObservation] = []
            for turn, message in enumerate(messages, start=1):
                request_id = f"perf-{scenario_id}-{turn}-{uuid4().hex[:8]}"
                async with semaphore:
                    result = await measure_stream_turn(
                        client,
                        user_id=user_id,
                        message=message,
                        request_id=request_id,
                        scenario_id=scenario_id,
                        turn=turn,
                        session_id=session_id,
                    )
                results.append(result)
                if not result.completed or not result.session_id:
                    break
                session_id = result.session_id
            return results

        nested = await asyncio.gather(*(run_session(number) for number in range(1, sessions + 1)))
    observations = [item for session in nested for item in session]
    return {
        "mode": mode,
        "sessions": sessions,
        "turns_per_session": len(messages),
        "concurrency": concurrency,
        "summary": summarize_observations(observations, multi_turn=mode == "multi"),
        "observations": [asdict(item) for item in observations],
    }


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "mean": round(mean(ordered), 2),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
    }


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def _milliseconds(seconds: float) -> float:
    return round(max(0.0, seconds * 1000), 2)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--base-url", default="http://localhost:8080")
    cli.add_argument("--user-id", type=int, default=900001)
    cli.add_argument("--mode", choices=["single", "multi", "both"], default="both")
    cli.add_argument("--sessions", type=int, default=3)
    cli.add_argument("--concurrency", type=int, default=1)
    cli.add_argument("--timeout", type=float, default=60.0)
    cli.add_argument("--message", default=DEFAULT_SINGLE_MESSAGE)
    cli.add_argument("--output", type=Path, default=Path("runtime/performance_report.json"))
    return cli


async def async_main(args: argparse.Namespace) -> dict:
    if args.sessions < 1 or args.concurrency < 1:
        raise SystemExit("--sessions and --concurrency must be positive")
    modes = ["single", "multi"] if args.mode == "both" else [args.mode]
    results = {}
    for mode in modes:
        results[mode] = await run_benchmark(
            base_url=args.base_url,
            user_id=args.user_id,
            mode=mode,
            sessions=args.sessions,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            single_message=args.message,
        )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement": "client_observed_sse",
        "base_url": args.base_url,
        "thresholds_ms": THRESHOLDS_MS,
        "results": results,
    }


def main() -> None:
    args = parser().parse_args()
    report = asyncio.run(async_main(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved performance report to {args.output}")


if __name__ == "__main__":
    main()
