"""Literal initial-plan context answers; no inferred context or safety override."""

import pytest
from pydantic import ValidationError

from app.agent.clarification import missing_questions
from app.agent.context_answers import grounded_context_answer
from app.agent.response_copy import clarification_copy
from app.domain.models import DinerUpdate, Intent, SessionState
from tests.test_agent_api import ScriptedLLM, client_for
from tests.test_agent_api import catalog as base_catalog


def pending(**values):
    state = SessionState(
        session_id="a" * 32,
        user_id=3,
        pending_plan=True,
        pending_fields=["people", "meal_type", "restrictions"],
        **values,
    )
    state.pending_clarification = clarification_copy(missing_questions(state))
    return state


@pytest.mark.parametrize(
    "message,people,meal,ack",
    [
        ("2个人", 2, None, False),
        ("两个人午餐", 2, "午餐", False),
        ("我们共四人吃晚餐", 4, "晚餐", False),
        ("8位下午茶", 8, "下午茶", False),
        ("午餐", None, "午餐", False),
        ("没有其他忌口", None, None, True),
        ("2人午餐，没有其他忌口", 2, "午餐", True),
        ("2人；午餐；按档案忌口", 2, "午餐", True),
        ("2人午餐，没有", 2, "午餐", True),
    ],
)
def test_finite_context_answer_does_not_mutate_state(message, people, meal, ack):
    state = pending()
    before = state.model_dump()
    answer = grounded_context_answer(message, state)
    assert (
        answer.model_dump()
        == Intent(people=people, meal_type=meal, restrictions_confirmed=ack).model_dump()
    )
    assert answer._context_answer_ack == ack
    assert state.model_dump() == before


@pytest.mark.parametrize(
    "message",
    [
        "没有",
        "随便",
        "都可以",
        "2人或3人",
        "9人午餐，没有其他忌口",
        "0人",
        "2人，3人",
        "早餐，午餐",
        "不是没有忌口",
        "2人午餐吗？",
        "没有其他忌口吗？",
        "“2人午餐，没有其他忌口”",
        "如果两个人午餐，没有其他忌口",
        "报告内容：2人午餐，没有其他忌口",
        "文档：2人午餐，没有其他忌口",
        "两个人午餐，我虾过敏",
        "2人午餐，不要辣",
        "2人午餐，只换第1道",
        "暂不规划",
        "2人午餐，继续",
        "2人午餐，取消清淡偏好",
        "只解释，2人午餐",
    ],
)
def test_unbound_ambiguous_quoted_or_mixed_reply_is_not_authority(message):
    assert grounded_context_answer(message, pending()) is None


@pytest.mark.parametrize(
    "field", ["allergy", "time_limit", "request", "method_meal_priority", "flavor_resolution"]
)
def test_other_pending_questions_cannot_be_resolved(field):
    state = pending()
    state.pending_fields.append(field)
    assert grounded_context_answer("2人午餐，没有其他忌口", state) is None


@pytest.mark.parametrize("kind", ["cold", "menu", "no_questions"])
def test_reply_cannot_create_cold_plan_or_edit_an_old_menu(kind):
    state = pending()
    if kind == "cold":
        state.pending_plan = False
    elif kind == "menu":
        state.menu_ids = ["existing"]
    else:
        state.pending_fields = []
    assert grounded_context_answer("2人午餐，没有其他忌口", state) is None


def test_short_no_is_only_bound_to_remaining_restrictions():
    state = pending(confirmed_fields=["people", "meal_type"])
    state.pending_fields = ["restrictions"]
    answer = grounded_context_answer("没有", state)
    assert answer is not None and answer.restrictions_confirmed and answer._context_answer_ack


def test_ack_proof_is_not_a_model_json_field():
    answer = grounded_context_answer("2人午餐，没有", pending())
    assert answer is not None and answer._context_answer_ack
    assert "_context_answer_ack" not in answer.model_dump()
    with pytest.raises(ValidationError):
        Intent.model_validate({"restrictions_confirmed": True, "_context_answer_ack": True})


def test_context_fields_do_not_authorize_answering_an_unrelated_model_question():
    state = pending()
    state.pending_clarification = "请先确认补气血目标，当前数据不能验证这种效果。"
    assert grounded_context_answer("2人午餐，没有其他忌口", state) is None


@pytest.mark.parametrize("action", ["plan", "clarify"])
def test_combined_bare_answer_confirms_only_the_now_unambiguous_last_question(
    tmp_path, catalog, action
):
    llm = ScriptedLLM([Intent(), Intent(action=action)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "安排一餐"}).json()
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "2人午餐，没有",
            },
        ).json()
    assert result["status"] == "ok"
    assert result["conversation_state"]["confirmed_fields"] == [
        "people",
        "meal_type",
        "restrictions",
    ]


@pytest.fixture
def catalog():
    return base_catalog.__wrapped__()


@pytest.mark.parametrize("mode", ["missing", "clarify", "reject", "explain"])
def test_reply_to_pending_initial_plan_uses_literal_context_not_model_mode(tmp_path, catalog, mode):
    answer = (
        Intent()
        if mode == "missing"
        else Intent(action=mode, clarification="人数餐次未明确" if mode == "clarify" else None)
    )
    llm = ScriptedLLM([Intent(preferences=["清淡"], dish_count=3), answer, answer, answer])
    with client_for(tmp_path, catalog, llm) as client:
        start = client.post(
            "/chat", json={"user_id": 3, "message": "帮我安排3道菜，清淡一点"}
        ).json()
        assert start["status"] == "clarification_required"
        sid = start["conversation_state"]["session_id"]
        for message, fields in [
            ("2个人", ["meal_type", "restrictions"]),
            ("午餐", ["restrictions"]),
            ("没有其他忌口", []),
        ]:
            response = client.post(
                "/chat", json={"user_id": 3, "message": message, "session_id": sid}
            ).json()
            assert response["conversation_state"]["pending_fields"] == fields
            if fields:
                assert response["menu"] == [] and response["tool_calls"] == []
        assert response["status"] == "ok"
        assert response["conversation_state"]["constraints"]["people"] == 2
        assert response["conversation_state"]["constraints"]["meal_type"] == "午餐"
        assert "清淡" in response["conversation_state"]["constraints"]["preferences"]
        assert response["conversation_state"]["constraints"]["health_goals"] == ["增肌"]
        assert len(response["menu"]) == 3


def test_new_safety_in_mixed_reply_stays_on_additive_path(tmp_path, catalog):
    llm = ScriptedLLM([Intent(), Intent(allergies=["鸡蛋"])])
    with client_for(tmp_path, catalog, llm) as client:
        start = client.post("/chat", json={"user_id": 3, "message": "安排一餐"}).json()
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": start["conversation_state"]["session_id"],
                "message": "2人午餐，我鸡蛋过敏",
            },
        ).json()
    assert result["status"] == "clarification_required"
    assert result["conversation_state"]["constraints"]["allergies"] == ["鸡蛋"]
    assert result["menu"] == []


def test_model_rejection_and_fabricated_facts_are_not_context_answer_authority(tmp_path, catalog):
    catalog.profiles[3].allergies = ["虾"]
    wrong = Intent(
        action="reject",
        people=8,
        meal_type="早餐",
        no_spicy=False,
        inventory=["虾"],
        preferences=["便当"],
        health_goals=["控糖"],
        diner_updates=[DinerUpdate(diner="妈妈", allergies=["花生"])],
    )
    llm = ScriptedLLM([Intent(no_spicy=True, preferences=["清淡"], dish_count=3), wrong])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "安排3道菜，清淡，不吃辣"}
        ).json()
        payload = {
            "user_id": 3,
            "session_id": first["conversation_state"]["session_id"],
            "message": "2人午餐",
            "request_id": "answer",
        }
        result = client.post("/chat", json=payload).json()
        assert client.post("/chat", json=payload).json() == result
    state = result["conversation_state"]
    constraints = state["constraints"]
    assert result["status"] == "ok"
    assert constraints["people"] == 2 and constraints["meal_type"] == "午餐"
    assert constraints["no_spicy"] and constraints["allergies"] == ["虾"]
    assert constraints["preferences"] == ["清淡"] and constraints["health_goals"] == ["增肌"]
    assert constraints["inventory"] is None and state["rejected_recipe_ids"] == []
    assert len(state["diners"]) == 1 and not state["pending_fields"]


def test_context_reply_does_not_resolve_unknown_allergen(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋", "某种调料"]
    llm = ScriptedLLM([Intent(), Intent(people=2, meal_type="午餐", restrictions_confirmed=True)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "安排一餐"}).json()
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "2人午餐，没有其他忌口",
            },
        ).json()
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert "allergy" in result["conversation_state"]["pending_fields"]
    assert "鸡蛋" in result["conversation_state"]["constraints"]["allergies"]
    assert any(
        "某种调料" in d["pending_allergy_terms"] for d in result["conversation_state"]["diners"]
    )


def test_unrelated_model_question_is_not_replaced_by_context_completion(tmp_path, catalog):
    question = "当前数据不能验证补气血，请核对是否改为普通膳食。"
    llm = ScriptedLLM(
        [
            Intent(clarification=question, health_goals=["补气血"]),
            Intent(action="clarify", clarification=question),
        ]
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "安排一餐，希望补气血"}).json()
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "2人午餐，没有其他忌口",
            },
        ).json()
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["reason"] == question
