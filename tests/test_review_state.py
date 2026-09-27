"""PR review regressions: uncertainty survives turns and legacy structure is preserved."""

import json
import sqlite3

import pytest
from test_agent_api import ScriptedLLM, complete_intent
from test_agent_api import catalog as catalog

from app.agent.service import MealAgent
from app.domain.models import Constraints, DinerUpdate, Intent, SessionState
from app.infrastructure.sessions import SessionStore


def dad(state):
    return next(diner for diner in state.diners if diner.display_name == "爸爸")


def pending_intent(**changes):
    return complete_intent(
        people=2,
        diner_updates=[DinerUpdate(
            diner="爸爸", attendance=True, allergies=["鸡蛋", "神秘酱料"],
        )],
        **changes,
    )


async def test_known_and_pending_diner_allergies_survive_restart_and_explicit_resolution(
    tmp_path, catalog
):
    path = tmp_path / "state.db"
    first_agent = MealAgent(catalog, SessionStore(path), ScriptedLLM([pending_intent()]))
    first = await first_agent.chat(3, "我和爸爸两人晚餐，爸爸对鸡蛋和神秘酱料过敏")
    assert first.status == "clarification_required"
    assert first.menu == [] and first.tool_calls == []
    assert dad(first.conversation_state).allergies == ["鸡蛋"]
    assert dad(first.conversation_state).pending_allergy_terms == ["神秘酱料"]
    assert first.conversation_state.constraints.allergies == ["鸡蛋"]
    assert first.conversation_state.pending_fields == ["allergy"]
    sid = first.conversation_state.session_id
    restarted = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        Intent(),
        Intent(diner_updates=[DinerUpdate(diner="我爸", allergies=["花生"])]),
        Intent(diner_updates=[DinerUpdate(
            diner="我爸", allergy_clarifications={"神秘酱料": ["芝麻"]},
        )]),
    ]))
    unrelated = await restarted.chat(3, "先推荐一份吧", sid)
    additional = await restarted.chat(3, "爸爸另外还对花生过敏", sid)
    for result in [unrelated, additional]:
        assert result.status == "clarification_required"
        assert result.menu == [] and result.tool_calls == []
        assert dad(result.conversation_state).pending_allergy_terms == ["神秘酱料"]
    resolved = await restarted.chat(3, "爸爸的神秘酱料具体指的是芝麻", sid)
    assert resolved.status == "ok"
    assert dad(resolved.conversation_state).pending_allergy_terms == []
    assert set(dad(resolved.conversation_state).allergies) == {"鸡蛋", "花生", "芝麻"}
    assert set(resolved.conversation_state.constraints.allergies) == {"鸡蛋", "花生", "芝麻"}
    for item in resolved.menu + resolved.replacement_suggestions:
        assert not {"鸡蛋", "花生", "芝麻"}.intersection(item.ingredients)
    assert SessionStore(path).get(sid, 3).model_dump() == resolved.conversation_state.model_dump()


@pytest.mark.parametrize(("message", "mapping"), [
    ("先推荐一份吧", {"神秘酱料": ["芝麻"]}),
    ("爸爸的神秘酱料是芝麻吗？", {"神秘酱料": ["芝麻"]}),
    ("爸爸另外也对芝麻过敏", {"神秘酱料": ["芝麻"]}),
    ("爸爸的神秘酱料具体是不明粉末", {"神秘酱料": ["不明粉末"]}),
    ("爸爸的神秘酱料具体是芝麻", {"神秘酱料": []}),
    ("爸爸的神秘酱料具体是芝麻", {"另一个未决词": ["芝麻"]}),
])
async def test_diner_pending_allergy_cannot_be_cleared_without_explicit_valid_mapping(
    tmp_path, catalog, message, mapping
):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        pending_intent(),
        Intent(diner_updates=[DinerUpdate(diner="爸爸", allergy_clarifications=mapping)]),
    ]))
    first = await agent.chat(3, "我和爸爸两人晚餐，爸爸对鸡蛋和神秘酱料过敏")
    second = await agent.chat(3, message, first.conversation_state.session_id)
    assert second.status == "clarification_required"
    assert second.menu == [] and second.tool_calls == []
    assert dad(second.conversation_state).allergies == ["鸡蛋"]
    assert dad(second.conversation_state).pending_allergy_terms == ["神秘酱料"]


async def test_same_pending_term_for_two_diners_is_resolved_only_for_named_owner(tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        complete_intent(people=3, diner_updates=[
            DinerUpdate(diner="爸爸", allergies=["鸡蛋", "神秘酱料"]),
            DinerUpdate(diner="妈妈", allergies=["花生", "神秘酱料"]),
        ]),
        Intent(diner_updates=[DinerUpdate(
            diner="爸爸", allergy_clarifications={"神秘酱料": ["芝麻"]},
        )]),
        Intent(allergy_clarifications={"神秘酱料": ["芝麻"]}),
    ]))
    first = await agent.chat(3, "我们三人晚餐，爸爸鸡蛋过敏，妈妈花生过敏，两人还对神秘酱料过敏")
    sid = first.conversation_state.session_id
    second = await agent.chat(3, "爸爸的神秘酱料具体是芝麻", sid)
    assert second.status == "clarification_required"
    assert dad(second.conversation_state).pending_allergy_terms == []
    mom = next(d for d in second.conversation_state.diners if d.display_name == "妈妈")
    assert mom.pending_allergy_terms == ["神秘酱料"]
    assert mom.allergies == ["花生"]
    unscoped = await agent.chat(3, "神秘酱料具体是芝麻", sid)
    assert unscoped.status == "clarification_required"
    assert unscoped.menu == [] and unscoped.tool_calls == []


async def test_personal_resolution_does_not_clear_global_uncertainty(tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        pending_intent(allergies=["神秘酱料"]),
        Intent(diner_updates=[DinerUpdate(
            diner="爸爸", allergy_clarifications={"神秘酱料": ["芝麻"]},
        )]),
    ]))
    first = await agent.chat(3, "两人晚餐，整桌神秘酱料过敏，爸爸还对鸡蛋和神秘酱料过敏")
    second = await agent.chat(3, "爸爸的神秘酱料具体是芝麻", first.conversation_state.session_id)
    assert second.status == "clarification_required"
    assert second.menu == [] and second.tool_calls == []
    assert dad(second.conversation_state).pending_allergy_terms == []
    assert second.conversation_state.pending_allergy_terms == ["神秘酱料"]
    assert second.conversation_state.pending_allergy is True


async def test_absence_retains_pending_terms_and_rejoining_blocks_planning(tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        pending_intent(),
        Intent(diner_updates=[DinerUpdate(diner="爸爸", attendance=False)]),
        Intent(diner_updates=[DinerUpdate(diner="我爸", attendance=True)]),
    ]))
    first = await agent.chat(3, "两人晚餐，爸爸对鸡蛋和神秘酱料过敏")
    sid = first.conversation_state.session_id
    absent = await agent.chat(3, "爸爸今晚不参加", sid)
    assert absent.status == "ok"
    assert dad(absent.conversation_state).pending_allergy_terms == ["神秘酱料"]
    assert dad(absent.conversation_state).allergies == ["鸡蛋"]
    returned = await agent.chat(3, "爸爸又参加了", sid)
    assert returned.status == "clarification_required"
    assert returned.menu == [] and returned.tool_calls == []
    assert returned.conversation_state.constraints.allergies == ["鸡蛋"]


@pytest.mark.parametrize("known_allergies", [[], ["鸡蛋"]])
async def test_preserve_pending_diner_reference_never_creates_global_pending(
    tmp_path, catalog, known_allergies
):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        complete_intent(people=2, diner_updates=[DinerUpdate(
            diner="爸爸", allergies=[*known_allergies, "神秘酱料"],
        )]),
        Intent(),
        Intent(),
        Intent(diner_updates=[DinerUpdate(
            diner="爸爸", allergy_clarifications={"神秘酱料": ["芝麻"]},
        )]),
    ]))
    first = await agent.chat(3, "两人晚餐，爸爸对神秘酱料过敏")
    sid = first.conversation_state.session_id
    for text in ["爸爸的过敏限制照旧", "过敏限制照旧，先推荐"]:
        preserved = await agent.chat(3, text, sid)
        assert preserved.status == "clarification_required"
        assert preserved.conversation_state.pending_allergy is False
        assert dad(preserved.conversation_state).pending_allergy_terms == ["神秘酱料"]
    final = await agent.chat(3, "爸爸的神秘酱料具体是芝麻", sid)
    assert final.status == "ok"
    assert final.conversation_state.pending_allergy is False
    assert set(dad(final.conversation_state).allergies) == {*known_allergies, "芝麻"}


@pytest.mark.parametrize("assertion", ["爸爸过敏", "爸爸有食物过敏", "爸爸对某种食材过敏"])
async def test_named_unspecified_allergy_requires_answer_for_same_diner(tmp_path, catalog, assertion):
    path = tmp_path / "state.db"
    agent = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        complete_intent(people=3, diner_updates=[
            DinerUpdate(diner="爸爸"), DinerUpdate(diner="妈妈"),
        ]),
        Intent(diner_updates=[DinerUpdate(diner="妈妈", allergies=["花生"])]),
        Intent(),
        Intent(diner_updates=[DinerUpdate(diner="爸爸", allergies=["鸡蛋"])]),
    ]))
    first = await agent.chat(3, "我们三人晚餐，" + assertion)
    sid = first.conversation_state.session_id
    assert first.status == "clarification_required"
    assert dad(first.conversation_state).pending_allergy is True
    assert first.conversation_state.pending_allergy is False
    assert dad(SessionStore(path).get(sid, 3)).pending_allergy is True
    other_person = await agent.chat(3, "妈妈具体是花生过敏", sid)
    assert other_person.status == "clarification_required"
    assert dad(other_person.conversation_state).pending_allergy is True
    preserve = await agent.chat(3, "爸爸过敏限制照旧", sid)
    assert preserve.status == "clarification_required"
    assert preserve.conversation_state.pending_allergy is False
    final = await agent.chat(3, "爸爸具体是鸡蛋过敏", sid)
    assert final.status == "ok"
    assert dad(final.conversation_state).pending_allergy is False
    assert dad(final.conversation_state).allergies == ["鸡蛋"]
    assert set(final.conversation_state.constraints.allergies) == {"鸡蛋", "花生"}


async def test_unattributed_unspecified_allergy_cannot_be_resolved_by_someone_else(tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        complete_intent(people=2, diner_updates=[DinerUpdate(diner="爸爸")]),
        Intent(diner_updates=[DinerUpdate(diner="爸爸", allergies=["鸡蛋"])]),
    ]))
    first = await agent.chat(3, "两人晚餐，有个人过敏")
    assert first.conversation_state.pending_allergy is True
    second = await agent.chat(3, "爸爸具体是鸡蛋过敏", first.conversation_state.session_id)
    assert second.status == "clarification_required"
    assert second.conversation_state.pending_allergy is True
    assert dad(second.conversation_state).allergies == ["鸡蛋"]


async def test_unnamed_answer_can_transition_through_unknown_term_before_resolution(tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        complete_intent(people=2, diner_updates=[DinerUpdate(diner="爸爸")]),
        Intent(diner_updates=[DinerUpdate(diner="爸爸", allergies=["神秘酱料"])]),
        Intent(diner_updates=[DinerUpdate(
            diner="爸爸", allergy_clarifications={"神秘酱料": ["芝麻"]},
        )]),
    ]))
    first = await agent.chat(3, "两人晚餐，爸爸过敏")
    sid = first.conversation_state.session_id
    second = await agent.chat(3, "爸爸具体是神秘酱料过敏", sid)
    assert second.status == "clarification_required"
    assert second.menu == [] and second.tool_calls == []
    assert dad(second.conversation_state).pending_allergy is False
    assert dad(second.conversation_state).pending_allergy_terms == ["神秘酱料"]
    final = await agent.chat(3, "爸爸的神秘酱料具体指的是芝麻", sid)
    assert final.status == "ok"
    assert dad(final.conversation_state).pending_allergy is False
    assert dad(final.conversation_state).pending_allergy_terms == []
    assert dad(final.conversation_state).allergies == ["芝麻"]


@pytest.mark.parametrize("intent", [
    Intent(people=3),
    Intent(diner_updates=[DinerUpdate(diner="爸爸", attendance=True)]),
])
async def test_legacy_stored_menu_counts_survive_people_or_attendance_updates(
    tmp_path, catalog, intent
):
    path = tmp_path / "state.db"
    store = SessionStore(path)
    legacy = {
        "session_id": "legacy-party", "user_id": 3, "revision": 4,
        "constraints": Constraints(people=2, dish_count=5, soup_count=1).model_dump(),
        "confirmed_fields": ["people", "meal_type", "restrictions"],
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            ("legacy-party", 3, 4, json.dumps(legacy)),
        )
    assert store.get("legacy-party", 3).menu_structure_explicit is True
    agent = MealAgent(catalog, store, ScriptedLLM([intent]))
    result = await agent.chat(3, "人数变了，其他照旧", "legacy-party")
    assert result.status == "ok"
    assert result.conversation_state.constraints.dish_count == 5
    assert result.conversation_state.constraints.soup_count == 1
    assert len(result.menu) == 5
    assert store.get("legacy-party", 3).menu_structure_explicit is True


def test_current_explicit_false_structure_marker_is_not_migrated(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    state = SessionState(
        session_id="current", user_id=3, revision=1, menu_structure_explicit=False,
        constraints=Constraints(dish_count=3, soup_count=0),
    )
    store.save(state, None)
    assert store.get("current", 3).menu_structure_explicit is False
