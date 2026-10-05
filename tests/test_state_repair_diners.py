"""R2: hand-authored synthetic intents; no claim about live model extraction."""

import json
import sqlite3

import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent import diners as participant_rules
from app.agent.clarification import recover_explicit_meal_context
from app.agent.diners import aggregate_constraints, apply_diner_updates, profile_diner
from app.domain.models import ChatResult, Constraints, DinerUpdate, Intent, SessionState
from app.infrastructure.sessions import SessionStore


def three_people():
    return [
        DinerUpdate(diner="第一位", attendance=True, allergies=["花生"]),
        DinerUpdate(diner="第二位", attendance=True, excluded_ingredients=["猪肉"]),
        DinerUpdate(diner="第三位", attendance=True, no_spicy=True),
    ]


def owner_of(state):
    return next(d for d in state["diners"] if d["profile_owner"])


@pytest.mark.parametrize("profile_allergies", [[], ["鸡蛋"]])
def test_three_enumerated_diners_do_not_acquire_a_fourth_person(
    tmp_path, catalog, profile_allergies
):
    catalog.profiles[3].allergies = profile_allergies
    llm = ScriptedLLM([complete_intent(
        people=3, dish_count=4, soup_count=1, diner_updates=three_people(),
    )])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/chat", json={
            "user_id": 3,
            "message": "3个人晚餐，共4道菜含1汤。第一位花生过敏，第二位不吃猪肉，"
                       "第三位不吃辣；整桌共享，没有其他忌口。",
        }).json()

    assert response["status"] == "ok", response["reason"]
    state = response["conversation_state"]
    assert state["constraints"]["people"] == 3
    assert len(state["diners"]) == 4  # The account record is retained, not guessed away.
    owner = owner_of(state)
    assert owner["participation_basis"] == "profile_unlinked"
    assert owner["attendance"] is True  # Unknown affiliation is not asserted absence.
    assert owner["allergies"] == profile_allergies
    named = {d["display_name"]: d for d in state["diners"] if not d["profile_owner"]}
    assert set(named) == {"第一位", "第二位", "第三位"}
    assert named["第一位"]["allergies"] == ["花生"]
    assert named["第二位"]["excluded_ingredients"] == ["猪肉"]
    assert named["第三位"]["no_spicy"] is True
    assert all("鸡蛋" not in d["allergies"] for d in named.values())
    assert set(state["constraints"]["allergies"]) == {*profile_allergies, "花生"}
    assert state["constraints"]["no_spicy"] is True
    for dish in response["menu"] + response["replacement_suggestions"]:
        assert not ({"花生", "猪肉", *profile_allergies} & set(dish["ingredients"]))
    owner_scope = next(x for x in response["diner_suitability"]
                       if x["diner_id"] == owner["diner_id"])
    assert "待关联" in owner_scope["scope_note"]
    assert "共享" in owner_scope["scope_note"]
    assert any("待关联" in item for item in response["constraints"])


def test_me_and_three_others_counts_four_then_explicit_exit_and_return(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    llm = ScriptedLLM([
        complete_intent(people=4, dish_count=4, soup_count=1, diner_updates=three_people()),
        Intent(diner_updates=[DinerUpdate(diner="本人", attendance=False)]),
        Intent(diner_updates=[DinerUpdate(diner="我", attendance=True)]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "我和另外三个人共4人晚餐，4菜1汤，没有其他忌口",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "我不参加，其他人照旧",
        }).json()
        third = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "我重新参加，其他照旧",
        }).json()
    for response, count in [(first, 4), (second, 3), (third, 4)]:
        assert response["status"] == "ok", response["reason"]
        assert response["conversation_state"]["constraints"]["people"] == count
        assert owner_of(response["conversation_state"])["participation_basis"] == "explicit"
    first_owner = owner_of(first["conversation_state"])
    assert owner_of(second["conversation_state"])["diner_id"] == first_owner["diner_id"]
    assert owner_of(third["conversation_state"])["diner_id"] == first_owner["diner_id"]
    assert "鸡蛋" not in second["conversation_state"]["constraints"]["allergies"]
    assert owner_of(second["conversation_state"])["allergies"] == ["鸡蛋"]
    assert "鸡蛋" in third["conversation_state"]["constraints"]["allergies"]


def test_explicit_owner_absence_keeps_profile_but_only_friends_contribute(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    llm = ScriptedLLM([complete_intent(people=3, diner_updates=three_people())])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/chat", json={
            "user_id": 3,
            "message": "我不参加，给三位朋友安排3人晚餐，没有其他忌口",
        }).json()
    assert response["status"] == "ok", response["reason"]
    state = response["conversation_state"]
    assert owner_of(state)["attendance"] is False
    assert owner_of(state)["allergies"] == ["鸡蛋"]
    assert state["constraints"]["allergies"] == ["花生"]
    assert state["constraints"]["people"] == 3


def test_confirmed_alias_preserves_identity_and_scoped_constraints(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(people=3, diner_updates=three_people()),
        Intent(diner_updates=[DinerUpdate(diner="小林", aliases=["第一位"], preferences=["软烂"])]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "3人晚餐，第一位花生过敏，没有其他忌口",
        }).json()
        second = client.post("/chat", json={
            "user_id": 3, "message": "第一位就是小林，喜欢软烂的菜",
            "session_id": first["conversation_state"]["session_id"],
        }).json()
    assert first["status"] == second["status"] == "ok"
    initial = first["conversation_state"]["diners"][1]
    updated = second["conversation_state"]["diners"][1]
    assert updated["diner_id"] == initial["diner_id"]
    assert {"小林", "第一位"}.issubset(updated["aliases"])
    assert updated["allergies"] == ["花生"]
    assert len(second["conversation_state"]["diners"]) == 4


def test_real_three_named_attendees_cannot_fit_a_two_person_meal(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(people=2, diner_updates=three_people())])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/chat", json={
            "user_id": 3,
            "message": "2人晚餐，第一第二第三位都参加，没有其他忌口",
        }).json()
    assert response["status"] == "clarification_required"
    assert "3 位参餐者" in response["reason"]
    assert response["menu"] == []
    assert response["conversation_state"]["constraints"]["people"] == 2


@pytest.mark.parametrize("message", [
    "给我安排3人晚餐", "如果我和另外三人参加呢？", "他说‘我和另外三人吃晚餐’",
    "我不确定是否参加", "我和爸爸都参加吗？",
])
def test_requester_reference_and_uncertain_attendance_do_not_guess_profile_identity(
    catalog, message
):
    diners = apply_diner_updates(
        [profile_diner(catalog.profiles[3])], three_people(), session_id="synthetic-r2",
        message=message,
    )
    assert diners[0].participation_basis == "profile_unlinked"
    assert participant_rules.effective_attendee_count(diners) == 3


def test_legacy_profile_and_explicit_people_load_and_replay_without_false_link(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    diners = apply_diner_updates(
        [profile_diner(catalog.profiles[3])], three_people(), session_id="legacy-r2",
    )
    state = SessionState(
        session_id="legacy-r2", user_id=3, revision=1, diners=diners,
        meal_constraints=Constraints(people=3), confirmed_fields=["people"],
    )
    result = ChatResult(status="clarification_required", reason="synthetic legacy", conversation_state=state)
    snapshot = state.model_dump()
    cached = result.model_dump()
    for payload in (snapshot, cached["conversation_state"]):
        for diner in payload["diners"]:
            diner.pop("participation_basis", None)
    db = tmp_path / "synthetic-legacy.sqlite3"
    store = SessionStore(db)
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                           (state.session_id, 3, 1, json.dumps(snapshot)))
        connection.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                           (state.session_id, "old", "synthetic-hash", 1, json.dumps(cached)))
    loaded = SessionStore(db).get(state.session_id, 3)
    replayed = store.replay(state.session_id, "old", "synthetic-hash", 1).conversation_state
    for restored in (loaded, replayed):
        assert restored.diners[0].participation_basis == "legacy"
        assert participant_rules.effective_attendee_count(restored.diners) == 3
        assert restored.diners[0].attendance is True
        aggregate = aggregate_constraints(restored.meal_constraints, restored.diners)
        assert set(aggregate.allergies) == {"鸡蛋", "花生"}
        assert aggregate.no_spicy is True
        assert restored.diners[1].allergies == ["花生"]


def test_conditional_owner_absence_does_not_drop_profile_allergy(tmp_path, catalog):
    """Review counterexample: '的话' does not confirm that the owner is absent."""
    catalog.profiles[3].allergies = ["花生"]
    with client_for(tmp_path, catalog, ScriptedLLM([Intent(action="clarify")])) as client:
        response = client.post("/chat", json={
            "user_id": 3,
            "message": "我不参加的话，就给三位朋友安排晚餐",
        }).json()
    assert response["status"] == "clarification_required"
    state = response["conversation_state"]
    assert state["constraints"]["allergies"] == ["花生"]
    assert owner_of(state)["attendance"] is True
    assert owner_of(state)["participation_basis"] == "profile_unlinked"


@pytest.mark.parametrize("message", [
    "我和另外三个人吃晚餐", "我和其他三个人吃晚餐", "我和另外3个人吃晚餐",
])
def test_relative_party_size_is_not_recovered_as_total_people(tmp_path, catalog, message):
    """The fallback may leave a total unknown; it must not call three others a total of three."""
    with client_for(tmp_path, catalog, ScriptedLLM([Intent(action="clarify")])) as client:
        response = client.post("/chat", json={"user_id": 3, "message": message}).json()
    assert response["status"] == "clarification_required"
    state = response["conversation_state"]
    assert "people" not in state["confirmed_fields"]
    assert "people" in state["pending_fields"]
    assert owner_of(state)["attendance"] is True
    assert owner_of(state)["participation_basis"] == "explicit"


@pytest.mark.parametrize("message", [
    "我不参加的话，就给三位朋友安排晚餐",
    "若我不参加，则给三位朋友安排晚餐",
])
def test_conditional_absence_in_parsed_intent_keeps_profile_allergy(tmp_path, catalog, message):
    """A conditional exit in the parsed intent must not confirm the owner absent."""
    catalog.profiles[3].allergies = ["花生"]
    llm = ScriptedLLM([Intent(
        people=3, diner_updates=[DinerUpdate(diner="我", attendance=False)],
    )])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/chat", json={"user_id": 3, "message": message}).json()
    assert response["status"] == "clarification_required"
    state = response["conversation_state"]
    assert state["constraints"]["allergies"] == ["花生"]
    assert owner_of(state)["attendance"] is True
    assert owner_of(state)["participation_basis"] == "profile_unlinked"


@pytest.mark.parametrize("message", [
    "若四人参加，则安排晚餐",
    "如果四人参加的话，就安排晚餐",
])
def test_conditional_party_size_is_not_recovered_as_a_fact(message):
    """A hypothetical party size is not a confirmed total."""
    assert recover_explicit_meal_context(Intent(), message).people is None
