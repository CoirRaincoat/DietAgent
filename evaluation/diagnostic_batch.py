"""Explicit synthetic POST reservations for one bounded diagnostic batch.

There is no CLI, server discovery, evaluator import or retry. The model allowance
is the source-audited upper bound of one parse plus at most one explanation.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import httpx

from evaluation.latency_observation import ClientObservation, observe_client_turn


class BudgetExceeded(RuntimeError):
    """No further POST is allowed in this batch."""


@dataclass(frozen=True)
class Reservation:
    sequence: int
    client_request_id_sha256: str
    model_request_upper_bound: int


@dataclass
class RequestBudget:
    max_posts: int
    max_model_requests: int
    attempted_posts: int = field(default=0, init=False)
    _reserved_ids: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self):
        if type(self.max_posts) is not int or not 1 <= self.max_posts <= 17:
            raise ValueError("POST budget must be an integer from 1 to 17")
        if type(self.max_model_requests) is not int or not 1 <= self.max_model_requests <= 34:
            raise ValueError("Model budget must be an integer from 1 to 34")

    @property
    def model_request_upper_bound(self) -> int:
        return self.attempted_posts * 2

    def reserve(self, request_id: str) -> Reservation:
        self.__post_init__()
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("A bounded request ID is required")
        identity = hashlib.sha256(request_id.encode("utf-8", "surrogatepass")).hexdigest()
        if identity in self._reserved_ids:
            raise BudgetExceeded("A request ID cannot be attempted twice")
        if self.attempted_posts >= self.max_posts or self.model_request_upper_bound + 2 > self.max_model_requests:
            raise BudgetExceeded("This batch has exhausted its authorized allowance")
        self._reserved_ids.add(identity)
        self.attempted_posts += 1
        return Reservation(self.attempted_posts, identity, self.model_request_upper_bound)


def validate_synthetic_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) - {"user_id", "message", "request_id", "session_id"}:
        raise ValueError("Only synthetic incremental chat fields are allowed")
    if type(payload.get("user_id")) is not int or payload["user_id"] not in {900001, 900002, 900003}:
        raise ValueError("Only registered synthetic users are allowed")
    if not isinstance(payload.get("message"), str) or not 1 <= len(payload["message"]) <= 2000 or not payload["message"].strip():
        raise ValueError("A bounded synthetic message is required")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ValueError("A bounded request ID is required")
    if "session_id" in payload and (not isinstance(payload["session_id"], str)
                                   or not re.fullmatch(r"[a-f0-9]{32}", payload["session_id"])):
        raise ValueError("A valid incremental session ID is required")


async def observe_budgeted_turn(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    assess: Callable[[dict], bool],
    budget: RequestBudget,
    *,
    on_attempt: Callable[[Reservation], None] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> ClientObservation:
    """Reserve before sending; errors/cancellation never refund or retry a POST.

    The caller must freeze the synthetic messages, use a client without transport
    retries/authentication replay and prevent a second invocation of the batch.
    A failed ledger callback aborts before sending, retaining the reservation.
    """
    validate_synthetic_payload(payload)
    reservation = budget.reserve(payload["request_id"])
    if on_attempt is not None:
        on_attempt(reservation)
    return await observe_client_turn(client, payload, assess, clock=clock)
