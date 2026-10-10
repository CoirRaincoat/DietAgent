"""Synthetic counterexamples for repeated reject/restore operations."""

from uuid import uuid4

import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.menu_restore import (
    apply_menu_restore,
    record_menu_revision,
    record_rejection_action,
)
from app.domain.models import Constraints, Intent, RestoreMenuIntent, SessionState
from app.rules.engine import RuleEngine


def restore_original():
    return Intent(restore_menu=RestoreMenuIntent(reference="original"))


def send(client, message, session_id=None):
    payload = {"user_id": 3, "message": message, "request_id": uuid4().hex}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def menu_ids(body):
    return [item["recipe_id"] for item in body["menu"]]


@pytest.mark.parametrize("cycles", [2, 3])
def test_original_menu_can_be_rejected_and_restored_repeatedly(tmp_path, catalog, cycles):
    intents = [complete_intent(dish_count=3)]
    for _ in range(cycles):
        intents.extend([Intent(action="reject"), restore_original()])
    with client_for(tmp_path, catalog, ScriptedLLM(intents)) as client:
        first = send(client, "1人晚餐，3道菜，没有其他忌口")
        assert first["status"] == "ok"
        original = menu_ids(first)
        session_id = first["conversation_state"]["session_id"]
        for _ in range(cycles):
            replacement = send(client, "整桌都不要，全部换掉", session_id)
            assert replacement["status"] == "ok"
            assert set(original).isdisjoint(menu_ids(replacement))
            restored = send(client, "撤销刚才整桌拒绝，恢复第一轮的原菜", session_id)
            assert restored["status"] == "ok"
            assert menu_ids(restored) == original
            state = restored["conversation_state"]
            assert not set(original).intersection(state["rejected_recipe_ids"])
            assert all(not action["active"] for action in state["rejection_actions"])


def test_repeated_restore_keeps_a_different_menu_rejected(tmp_path, catalog):
    intents = [complete_intent(dish_count=3), Intent(action="reject"), restore_original(),
               Intent(action="reject"), Intent(action="reject"), restore_original()]
    with client_for(tmp_path, catalog, ScriptedLLM(intents)) as client:
        first = send(client, "1人晚餐，3道菜，没有其他忌口")
        original = menu_ids(first)
        session_id = first["conversation_state"]["session_id"]
        send(client, "整桌换掉", session_id)
        restored_once = send(client, "恢复第一轮原菜", session_id)
        assert restored_once["status"] == "ok"
        other_menu = send(client, "整桌再次换掉", session_id)
        assert other_menu["status"] == "ok"
        other_ids = menu_ids(other_menu)
        assert set(original).isdisjoint(other_ids)
        newer_menu = send(client, "刚换出的这一桌也都不要", session_id)
        assert newer_menu["status"] == "ok"
        restored = send(client, "恢复第一轮原菜，保留另一桌的拒绝", session_id)
        assert restored["status"] == "ok"
        assert menu_ids(restored) == original
        assert set(restored["conversation_state"]["rejected_recipe_ids"]) == set(other_ids)
        active = [action for action in restored["conversation_state"]["rejection_actions"]
                  if action["active"]]
        assert len(active) == 1
        assert set(active[0]["rejected_recipe_ids"]) == set(other_ids)


def test_repeated_restore_still_blocks_a_new_allergy_without_mutation(catalog):
    original = ["test_8", "test_0", "test_1"]  # Synthetic peanut porridge.
    other = ["test_3", "test_4", "test_5"]
    state = SessionState(session_id="synthetic-cycle", user_id=3, constraints=Constraints())
    rules = RuleEngine()
    state.revision = 1
    record_menu_revision(state, original)
    record_rejection_action(state, original)
    state.rejected_recipe_ids = list(original)
    state.revision = 2
    record_menu_revision(state, other)
    state.menu_ids = list(other)
    assert apply_menu_restore(state, restore_original(), rules, catalog.recipes) is None
    state.revision = 3
    record_menu_revision(state, original, source="restored_menu")
    record_rejection_action(state, original)
    state.rejected_recipe_ids = list(original)
    state.revision = 4
    record_menu_revision(state, other)
    state.menu_ids = list(other)
    state.constraints.allergies = ["花生"]
    before = state.model_dump()
    assert apply_menu_restore(state, restore_original(), rules, catalog.recipes) is not None
    assert state.model_dump() == before


def test_restoring_original_cannot_undo_an_overlapping_different_menu(catalog):
    original = ["test_0", "test_1", "test_2"]
    other = ["test_3", "test_4", "test_5"]
    overlap = ["test_0", "test_3", "test_4"]
    state = SessionState(session_id="synthetic-overlap", user_id=3, constraints=Constraints())
    rules = RuleEngine()
    state.revision = 1
    record_menu_revision(state, original)
    record_rejection_action(state, original)
    state.rejected_recipe_ids = list(original)
    state.revision = 2
    record_menu_revision(state, other)
    state.menu_ids = list(other)
    assert apply_menu_restore(state, restore_original(), rules, catalog.recipes) is None
    state.revision = 3
    record_menu_revision(state, original, source="restored_menu")
    state.revision = 4
    record_menu_revision(state, overlap)
    record_rejection_action(state, overlap)
    state.rejected_recipe_ids = list(overlap)
    state.revision = 5
    state.menu_ids = ["test_6", "test_7", "test_10"]
    record_menu_revision(state, state.menu_ids)
    before = state.model_dump()
    assert apply_menu_restore(state, restore_original(), rules, catalog.recipes) is not None
    assert state.model_dump() == before
