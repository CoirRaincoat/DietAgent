"""Budget and data-scope counterexamples use only synthetic in-memory HTTP."""

import asyncio

import httpx
import pytest

from evaluation.diagnostic_batch import BudgetExceeded, RequestBudget, observe_budgeted_turn


def payload(request_id="synthetic-turn", **changes):
    return {"user_id": 900001, "message": "1人晚餐，无忌口", "request_id": request_id, **changes}


@pytest.mark.parametrize("max_posts,max_model,allowed", [(1, 4, 1), (17, 2, 1), (17, 34, 17)])
async def test_allowance_is_reserved_before_post_and_cannot_be_exceeded(max_posts, max_model, allowed):
    sent, reservations = [], []

    def handler(request):
        assert len(reservations) == len(sent) + 1
        sent.append(request)
        return httpx.Response(200, json={})

    budget = RequestBudget(max_posts, max_model)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        for turn in range(allowed):
            result = await observe_budgeted_turn(client, payload(str(turn)), lambda body: True, budget,
                                                on_attempt=reservations.append)
            assert result.passed is True
        with pytest.raises(BudgetExceeded):
            await observe_budgeted_turn(client, payload("extra"), lambda body: True, budget)
    assert len(sent) == budget.attempted_posts == allowed
    assert budget.model_request_upper_bound == allowed * 2 <= max_model
    assert reservations[-1].model_request_upper_bound == allowed * 2


@pytest.mark.parametrize("max_posts,max_model", [(0, 34), (18, 34), (True, 34), ("17", 34),
                                               (17, 0), (17, 35), (17, True), (17, "34")])
def test_invalid_or_expanded_budget_is_rejected(max_posts, max_model):
    with pytest.raises(ValueError):
        RequestBudget(max_posts, max_model)


@pytest.mark.parametrize("changes", [{"user_id": 1}, {"user_id": True}, {"profile": {"raw": "SENSITIVE-raw"}},
                                     {"request_id": None}, {"message": " "}, {"session_id": "unknown"}])
async def test_non_synthetic_or_invalid_payload_never_reserves_or_sends(changes):
    sent = []
    budget = RequestBudget(17, 34)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sent.append(request)),
                                 base_url="http://synthetic") as client:
        with pytest.raises(ValueError):
            await observe_budgeted_turn(client, payload(**changes), lambda body: True, budget)
    assert sent == [] and budget.attempted_posts == budget.model_request_upper_bound == 0


async def test_duplicate_request_id_cannot_replay_cache_as_another_model_sample():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={})

    budget = RequestBudget(17, 34)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        await observe_budgeted_turn(client, payload(), lambda body: True, budget)
        with pytest.raises(BudgetExceeded):
            await observe_budgeted_turn(client, payload(), lambda body: True, budget)
    assert len(sent) == budget.attempted_posts == 1


@pytest.mark.parametrize("cancelled", [False, True])
async def test_timeout_and_cancellation_consume_the_reserved_attempt_without_retry(cancelled):
    sent = []

    def handler(request):
        sent.append(request)
        if cancelled:
            raise asyncio.CancelledError()
        raise httpx.ReadTimeout("SENSITIVE-error", request=request)

    budget = RequestBudget(17, 34)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await observe_budgeted_turn(client, payload(), lambda body: True, budget)
        else:
            result = await observe_budgeted_turn(client, payload(), lambda body: True, budget)
            assert result.error == "transport_error"
        with pytest.raises(BudgetExceeded):
            await observe_budgeted_turn(client, payload(), lambda body: True, budget)
    assert len(sent) == budget.attempted_posts == 1 and budget.model_request_upper_bound == 2


async def test_failed_persistent_ledger_callback_aborts_before_network_and_retains_reservation():
    sent = []
    budget = RequestBudget(17, 34)

    def broken_ledger(reservation):
        raise OSError("Synthetic unwritable ledger")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: sent.append(request)),
                                 base_url="http://synthetic") as client:
        with pytest.raises(OSError):
            await observe_budgeted_turn(client, payload(), lambda body: True, budget, on_attempt=broken_ledger)
    assert sent == [] and budget.attempted_posts == 1


async def test_overlapping_coroutines_cannot_reserve_more_than_the_remaining_single_slot():
    sent = []

    async def handler(request):
        sent.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={})

    budget = RequestBudget(1, 2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://synthetic") as client:
        results = await asyncio.gather(*(observe_budgeted_turn(client, payload(str(turn)), lambda body: True, budget)
                                         for turn in (1, 2)), return_exceptions=True)
    assert sum(isinstance(result, BudgetExceeded) for result in results) == 1
    assert len(sent) == budget.attempted_posts == 1
