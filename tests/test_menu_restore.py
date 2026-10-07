"""Scoped menu restore / rejection undo (J18) tests.

Pins the menu-snapshot record rule, rejection-delta record rule, exact-restore
semantics, precise rejection undo, current-constraint revalidation and the
original-J18 three-turn flow using only fake providers — never a real model.
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.history_restore import record_constraint_revision
from app.agent.menu_restore import (
    apply_menu_restore,
    record_menu_revision,
    record_rejection_action,
)
from app.domain.models import (
    Constraints,
    Ingredient,
    Intent,
    Recipe,
    RestoreMenuIntent,
    ScopedMethod,
    SessionState,
    UserProfile,
)
from app.infrastructure.llm.base import LLMOutputError
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore
from app.rules.engine import RuleEngine

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "configs" / "intent_prompt.txt"


def _state(**kwargs):
    state = SessionState(
        session_id="s", user_id=1,
        constraints=Constraints(),
        meal_constraints=Constraints(dish_count=3),
    )
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def _restore_original():
    return Intent(restore_menu=RestoreMenuIntent(reference="original"))


@pytest.mark.parametrize("requirement", ["composition", "method"])
def test_restore_preserves_rejections_when_newer_menu_requirements_block_it(catalog, requirement):
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_0", "test_1", "test_2"]
    state.menu_ids = ["test_3", "test_4", "test_5"]
    if requirement == "composition":
        state.constraints.vegetarian_dish_count = 3
    else:
        state.constraints.scoped_methods = [ScopedMethod(food="鸡肉", method="炒", slot=2)]
    before = state.model_dump()
    issue = apply_menu_restore(state, _restore_original(), RuleEngine(), catalog.recipes)
    assert issue and "无法直接恢复" in issue
    assert state.model_dump() == before


# --- M1 menu snapshot ---

def test_m1_menu_revision_appends_only_on_change():
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["a", "b", "c"])
    state.revision = 2
    record_menu_revision(state, ["d", "e", "f"])
    state.revision = 3
    record_menu_revision(state, ["d", "e", "f"])  # consecutive duplicate ignored
    assert [r.recipe_ids for r in state.menu_history] == [["a", "b", "c"], ["d", "e", "f"]]
    assert state.menu_history[0].revision_id == "menu-1"


# --- M2 whole-menu rejection delta ---

def test_m2_rejection_delta_is_newly_rejected_only():
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["a", "b", "c"])
    state.rejected_recipe_ids = ["x"]  # unrelated earlier rejection
    state.revision = 2
    record_rejection_action(state, ["a", "b", "c"])
    assert len(state.rejection_actions) == 1
    action = state.rejection_actions[0]
    assert action.rejected_recipe_ids == ["a", "b", "c"]
    assert action.source_menu_revision_id == state.menu_history[0].revision_id
    # Rejecting the same menu again adds no new delta (already rejected).
    state.rejected_recipe_ids = ["x", "a", "b", "c"]
    record_rejection_action(state, ["a", "b", "c"])
    assert len(state.rejection_actions) == 1


# --- M3 exact restore ---

def test_m3_exact_restore_undoes_target_delta(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_0", "test_1", "test_2"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is None
    assert state.menu_ids == ["test_0", "test_1", "test_2"]
    assert state.rejected_recipe_ids == []
    assert state.rejection_actions[0].active is False


# --- M4 unrelated rejection retained ---

def test_m4_unrelated_rejection_is_retained(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_9"]  # unrelated, must survive restore
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_9", "test_0", "test_1", "test_2"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is None
    assert state.menu_ids == ["test_0", "test_1", "test_2"]
    assert state.rejected_recipe_ids == ["test_9"]


# --- M5 later rejection retained ---

def test_m5_later_rejection_is_retained(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])  # rev1
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])  # reject1 -> rev1
    state.rejected_recipe_ids = ["test_0", "test_1", "test_2"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])  # rev2
    state.revision = 4
    record_rejection_action(state, ["test_3", "test_4", "test_5"])  # reject2 -> rev2
    state.rejected_recipe_ids = [
        "test_0", "test_1", "test_2", "test_3", "test_4", "test_5",
    ]
    state.revision = 5
    record_menu_revision(state, ["test_6", "test_7", "test_10"])  # rev3
    state.menu_ids = ["test_6", "test_7", "test_10"]

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is None
    assert state.menu_ids == ["test_0", "test_1", "test_2"]
    # reject2 delta retained, reject1 delta undone
    assert state.rejected_recipe_ids == ["test_3", "test_4", "test_5"]
    assert state.rejection_actions[0].active is False
    assert state.rejection_actions[1].active is True


# --- M6 later ordinary exclusion retained ---

def test_m6_later_exclusion_is_retained(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_0", "test_1", "test_2"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]
    state.constraints.excluded_ingredients = ["牛肉"]  # later ordinary exclusion

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is None
    assert state.menu_ids == ["test_0", "test_1", "test_2"]
    assert state.constraints.excluded_ingredients == ["牛肉"]


# --- M7 allergy conflict blocks restore ---

def test_m7_allergy_conflict_blocks_restore(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_8", "test_0", "test_1"])  # 花生粥 among originals
    state.revision = 2
    record_rejection_action(state, ["test_8", "test_0", "test_1"])
    state.rejected_recipe_ids = ["test_8", "test_0", "test_1"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]
    state.constraints.allergies = ["花生"]  # later allergy conflicts with 花生粥

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is not None
    assert state.menu_ids == ["test_3", "test_4", "test_5"]  # unchanged
    assert state.rejected_recipe_ids == ["test_8", "test_0", "test_1"]  # unchanged
    assert state.rejection_actions[0].active is True  # unchanged


# --- M8 no_spicy conflict blocks restore ---

def test_m8_no_spicy_conflict_blocks_restore(catalog):
    spicy = Recipe(
        recipe_id="spicy", name="辣子鸡", raw_ingredients="辣椒；鸡肉",
        ingredients=[Ingredient(raw="辣椒", name="辣椒"), Ingredient(raw="鸡肉", name="鸡肉")],
        steps="放入辣椒炒熟。", categories=["protein"], methods=["炒"],
        meal_types=["晚餐"], source_row=2, fingerprint="spicy",
    )
    recipes = dict(catalog.recipes)
    recipes["spicy"] = spicy
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["spicy", "test_0", "test_1"])
    state.revision = 2
    record_rejection_action(state, ["spicy", "test_0", "test_1"])
    state.rejected_recipe_ids = ["spicy", "test_0", "test_1"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]
    state.constraints.no_spicy = True

    issue = apply_menu_restore(state, _restore_original(), rules, recipes)

    assert issue is not None
    assert state.menu_ids == ["test_3", "test_4", "test_5"]
    assert state.rejected_recipe_ids == ["spicy", "test_0", "test_1"]


# --- M9 no history ---

def test_m9_no_history_clarifies_without_mutation(catalog):
    rules = RuleEngine()
    state = _state()
    state.menu_ids = ["test_3", "test_4", "test_5"]
    state.rejected_recipe_ids = ["test_9"]

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is not None
    assert state.menu_ids == ["test_3", "test_4", "test_5"]
    assert state.rejected_recipe_ids == ["test_9"]


# --- M12 J17 coexistence ---

def test_m12_j17_constraint_history_coexists(catalog):
    rules = RuleEngine()
    state = _state()
    state.revision = 1
    record_menu_revision(state, ["test_0", "test_1", "test_2"])
    record_constraint_revision(state, "dish_count", 3)  # J17 data, must survive
    state.revision = 2
    record_rejection_action(state, ["test_0", "test_1", "test_2"])
    state.rejected_recipe_ids = ["test_0", "test_1", "test_2"]
    state.revision = 3
    record_menu_revision(state, ["test_3", "test_4", "test_5"])
    state.menu_ids = ["test_3", "test_4", "test_5"]

    issue = apply_menu_restore(state, _restore_original(), rules, catalog.recipes)

    assert issue is None
    assert state.menu_ids == ["test_0", "test_1", "test_2"]
    assert [r.value for r in state.constraint_history] == [3]


# --- Synthetic J18 full-flow ---

def test_synthetic_j18_full_flow(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(dish_count=3, soup_count=0),
        Intent(action="reject"),
        Intent(restore_menu=RestoreMenuIntent(reference="original")),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        t1 = client.post("/chat", json={
            "user_id": 3, "message": "1个人吃晚餐，总共3道菜，不要汤，没有过敏或其他忌口。",
        }).json()
        sid = t1["conversation_state"]["session_id"]
        t2 = client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "这三道都不要了，这顿饭不要再推荐它们，整套换掉，其他要求不变。",
        }).json()
        t3 = client.post("/chat", json={
            "user_id": 3, "session_id": sid,
            "message": "我改变主意了，撤销刚才整套换菜和拒绝记录，恢复第一轮那三道原菜，其他要求不变。",
        }).json()

    assert t1["status"] == "ok"
    t1_ids = [item["recipe_id"] for item in t1["menu"]]
    assert len(t1_ids) == 3

    assert t2["status"] == "ok"
    t2_ids = [item["recipe_id"] for item in t2["menu"]]
    assert len(t2_ids) == 3
    assert set(t2_ids).isdisjoint(t1_ids)
    assert set(t1_ids) <= set(t2["conversation_state"]["rejected_recipe_ids"])

    assert t3["status"] == "ok"
    assert [item["recipe_id"] for item in t3["menu"]] == t1_ids  # exact (slot, id)
    assert not (set(t1_ids) & set(t3["conversation_state"]["rejected_recipe_ids"]))
    c3 = t3["conversation_state"]["constraints"]
    assert c3["people"] == 1 and c3["meal_type"] == "晚餐"
    assert c3["dish_count"] == 3 and c3["soup_count"] == 0


# --- Later ordinary exclusion retained through the full pipeline ---

def test_restore_retains_later_exclusion_full_flow(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(dish_count=3, soup_count=0),
        Intent(action="reject"),
        complete_intent(excluded_ingredients=["牛肉"]),
        Intent(restore_menu=RestoreMenuIntent(reference="original")),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        t1 = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，没有其他忌口",
        }).json()
        sid = t1["conversation_state"]["session_id"]
        t2 = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "这三道都不要了，整套换掉",
        }).json()
        t3 = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "再加个不吃牛肉",
        }).json()
        t4 = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "恢复第一轮那三道原菜",
        }).json()

    assert t2["status"] == "ok"
    assert t3["status"] == "ok"
    assert t4["status"] == "ok"
    assert [item["recipe_id"] for item in t4["menu"]] == [item["recipe_id"] for item in t1["menu"]]
    assert "牛肉" in t4["conversation_state"]["constraints"]["excluded_ingredients"]


# --- No-history negative through the full pipeline ---

def test_restore_without_history_clarifies_full_flow(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(inventory=[]),
        Intent(restore_menu=RestoreMenuIntent(reference="original")),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，没有其他忌口，没有食材",
        }).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "恢复上一桌",
        }).json()

    assert first["status"] == "no_feasible_menu"
    assert second["status"] == "clarification_required"
    assert second["menu"] == []
    assert second["conversation_state"]["menu_ids"] == []


# --- M10 idempotent replay ---

def test_m10_restore_request_replay_is_idempotent(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(dish_count=3, soup_count=0),
        Intent(action="reject"),
        Intent(restore_menu=RestoreMenuIntent(reference="original")),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        t1 = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，没有其他忌口",
        }).json()
        sid = t1["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "这三道都不要了，整套换掉",
        }).json()
        body = {"user_id": 3, "session_id": sid, "request_id": "req-restore-menu-1",
                "message": "恢复第一轮那三道原菜"}
        once = client.post("/chat", json=body).json()
        twice = client.post("/chat", json=body).json()

    assert once["conversation_state"]["menu_ids"] == twice["conversation_state"]["menu_ids"]
    assert once["conversation_state"]["menu_history"] == twice["conversation_state"]["menu_history"]
    assert once["conversation_state"]["rejection_actions"] == twice["conversation_state"]["rejection_actions"]
    assert once["conversation_state"]["rejected_recipe_ids"] == twice["conversation_state"]["rejected_recipe_ids"]


# --- Persistence across restart ---

def test_menu_restore_survives_restart(tmp_path, catalog):
    first_llm = ScriptedLLM([
        complete_intent(dish_count=3, soup_count=0),
        Intent(action="reject"),
    ])
    with client_for(tmp_path, catalog, first_llm) as client:
        t1 = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，3道菜，没有其他忌口",
        }).json()
        sid = t1["conversation_state"]["session_id"]
        client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "这三道都不要了，整套换掉",
        }).json()

    second_llm = ScriptedLLM([Intent(restore_menu=RestoreMenuIntent(reference="original"))])
    with client_for(tmp_path, catalog, second_llm) as client:
        restored = client.post("/chat", json={
            "user_id": 3, "session_id": sid, "message": "恢复第一轮那三道原菜",
        }).json()

    assert restored["status"] == "ok"
    assert [item["recipe_id"] for item in restored["menu"]] == [item["recipe_id"] for item in t1["menu"]]


# --- M11 old session compatibility ---

def test_m11_legacy_snapshot_defaults_new_fields_empty(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    state = SessionState(session_id=uuid4().hex, user_id=3, revision=1)
    snapshot = state.model_dump(exclude={"menu_history", "rejection_actions"})
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (state.session_id, 3, 1, json.dumps(snapshot)),
        )
    loaded = store.get(state.session_id, 3)
    assert loaded.menu_history == []
    assert loaded.rejection_actions == []


# --- Intent contract: prompt and adapter ---

def completion(content):
    return {"choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
    }]}


def adapter_for(body):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    return DeepSeekLLM(api_key="fake-test-secret", client=client), client


@pytest.fixture
def profile():
    return UserProfile(
        data_scope="synthetic", user_id=1, age=30, sex="女", height_cm=160,
        weight_kg=55, bmi=21.5, allergies=["花生"],
    )


@pytest.fixture
def state():
    return SessionState(
        session_id="test-session", user_id=1,
        constraints=Constraints(), meal_constraints=Constraints(dish_count=5),
    )


def test_intent_prompt_documents_menu_restore_contract():
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "restore_menu" in text
    assert "original" in text
    assert "恢复第一轮那三道原菜" in text
    assert "恢复之前的菜单" in text


async def test_contract_accepts_restore_menu_json(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan",
        "restore_menu": {"reference": "original"},
    }))
    async with client:
        intent = await adapter.parse("恢复第一轮那三道原菜", state, profile)
    assert intent.restore_menu == RestoreMenuIntent(reference="original")


async def test_contract_rejects_restore_menu_with_dish_count(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan", "dish_count": 4,
        "restore_menu": {"reference": "original"},
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("恢复第一轮，同时改成4道", state, profile)


async def test_contract_rejects_restore_menu_with_restore_constraints(profile, state):
    adapter, client = adapter_for(completion({
        "action": "plan",
        "restore_constraints": [{"field": "dish_count", "reference": "original"}],
        "restore_menu": {"reference": "original"},
    }))
    async with client:
        with pytest.raises(LLMOutputError):
            await adapter.parse("恢复最初菜数和菜单", state, profile)
