"""Explicit client-side timing helper; no CLI, auto-run or retry behavior.

An assessment callback receives the decoded response in memory. Returned
measurements never contain request data, response text or exception messages.
"""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx


@dataclass(frozen=True)
class ClientObservation:
    http_status: int | None
    http_ms: float
    decode_ms: float
    assessment_ms: float
    complete_ms: float
    passed: bool | None
    error: str | None


async def observe_client_turn(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    assess: Callable[[dict], bool],
    *,
    path: str = "/chat",
    clock: Callable[[], float] = perf_counter,
) -> ClientObservation:
    """One explicitly requested POST; distinguish HTTP return from assessment.

    Callers own authorization, budget and synthetic-data selection. This helper
    never discovers a server, imports an evaluator or starts another batch.
    """
    started = clock()
    try:
        response = await client.post(path, json=payload, follow_redirects=False)
    except httpx.RequestError:
        elapsed = round((clock() - started) * 1000, 3)
        return ClientObservation(None, elapsed, 0, 0, elapsed, None, "transport_error")
    returned = clock()
    http_ms = round((returned - started) * 1000, 3)
    if not 200 <= response.status_code < 300:
        return ClientObservation(response.status_code, http_ms, 0, 0, http_ms, None, "http_error")
    try:
        body = response.json()
    except ValueError:
        decoded = clock()
        return ClientObservation(response.status_code, http_ms, round((decoded - returned) * 1000, 3),
                                 0, round((decoded - started) * 1000, 3), None, "json_error")
    decoded = clock()
    decode_ms = round((decoded - returned) * 1000, 3)
    if not isinstance(body, dict):
        return ClientObservation(response.status_code, http_ms, decode_ms, 0,
                                 round((decoded - started) * 1000, 3), None, "invalid_response")
    try:
        passed = assess(body)
        error = None if type(passed) is bool else "invalid_assessment"
        passed = passed if error is None else None
    except Exception:
        passed, error = None, "assessment_error"
    assessed = clock()
    return ClientObservation(response.status_code, http_ms, decode_ms,
                             round((assessed - decoded) * 1000, 3),
                             round((assessed - started) * 1000, 3), passed, error)
