"""PR-review regressions crossing the real HTTP adapter and persistent agent."""

import json

import httpx
import pytest
from test_agent_api import catalog as catalog
from test_llm import completion

from app.agent.service import MealAgent
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore


async def test_attributed_allergy_survives_real_adapter_replace_and_explain(tmp_path, catalog):
    intents = {
        "我和爸爸两个人晚餐，爸爸花生过敏": {
            "action": "plan", "people": 2, "meal_type": "晚餐",
            "diner_updates": [{"diner": "爸爸", "attendance": True, "allergies": ["花生"]}],
        },
        "过敏限制照旧，换第二道": {"action": "replace", "replace_slot": 2},
        "解释一下这份菜单如何避开过敏食材": {"action": "explain"},
        "过敏限制照旧，另外妈妈有食物过敏": {"action": "plan"},
        "先推荐一份吧": {"action": "plan"},
    }

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        answer = (
            {"reason_ids": ["opening"]} if "facts" in payload else intents[payload["message"]]
        )
        return httpx.Response(200, json=completion(answer))

    store = SessionStore(tmp_path / "state.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = MealAgent(catalog, store, DeepSeekLLM("fake-test-secret", client=client))
        first = await agent.chat(3, next(iter(intents)))
        assert first.status == "ok"
        sid = first.conversation_state.session_id
        assert first.conversation_state.meal_constraints.allergies == []
        dad = next(d for d in first.conversation_state.diners if d.display_name == "爸爸")
        assert dad.allergies == ["花生"]

        second = await agent.chat(3, "过敏限制照旧，换第二道", sid)
        assert second.status == "ok"
        assert second.menu[0].recipe_id == first.menu[0].recipe_id
        assert second.menu[2].recipe_id == first.menu[2].recipe_id
        assert second.menu[1].recipe_id != first.menu[1].recipe_id
        assert second.conversation_state.constraints.allergies == ["花生"]
        assert all("花生" not in dish.ingredients for dish in second.menu)

        third = await agent.chat(3, "解释一下这份菜单如何避开过敏食材", sid)
        assert third.status == "ok"
        assert [dish.recipe_id for dish in third.menu] == [dish.recipe_id for dish in second.menu]
        assert not third.conversation_state.pending_allergy
        assert not any(event.name == "menu_modify" for event in third.tool_calls)

        fourth = await agent.chat(3, "过敏限制照旧，另外妈妈有食物过敏", sid)
        assert fourth.status == "clarification_required"
        assert fourth.menu == []
        # Even a restart and a vague request must not clear the genuinely missing fact.
        agent = MealAgent(catalog, store, agent.llm)
        fifth = await agent.chat(3, "先推荐一份吧", sid)
        assert fifth.status == "clarification_required"
        assert fifth.menu == []
        saved = store.get(sid, 3)
        assert saved.constraints.allergies == ["花生"]
        assert saved.pending_allergy


@pytest.mark.parametrize("initial_allergies", [["神秘酱料"], ["鸡蛋", "神秘酱料"]])
async def test_personal_pending_reference_and_resolution_cross_adapter_and_service(
    initial_allergies, tmp_path, catalog
):
    messages = [
        "我和爸爸两个人晚餐，爸爸对" + "和".join(initial_allergies) + "过敏",
        "爸爸的过敏限制照旧",
        "爸爸过敏的神秘酱料具体是芝麻",
    ]
    intents = dict(zip(messages, [
        {"action": "plan", "people": 2, "meal_type": "晚餐", "diner_updates": [{
            "diner": "爸爸", "attendance": True, "allergies": initial_allergies,
        }]},
        {"action": "plan"},
        {"action": "plan", "diner_updates": [{
            "diner": "爸爸", "allergy_clarifications": {"神秘酱料": ["芝麻"]},
        }]},
    ], strict=True))

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        answer = (
            {"reason_ids": ["opening"]} if "facts" in payload else intents[payload["message"]]
        )
        return httpx.Response(200, json=completion(answer))

    store = SessionStore(tmp_path / "pending.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        agent = MealAgent(catalog, store, adapter)
        first = await agent.chat(3, messages[0])
        assert first.status == "clarification_required"
        sid = first.conversation_state.session_id
        second = await agent.chat(3, messages[1], sid)
        assert second.status == "clarification_required"
        assert second.menu == []
        assert not second.conversation_state.pending_allergy
        # The original attributed pending term survives; no second anonymous fact is added.
        dad = next(d for d in second.conversation_state.diners if d.display_name == "爸爸")
        assert dad.pending_allergy_terms == ["神秘酱料"]
        agent = MealAgent(catalog, store, adapter)
        third = await agent.chat(3, messages[2], sid)
        assert third.status == "ok"
        dad = next(d for d in third.conversation_state.diners if d.display_name == "爸爸")
        assert dad.pending_allergy_terms == []
        assert "芝麻" in dad.allergies
        if "鸡蛋" in initial_allergies:
            assert "鸡蛋" in dad.allergies
            assert all("鸡蛋" not in dish.ingredients for dish in third.menu)


async def test_unnamed_personal_allergy_can_be_resolved_by_the_same_person(tmp_path, catalog):
    intents = {
        "我和爸爸两个人晚餐，爸爸过敏": {
            "action": "plan", "people": 2, "meal_type": "晚餐",
            "diner_updates": [{"diner": "爸爸", "attendance": True}],
        },
        "爸爸的过敏限制照旧": {"action": "plan"},
        "爸爸具体是鸡蛋过敏": {
            "action": "plan", "diner_updates": [{"diner": "爸爸", "allergies": ["鸡蛋"]}],
        },
    }

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        answer = (
            {"reason_ids": ["opening"]} if "facts" in payload else intents[payload["message"]]
        )
        return httpx.Response(200, json=completion(answer))

    store = SessionStore(tmp_path / "unnamed.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        agent = MealAgent(catalog, store, adapter)
        first = await agent.chat(3, next(iter(intents)))
        assert first.status == "clarification_required"
        sid = first.conversation_state.session_id
        assert not first.conversation_state.pending_allergy
        dad = next(d for d in first.conversation_state.diners if d.display_name == "爸爸")
        assert dad.pending_allergy
        second = await agent.chat(3, "爸爸的过敏限制照旧", sid)
        assert second.status == "clarification_required"
        assert not second.conversation_state.pending_allergy
        third = await agent.chat(3, "爸爸具体是鸡蛋过敏", sid)
        assert third.status == "ok"
        dad = next(d for d in third.conversation_state.diners if d.display_name == "爸爸")
        assert not dad.pending_allergy
        assert dad.allergies == ["鸡蛋"]
        assert all("鸡蛋" not in dish.ingredients for dish in third.menu)
