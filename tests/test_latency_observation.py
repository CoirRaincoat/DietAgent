"""Synthetic clocks and in-memory transports distinguish client timing phases."""

import asyncio
from dataclasses import asdict

import httpx
import pytest

from evaluation.latency_observation import observe_client_turn


class Clock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


async def test_http_return_and_json_are_measured_before_slow_local_assessment():
    posted, assessed = [], []

    def handler(request):
        posted.append(request)
        return httpx.Response(200, json={"status": "ok", "secret": "SENSITIVE-response"})

    def assess(body):
        assessed.append(body["status"])
        return True

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        result = await observe_client_turn(client, {"message": "SENSITIVE-message"}, assess,
                                           clock=Clock(10, 10.4, 10.5, 12.5))
    assert (result.http_ms, result.decode_ms, result.assessment_ms, result.complete_ms) == (400, 100, 2000, 2500)
    assert result.passed is True and result.error is None
    assert len(posted) == 1 and assessed == ["ok"]
    assert "SENSITIVE" not in str(asdict(result))


@pytest.mark.parametrize("answer", [True, False, "true", 1, None])
async def test_assessment_only_accepts_explicit_booleans(answer):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
                                 base_url="http://synthetic") as client:
        result = await observe_client_turn(client, {}, lambda body: answer, clock=Clock(1, 1.1, 1.2, 1.3))
    assert result.passed == (answer if type(answer) is bool else None)
    assert result.error == (None if type(answer) is bool else "invalid_assessment")


@pytest.mark.parametrize("body,error", [(b"not json", "json_error"), (b"[]", "invalid_response")])
async def test_bad_body_does_not_run_oracle_or_expose_response(body, error):
    assessed = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
                                 base_url="http://synthetic") as client:
        result = await observe_client_turn(client, {}, lambda value: assessed.append(value), clock=Clock(1, 1.1, 1.2))
    assert result.error == error and result.passed is None and assessed == []
    assert result.assessment_ms == 0 and result.complete_ms == 200


async def test_assessment_exception_is_counted_and_sanitized_without_retry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    def failing(body):
        raise ValueError("SENSITIVE-oracle-error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        result = await observe_client_turn(client, {}, failing, clock=Clock(1, 1.1, 1.2, 1.5))
    assert result.error == "assessment_error" and result.assessment_ms == 300
    assert len(requests) == 1 and "SENSITIVE" not in str(asdict(result))


async def test_timeout_is_one_attempt_and_preserves_unknown_response_and_assessment():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("SENSITIVE-transport-error", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        result = await observe_client_turn(client, {}, lambda body: True, clock=Clock(1, 3))
    assert result.http_ms == result.complete_ms == 2000
    assert result.http_status is None and result.passed is None and result.error == "transport_error"
    assert len(requests) == 1 and "SENSITIVE" not in str(asdict(result))


async def test_redirect_is_not_followed_even_when_injected_client_defaults_to_follow():
    requests = []
    assessed = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "http://synthetic/second-post"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic", follow_redirects=True) as client:
        result = await observe_client_turn(client, {}, lambda body: assessed.append(body), clock=Clock(1, 1.1))
    assert len(requests) == 1 and assessed == [] and result.error == "http_error" and result.http_status == 307


async def test_client_cancellation_propagates_without_assessment_or_second_request():
    requests = []

    def handler(request):
        requests.append(request)
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        with pytest.raises(asyncio.CancelledError):
            await observe_client_turn(client, {}, lambda body: True, clock=Clock(1))
    assert len(requests) == 1
