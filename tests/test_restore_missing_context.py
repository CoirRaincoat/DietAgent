"""Synthetic API counterexamples for restore requests before meal confirmation."""

from uuid import uuid4

from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.menu_restore import record_menu_revision, record_rejection_action
from app.domain.models import Constraints, Intent, RestoreMenuIntent, SessionState
from app.infrastructure.sessions import SessionStore


def restore_intent(**changes):
    return Intent(restore_menu=RestoreMenuIntent(reference="original"), **changes)


def post(client, message, **changes):
    payload = {"user_id": 3, "request_id": uuid4().hex, "message": message}
    payload.update(changes)
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def assert_no_history(body, confirmed_fields=()):
    assert body["status"] == "clarification_required"
    assert "没有记录到可恢复的历史菜单" in body["reason"]
    assert body["menu"] == body["replacement_suggestions"] == []
    state = body["conversation_state"]
    assert state["menu_ids"] == state["menu_history"] == []
    assert state["rejected_recipe_ids"] == state["rejection_actions"] == []
    assert state["menu_valid"] is False
    assert set(state["confirmed_fields"]) == set(confirmed_fields)
    needed = {"request", "people", "meal_type"}
    if "restrictions" not in confirmed_fields:
        needed.add("restrictions")
    assert {q["field"] for q in body["clarification_questions"]} >= needed


def test_first_restore_explains_missing_history_and_replay_does_not_plan(tmp_path, catalog):
    llm = ScriptedLLM([restore_intent()])
    request_id = uuid4().hex
    with client_for(tmp_path, catalog, llm) as client:
        first = post(client, "恢复第一轮原菜单。", request_id=request_id)
        assert_no_history(first)
        replay = post(client, "恢复第一轮原菜单。", request_id=request_id)
    assert replay == first
    assert llm.parse_calls == 1
    assert first["tool_calls"] == []


def test_empty_history_clarification_can_be_followed_by_new_meal(tmp_path, catalog):
    llm = ScriptedLLM([restore_intent(), complete_intent(dish_count=3, soup_count=0)])
    with client_for(tmp_path, catalog, llm) as client:
        first = post(client, "恢复第一轮原菜单。")
        assert_no_history(first)
        second = post(client, "1人吃晚餐，3道菜，不要汤，没有其他忌口。",
                      session_id=first["conversation_state"]["session_id"])
    assert second["status"] == "ok"
    assert len(second["menu"]) == 3
    assert second["conversation_state"]["revision"] == 2
    assert len(second["conversation_state"]["menu_history"]) == 1
    assert second["conversation_state"]["menu_history"][0]["source"] == "planned_menu"
    assert second["conversation_state"]["rejection_actions"] == []


def test_valid_history_does_not_undo_rejections_before_context_confirmation(tmp_path, catalog):
    state = SessionState(session_id=uuid4().hex, user_id=3, constraints=Constraints())
    original = ["test_0", "test_1", "test_2"]
    replacement = ["test_3", "test_4", "test_5"]
    state.revision = 1
    record_menu_revision(state, original)
    record_rejection_action(state, original)
    state.rejected_recipe_ids = original[:]
    state.revision = 2
    record_menu_revision(state, replacement)
    state.menu_ids = replacement[:]
    state.menu_valid = True
    before_actions = [action.model_dump() for action in state.rejection_actions]
    SessionStore(tmp_path / "state.db").save(state, None)
    with client_for(tmp_path, catalog, ScriptedLLM([restore_intent()])) as client:
        body = post(client, "恢复第一轮原菜单。", session_id=state.session_id)
    assert body["status"] == "clarification_required"
    assert body["menu"] == []
    after = body["conversation_state"]
    assert after["menu_ids"] == replacement
    assert after["rejected_recipe_ids"] == original
    assert after["rejection_actions"] == before_actions
    assert after["confirmed_fields"] == []


def test_empty_history_request_preserves_new_known_allergy(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM([restore_intent(allergies=["花生"])])) as client:
        body = post(client, "我对花生过敏，恢复第一轮原菜单。")
    assert_no_history(body, confirmed_fields=("restrictions",))
    assert "花生" in body["conversation_state"]["constraints"]["allergies"]


def test_pending_allergy_still_has_priority_over_empty_history(tmp_path, catalog):
    unknown = "未映射的合成调料"
    with client_for(tmp_path, catalog, ScriptedLLM([restore_intent(allergies=[unknown])])) as client:
        body = post(client, f"我对{unknown}过敏，恢复第一轮原菜单。")
    assert body["status"] == "clarification_required"
    assert body["menu"] == []
    assert body["clarification_questions"][0]["field"] == "allergy"
    assert unknown in body["reason"]
    assert unknown in body["conversation_state"]["pending_allergy_terms"]
    assert body["conversation_state"]["menu_history"] == []
