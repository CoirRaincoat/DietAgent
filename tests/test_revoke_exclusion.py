"""Offline tests for confirmed ordinary-exclusion revocation (revoke_exclusion)."""

import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.revoke_exclusion import apply_revoke_exclusion
from app.domain.models import (
    Constraints,
    Diner,
    DinerUpdate,
    Intent,
    SessionState,
)


def owner_diner(allergies=("花生",)):
    return Diner(
        diner_id="owner", display_name="用户", aliases=["用户", "我", "本人"],
        profile_owner=True, allergies=list(allergies),
    )


def make_state(exclusions=("鸡蛋",), allergies=("花生",), diners=None):
    meal = Constraints(excluded_ingredients=list(exclusions))
    diners = diners or [owner_diner(allergies)]
    return SessionState(
        session_id="a" * 32, user_id=900002,
        meal_constraints=meal,
        constraints=Constraints(
            excluded_ingredients=list(exclusions), allergies=list(allergies),
        ),
        diners=diners,
        confirmed_fields=["people", "meal_type", "restrictions"],
    )


def test_request_creates_pending_without_mutating():
    state = make_state(exclusions=["鸡蛋"])
    issue = apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋"]), "取消鸡蛋忌口")
    assert issue is not None
    assert state.pending_revoke_exclusion is not None
    assert state.pending_revoke_exclusion.targets == ["鸡蛋"]
    assert state.meal_constraints.excluded_ingredients == ["鸡蛋"]  # unchanged


def test_confirm_removes_only_requested_exclusions():
    state = make_state(exclusions=["鸡蛋", "牛奶", "香菜"])
    apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋", "牛奶"]), "取消鸡蛋和牛奶")
    assert state.pending_revoke_exclusion is not None
    result = apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert result is None
    assert state.pending_revoke_exclusion is None
    assert state.meal_constraints.excluded_ingredients == ["香菜"]
    assert state.diners[0].allergies == ["花生"]  # allergy retained


def test_cancel_clears_pending_without_change():
    state = make_state(exclusions=["鸡蛋", "牛奶"])
    apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋"]), "取消鸡蛋")
    result = apply_revoke_exclusion(state, Intent(revoke_cancelled=True), "算了")
    assert result is None
    assert state.pending_revoke_exclusion is None
    assert state.meal_constraints.excluded_ingredients == ["鸡蛋", "牛奶"]  # unchanged


def test_partial_match_only_removes_existing():
    state = make_state(exclusions=["鸡蛋"])
    issue = apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋", "芒果"]), "取消鸡蛋和芒果")
    assert issue is not None
    assert state.pending_revoke_exclusion.targets == ["鸡蛋", "芒果"]
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert state.meal_constraints.excluded_ingredients == []  # 芒果 was never there


def test_nonexistent_target_changes_nothing():
    state = make_state(exclusions=["香菜"])
    issue = apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋"]), "取消鸡蛋忌口")
    assert issue is not None
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert state.meal_constraints.excluded_ingredients == ["香菜"]  # unchanged


def test_allergy_is_not_revocable_via_ordinary_path():
    state = make_state(exclusions=["鸡蛋"], allergies=["花生"])
    issue = apply_revoke_exclusion(state, Intent(revoke_exclusions=["花生"]), "取消花生限制")
    assert issue is not None
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert state.diners[0].allergies == ["花生"]  # allergy retained
    assert "花生" not in state.meal_constraints.excluded_ingredients
    assert state.meal_constraints.excluded_ingredients == ["鸡蛋"]  # ordinary exclusion intact


def test_health_goal_is_not_revocable():
    state = make_state(exclusions=["鸡蛋"])
    state.diners[0].health_goals = ["低盐"]
    apply_revoke_exclusion(state, Intent(revoke_exclusions=["低盐"]), "取消低盐要求")
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert state.diners[0].health_goals == ["低盐"]  # health constraint retained


def test_subject_isolation():
    diners = [
        owner_diner(allergies=()),
        Diner(diner_id="a", display_name="甲", excluded_ingredients=["鸡蛋"]),
        Diner(diner_id="b", display_name="乙", excluded_ingredients=["牛奶"]),
    ]
    state = make_state(exclusions=[], allergies=(), diners=diners)
    issue = apply_revoke_exclusion(
        state,
        Intent(diner_updates=[DinerUpdate(diner="乙", revoke_exclusions=["牛奶"])]),
        "取消乙的牛奶忌口",
    )
    assert issue is not None
    assert state.pending_revoke_exclusion.subject == "乙"
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    by_name = {d.display_name: d for d in state.diners}
    assert by_name["乙"].excluded_ingredients == []       # 乙's milk removed
    assert by_name["甲"].excluded_ingredients == ["鸡蛋"]  # 甲 unaffected


@pytest.mark.parametrize("message", [
    "如果以后可以吃鸡蛋，就取消鸡蛋忌口",
    "可以取消鸡蛋忌口吗？",
])
def test_conditional_or_question_is_not_a_revoke(message):
    state = make_state(exclusions=["鸡蛋"])
    result = apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋"]), message)
    assert result is None
    assert state.pending_revoke_exclusion is None
    assert state.meal_constraints.excluded_ingredients == ["鸡蛋"]  # unchanged


def test_modify_pending_targets_does_not_delete_old_target():
    state = make_state(exclusions=["鸡蛋", "牛奶"])
    apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋", "牛奶"]), "取消鸡蛋和牛奶")
    assert state.pending_revoke_exclusion.targets == ["鸡蛋", "牛奶"]
    issue = apply_revoke_exclusion(state, Intent(revoke_exclusions=["鸡蛋"]), "只取消鸡蛋")
    assert issue is not None
    assert state.pending_revoke_exclusion.targets == ["鸡蛋"]  # replaced
    apply_revoke_exclusion(state, Intent(revoke_confirmed=True), "确认")
    assert state.meal_constraints.excluded_ingredients == ["牛奶"]  # 牛奶 retained


def test_j19_full_flow_request_then_confirm(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([
        complete_intent(people=1, dish_count=3, soup_count=0,
                        excluded_ingredients=["鸡蛋"], restrictions_confirmed=True),
        Intent(revoke_exclusions=["鸡蛋"]),
        Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，不吃鸡蛋，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "取消刚才不吃鸡蛋这一条，我愿意吃鸡蛋了",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "确认",
        }).json()

    assert first["status"] == "ok"
    assert first["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]
    assert "花生" in first["conversation_state"]["constraints"]["allergies"]

    assert second["status"] == "clarification_required"
    assert second["conversation_state"]["pending_revoke_exclusion"]["targets"] == ["鸡蛋"]
    assert second["conversation_state"]["constraints"]["excluded_ingredients"] == ["鸡蛋"]

    assert third["status"] == "ok"
    assert third["conversation_state"]["constraints"]["excluded_ingredients"] == []
    assert "花生" in third["conversation_state"]["constraints"]["allergies"]
    assert third["conversation_state"]["pending_revoke_exclusion"] is None
