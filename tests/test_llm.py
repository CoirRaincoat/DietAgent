"""Provider boundary tests use mocked HTTP, never live credentials."""

import json

import httpx
import pytest

from app.domain.models import Constraints, Diner, SessionState, UserProfile
from app.infrastructure.llm.base import LLMOutputError, LLMUnavailable
from app.infrastructure.llm.deepseek import DeepSeekLLM


@pytest.fixture
def profile():
    return UserProfile(
        data_scope="synthetic", user_id=1, age=30, sex="女", height_cm=160, weight_kg=55, bmi=21.5,
        allergies=["花生"], health_goals=["控糖"], preferences=["清淡"],
        raw={"private_note": "private-raw-marker"},
        measurements={"血压_mmHg": "private-measurement-marker"},
    )


@pytest.fixture
def state():
    return SessionState(
        session_id="test-session", user_id=1,
        constraints=Constraints(allergies=["花生"], no_spicy=True),
        menu_ids=["recipe-one", "recipe-two", "recipe-three"], menu_valid=True,
    )


def completion(content, finish_reason="stop"):
    return {"choices": [{
        "finish_reason": finish_reason,
        "message": {
            "role": "assistant",
            "content": json.dumps(content, ensure_ascii=False)
            if isinstance(content, dict) else content,
        },
    }]}


def adapter_for(body, status=200):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    return DeepSeekLLM(api_key="fake-test-secret", client=client), client


async def test_parse_contract_and_minimal_profile(profile, state):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=completion({
            "action": "plan", "dish_count": 5, "soup_count": 1,
            "allergies": ["虾"], "no_spicy": True,
        }))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        intent = await adapter.parse("四菜一汤，我对虾过敏，别放辣椒", state, profile)
        assert intent.dish_count == 5
        assert intent.soup_count == 1
        assert intent.allergies == ["虾"]
        assert intent.people is None
        assert state.constraints.allergies == ["花生"]
        request = observed[0]
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer fake-test-secret"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "disabled"}
        assert "json" in payload["messages"][0]["content"]
        context = json.loads(payload["messages"][1]["content"])
        assert context["profile"]["allergies"] == ["花生"]
        assert context["current_menu"][1] == {"slot": 2, "recipe_id": "recipe-two"}
        assert "private-raw-marker" not in str(payload)
        assert "private-measurement-marker" not in str(payload)
        assert "fake-test-secret" not in str(payload)


async def test_parse_attributed_diner_updates_and_send_stable_diner_context(profile, state):
    state.diners = [
        Diner(
            diner_id="profile-1",
            display_name="用户",
            aliases=["用户", "我"],
            profile_owner=True,
            allergies=["花生"],
        )
    ]
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=completion({
            "action": "plan",
            "people": 2,
            "diner_updates": [{
                "diner": "妈妈",
                "aliases": ["我妈"],
                "attendance": True,
                "no_spicy": True,
            }],
        }))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        intent = await adapter.parse("我和妈妈两人吃，她不吃辣", state, profile)

    assert intent.people == 2
    assert intent.diner_updates[0].diner == "妈妈"
    assert intent.diner_updates[0].no_spicy is True
    context = json.loads(json.loads(observed[0].content)["messages"][1]["content"])
    assert context["diners"][0]["diner_id"] == "profile-1"
    assert context["diners"][0]["allergies"] == ["花生"]
    assert "private-raw-marker" not in str(context)


@pytest.mark.parametrize("content", [
    "", "  ", "not json", "```json\n{}\n```", "[]", "null", "{}",
    '{"action":"plan","action":"replace"}',
    '{"action":"plan","people":NaN}',
    {"action": "plan", "people": "2"},
    {"action": "plan", "people": True},
    {"action": "plan", "people": 9},
    {"action": "plan", "diner_updates": [{"diner": "爸爸", "no_spicy": False}]},
    {"action": "plan", "unexpected": "field"},
    {"action": "plan", "allergies": [""]},
    {"action": "plan", "allergies": "虾"},
    {"action": "plan", "meal_type": "午夜大餐"},
    {"action": "plan", "dish_count": 1, "soup_count": 2},
    {"action": "plan", "replace_slot": 1},
    {"action": "plan", "no_spicy": False},
    {"action": "clarify"},
    {"action": "replace"},
    {"action": "replace", "replace_slot": 4},
    {"action": "replace", "replace_name": " "},
])
async def test_reject_invalid_or_unsafe_intents(content, profile, state):
    adapter, client = adapter_for(completion(content))
    async with client:
        with pytest.raises(LLMOutputError) as caught:
            await adapter.parse("安排一下", state, profile)
        assert caught.value.code == "invalid_output"


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
async def test_reject_truncated_or_unfinished_json(finish_reason, profile, state):
    adapter, client = adapter_for(completion({"action": "plan"}, finish_reason))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("安排一下", state, profile)


@pytest.mark.parametrize("body", [
    {}, [], {"choices": []}, {"choices": [None]},
    {"choices": [completion({})["choices"][0], completion({})["choices"][0]]},
    {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
])
async def test_reject_invalid_provider_envelope(body, profile, state):
    adapter, client = adapter_for(body)
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("安排一下", state, profile)


@pytest.mark.parametrize("status,code", [
    (401, "authentication"), (403, "authentication"), (429, "rate_limited"),
    (500, "upstream"), (503, "upstream"), (400, "request_rejected"),
    (302, "request_rejected"),
])
async def test_safe_http_errors(status, code, profile, state):
    adapter, client = adapter_for({"error": "upstream-secret fake-test-secret"}, status)
    async with client:
        with pytest.raises(LLMUnavailable) as caught:
            await adapter.parse("安排一下", state, profile)
        assert caught.value.code == code
        assert "secret" not in str(caught.value)
        assert "secret" not in repr(caught.value)


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network"),
])
async def test_safe_transport_errors(exception, code, profile, state):
    def handler(request):
        raise exception("upstream-secret fake-test-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        with pytest.raises(LLMUnavailable) as caught:
            await adapter.parse("安排一下", state, profile)
        assert caught.value.code == code
        assert "secret" not in str(caught.value)
        assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("target", [{"replace_slot": 2}, {"replace_name": "番茄炒蛋"}])
async def test_replace_preserves_explicit_target(target, profile, state):
    adapter, client = adapter_for(completion({"action": "replace", **target}))
    async with client:
        result = await adapter.parse("换一下", state, profile)
        for field, value in target.items():
            assert getattr(result, field) == value


@pytest.mark.parametrize("action", ["replace", "explain"])
async def test_existing_menu_required(action, profile):
    result = {"action": action}
    if action == "replace":
        result["replace_slot"] = 1
    adapter, client = adapter_for(completion(result))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse(
                "解释或替换", SessionState(session_id="empty", user_id=1), profile
            )


async def test_clarification_valid(profile, state):
    adapter, client = adapter_for(completion({
        "action": "clarify", "clarification": "请选择需要替换的菜品。",
    }))
    async with client:
        assert (await adapter.parse("换一道", state, profile)).action == "clarify"


@pytest.mark.parametrize("message", ["我对虾过敏", "有食物过敏", "虾不过敏，但花生过敏"])
async def test_omitted_explicit_allergy_requests_clarification(message, profile, state):
    adapter, client = adapter_for(completion({"action": "plan"}))
    async with client:
        intent = await adapter.parse(message, state, profile)
        assert intent.action == "clarify"
        assert "过敏" in intent.clarification


@pytest.mark.parametrize("message", ["没有过敏", "虾不过敏", "无食物过敏史"])
async def test_negative_allergy_mention_does_not_add_allergy(message, profile, state):
    adapter, client = adapter_for(completion({"action": "plan"}))
    async with client:
        intent = await adapter.parse(message, state, profile)
        assert not intent.allergies
        assert state.constraints.allergies == ["花生"]


async def test_explanation_returns_only_ordered_verified_ids():
    adapter, client = adapter_for(completion({"reason_ids": ["taste", "source"]}))
    async with client:
        assert await adapter.explain({"source": "菜品来自菜谱库。", "taste": "已过滤辣椒。"}) == [
            "taste", "source",
        ]


@pytest.mark.parametrize("content", [
    {"reason_ids": ["invented"]}, {"reason_ids": ["source", "source"]},
    {"reason_ids": []}, {"reason_ids": [1]},
    {"reason_ids": ["source"], "explanation": "虚构热量100千卡"},
])
async def test_explanation_rejects_unverified_content(content):
    adapter, client = adapter_for(completion(content))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.explain({"source": "菜品来自菜谱库。"})


async def test_empty_facts_skip_http_and_external_client_is_not_closed():
    def handler(request):
        pytest.fail("No HTTP call expected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        assert await adapter.explain({}) == []
        await adapter.aclose()
        assert not client.is_closed


async def test_owned_client_is_closed():
    adapter = DeepSeekLLM("fake-test-secret")
    await adapter.aclose()
    assert adapter._client.is_closed


@pytest.mark.parametrize("message", ["时间不限", "取消时间限制", "不赶时间", "不限时间", "不限制做饭时间", "取消做菜的时间上限", "不限定时间", "不用设定时间限制"])
async def test_explicit_time_withdrawal_accepted(message, profile, state):
    adapter, client = adapter_for(completion({"action": "plan", "clear_time_limit": True}))
    async with client:
        assert (await adapter.parse(message, state, profile)).clear_time_limit is True


async def test_unstated_time_withdrawal_rejected(profile, state):
    adapter, client = adapter_for(completion({"action": "plan", "clear_time_limit": True}))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("随便安排吧", state, profile)


async def test_conflicting_time_constraints_rejected(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "clear_time_limit": True, "max_minutes": 30,
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("取消时间限制", state, profile)

@pytest.mark.parametrize("api_key", ["", "   "])
async def test_missing_api_key_is_deferred_until_request(api_key, profile, state):
    def handler(request):
        pytest.fail("A missing API key must not send an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM(api_key, client=client)
        with pytest.raises(LLMUnavailable) as caught:
            await adapter.parse("安排晚餐", state, profile)
        assert caught.value.code == "authentication"

@pytest.mark.parametrize("message", ["没有其他过敏", "无额外食物过敏"])
async def test_no_additional_allergy_is_not_an_unspecified_allergy(message, profile, state):
    adapter, client = adapter_for(completion({"action": "plan", "restrictions_confirmed": True}))
    async with client:
        intent = await adapter.parse(message, state, profile)
    assert intent.action == "plan"
    assert intent.allergies == []


async def test_original_profile_is_blocked_before_any_external_request(profile, state):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion({"action": "plan"}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        original = profile.model_copy(update={"data_scope": "original"})
        with pytest.raises(LLMUnavailable) as caught:
            await adapter.parse("推荐", state, original)
        assert caught.value.code == "original_profile_blocked"
        assert requests == []


async def test_allergy_resolution_must_reference_an_existing_pending_term(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "allergy_clarifications": {"not_pending": ["芝麻"]},
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("具体是芝麻", state, profile)


async def test_allergy_resolution_preserves_the_named_mapping(profile, state):
    state.pending_allergy_terms = ["某种调料"]
    adapter, client = adapter_for(completion({
        "action": "plan", "allergies": ["芝麻"],
        "allergy_clarifications": {"某种调料": ["芝麻"]},
    }))
    async with client:
        intent = await adapter.parse("具体是芝麻", state, profile)
    assert intent.allergy_clarifications == {"某种调料": ["芝麻"]}
