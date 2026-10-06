"""Literal no-edit authority outranks model actions, not real safety assertions."""

import json

import httpx
import pytest

from app.agent.service import MealAgent
from app.domain.models import DinerUpdate, Intent
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_explain_intent_scope import PROTECTED
from tests.test_explain_intent_scope import catalog as scope_catalog
from tests.test_llm import completion


@pytest.fixture
def catalog():
    return scope_catalog.__wrapped__()


MESSAGES = [
    "解释一下不要香辣的含义，不要修改菜单。",
    "解释‘不要香辣’这个词，不要修改菜单。",
    "只解释这份菜单。",
    "请先不要换菜。",
    "当前菜单保持不变。",
    "只评价菜单，先别调整菜单。",
]


@pytest.mark.parametrize("action", ["plan", "replace", "reject", "clarify"])
@pytest.mark.parametrize("message", MESSAGES)
def test_explicit_readonly_text_outweighs_model_action(tmp_path, catalog, action, message):
    wrong = complete_intent(
        action=action,
        people=5,
        meal_type="早餐",
        dish_count=6,
        soup_count=1,
        preferences=["不要香辣"],
        excluded_ingredients=["鸡肉"],
        replace_slot=2,
        diner_updates=[DinerUpdate(diner="妈妈", allergies=["鱼"])],
    )
    llm = ScriptedLLM([complete_intent(), wrong])
    with client_for(tmp_path, catalog, llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口")).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        history = SessionStore(tmp_path / "state.db").recommendation_history(3)
        payload = dict(user_id=3, session_id=sid, message=message, request_id="readonly")
        second = client.post("/chat", json=payload).json()
        assert second["status"] == "ok"
        for field in PROTECTED:
            assert second["conversation_state"][field] == first["conversation_state"][field], field
        assert second["menu"] == first["menu"]
        assert not {"recipe_search", "menu_modify"} & {r["name"] for r in second["tool_calls"]}
        assert SessionStore(tmp_path / "state.db").recommendation_history(3) == history
        assert client.post("/chat", json=payload).json() == second
        assert llm.parse_calls == 2


def test_readonly_conflicting_edit_needs_confirmation_without_applying_fields(tmp_path, catalog):
    with client_for(
        tmp_path,
        catalog,
        ScriptedLLM(
            [complete_intent(), Intent(action="replace", replace_slot=2, preferences=["酸甜"])]
        ),
    ) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口")).json()
        second = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message="不要修改菜单，但只换第2道。",
            ),
        ).json()
        assert second["status"] == "clarification_required" and not second["menu"]
        for field in PROTECTED:
            assert second["conversation_state"][field] == first["conversation_state"][field], field
        assert "同时" in second["reason"] and "换菜" in second["reason"]
        assert not second["tool_calls"]


def test_local_permission_is_not_a_global_readonly_order(tmp_path, catalog):
    with client_for(
        tmp_path,
        catalog,
        ScriptedLLM([complete_intent(), Intent(action="replace", replace_slot=2)]),
    ) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口")).json()
        second = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message="只换第2道，其他菜不要修改。",
            ),
        ).json()
        assert second["status"] == "ok"
        ids_a, ids_b = [[r["recipe_id"] for r in x["menu"]] for x in (first, second)]
        assert ids_a[0] == ids_b[0] and ids_a[2] == ids_b[2] and ids_a[1] != ids_b[1]


@pytest.mark.parametrize(
    "safety_message",
    [
        "只解释菜单，不要修改菜单。另外我对虾过敏。",
        "不要修改菜单。妈妈还有食物过敏。",
    ],
)
def test_literal_readonly_order_cannot_waive_a_new_allergy(tmp_path, catalog, safety_message):
    with client_for(
        tmp_path, catalog, ScriptedLLM([complete_intent(), Intent(action="plan")])
    ) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口")).json()
        second = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message=safety_message,
            ),
        ).json()
        assert second["status"] == "clarification_required" and not second["menu"]
        assert second["conversation_state"]["pending_allergy"] or any(
            d["pending_allergy"] for d in second["conversation_state"]["diners"]
        )
        assert second["conversation_state"]["menu_ids"] == first["conversation_state"]["menu_ids"]
        assert not any(t["name"] == "menu_modify" for t in second["tool_calls"])


def test_literal_readonly_keeps_actual_nonspicy_fact(tmp_path, catalog):
    with client_for(
        tmp_path, catalog, ScriptedLLM([complete_intent(), Intent(action="plan", no_spicy=True)])
    ) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口")).json()
        second = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message="不要修改菜单。另外我不吃辣。",
            ),
        ).json()
        assert second["conversation_state"]["constraints"]["no_spicy"]
        assert not {"recipe_search", "menu_modify"} & {r["name"] for r in second["tool_calls"]}


@pytest.mark.parametrize("action", ["plan", "replace", "reject", "clarify"])
async def test_real_adapter_wrong_action_is_bounded_without_paid_calls(tmp_path, catalog, action):
    messages = ["1人晚餐，没有其他忌口", "解释‘不要香辣’这个词，不要修改菜单。"]
    answers = {
        messages[0]: complete_intent().model_dump(mode="json"),
        messages[1]: complete_intent(
            action=action,
            people=5,
            preferences=["不要香辣"],
            replace_slot=2 if action == "replace" else None,
            clarification="请补充要求" if action == "clarify" else None,
        ).model_dump(mode="json"),
    }
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        answer = {"reason_ids": ["opening"]} if "facts" in payload else answers[payload["message"]]
        return httpx.Response(200, json=completion(answer))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = MealAgent(
            catalog,
            SessionStore(tmp_path / "state.db"),
            DeepSeekLLM("fake-test-secret", client=client),
        )
        first = await agent.chat(3, messages[0], request_id="first")
        second = await agent.chat(3, messages[1], first.conversation_state.session_id, "explain")
        assert first.status == second.status == "ok"
        for field in PROTECTED:
            assert getattr(second.conversation_state, field) == getattr(
                first.conversation_state, field
            )
        assert [r.recipe_id for r in first.menu] == [r.recipe_id for r in second.menu]
        assert not {"recipe_search", "menu_modify"} & {t.name for t in second.tool_calls}
        replay = await agent.chat(3, messages[1], first.conversation_state.session_id, "explain")
        assert replay == second and len(calls) == 2  # Intent only; explanation local.


@pytest.mark.parametrize("quoted", [False, True])
def test_pending_global_safety_answer_can_be_recorded_without_planning(tmp_path, catalog, quoted):
    mapping = "我的籽类过敏原只指花生"
    message = ("例如‘" + mapping + "’" if quoted else mapping) + "，只解释菜单。"
    llm = ScriptedLLM(
        [
            complete_intent(allergies=["籽类"]),
            Intent(action="plan", allergy_clarifications={"籽类": ["花生"]}, people=5),
        ]
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，我对籽类过敏")).json()
        assert first["status"] == "clarification_required"
        second = client.post(
            "/chat",
            json=dict(
                user_id=3, session_id=first["conversation_state"]["session_id"], message=message
            ),
        ).json()
        assert second["status"] == "clarification_required" and not second["menu"]
        assert second["conversation_state"]["pending_plan"]
        assert second["conversation_state"]["constraints"]["people"] == 1
        assert not second["tool_calls"] or {t["name"] for t in second["tool_calls"]} == {
            "health_check"
        }
        if quoted:
            assert second["conversation_state"]["pending_allergy_terms"] == ["籽类"]
            assert not second["conversation_state"]["constraints"]["allergies"]
        else:
            assert not second["conversation_state"]["pending_allergy_terms"]
            assert second["conversation_state"]["constraints"]["allergies"] == ["花生"]
