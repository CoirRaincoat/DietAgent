"""R1 handwritten synthetic intents; never recorded vendor output or live HTTP."""

import json

import httpx
import pytest
from test_agent_api import ScriptedLLM, complete_intent
from test_agent_api import catalog as catalog
from test_llm import completion

from app.agent.clarification import confirm_from_intent, explicit_allergy_resolution
from app.agent.service import MealAgent
from app.domain.allergy_mentions import (
    repair_proven_reference_pending,
    requires_allergy_clarification,
)
from app.domain.models import Constraints, Intent, SessionState
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore


@pytest.mark.parametrize("text", [
    "没有过敏或其他忌口", "没有过敏，也没有其他忌口", "没有过敏和其他忌口",
    "无过敏及其他忌口", "目前没有过敏或其他忌口",
])
def test_compound_no_restrictions_are_confirmed_without_erasing_profile(text):
    state = SessionState(session_id="mock", user_id=3, constraints=Constraints(allergies=["花生"]))
    confirm_from_intent(state, Intent(restrictions_confirmed=True), text)
    assert "restrictions" in state.confirmed_fields
    assert state.constraints.allergies == ["花生"]


@pytest.mark.parametrize("text", [
    "没有过敏或其他忌口？", "如果没有过敏或其他忌口，就推荐",
    "他说没有过敏或其他忌口", "我不确定没有过敏或其他忌口",
    "请解释‘没有过敏或其他忌口’", '“没有过敏，也没有其他忌口”是什么意思',
    "如果身体允许，没有其他忌口", "她说，没有其他忌口",
])
def test_quotes_conditions_uncertainty_are_not_confirmations(text):
    state = SessionState(session_id="mock", user_id=3)
    confirm_from_intent(state, Intent(restrictions_confirmed=True), text)
    assert "restrictions" not in state.confirmed_fields


@pytest.mark.parametrize("message, action", [
    ("过敏限制照旧", "plan"),
    ("解释一下之前的过敏检查", "explain"),
    ("先不要换菜，只解释这三道为什么适合我的偏好，以及你怎样检查海鲜和花生过敏。", "explain"),
    ("取消刚才不吃鸡蛋这一条；海鲜和花生过敏仍然保持，其他要求不变。", "clarify"),
])
def test_known_reference_is_not_a_new_missing_allergen(message, action):
    state = SessionState(session_id="mock", user_id=3, constraints=Constraints(allergies=["海鲜", "花生"]))
    assert not requires_allergy_clarification(message, Intent(action=action), state)


@pytest.mark.parametrize("message", [
    "没有其他忌口，但还有一种过敏没说明", "我还有一种过敏，具体忘了",
    "另一个人也过敏，是什么还不清楚", "解释一下过敏检查，另外我还有一种过敏",
])
def test_unattributed_unknown_allergy_cannot_be_hidden_by_known_facts(message):
    state = SessionState(session_id="mock", user_id=3, constraints=Constraints(allergies=["花生"]))
    assert requires_allergy_clarification(message, Intent(allergies=["花生"]), state)


async def test_repaired_original_compound_then_dynamic_local_replace_and_explain(tmp_path, catalog):
    first_message = "给我安排1个人的晚餐，总共3道菜，不要汤，没有过敏或其他忌口。"
    agent = MealAgent(catalog, SessionStore(tmp_path / "compound.db"), ScriptedLLM([
        complete_intent(), Intent(action="replace", replace_slot=2), Intent(action="explain"),
    ]))
    first = await agent.chat(3, first_message)
    assert first.status == "ok"
    sid = first.conversation_state.session_id
    second = await agent.chat(3, f"只换掉第二道{first.menu[1].name}", sid)
    assert second.status == "ok"
    assert second.menu[0].recipe_id == first.menu[0].recipe_id
    assert second.menu[2].recipe_id == first.menu[2].recipe_id
    assert second.menu[1].recipe_id != first.menu[1].recipe_id
    third = await agent.chat(3, "只解释当前菜单", sid)
    assert [m.recipe_id for m in third.menu] == [m.recipe_id for m in second.menu]


async def test_j14_real_adapter_with_handwritten_mock_keeps_menu(tmp_path, catalog):
    catalog.profiles[3].allergies = ["海鲜", "花生"]
    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        value = {"reason_ids": ["catalog"]} if "facts" in data else (
            {"action": "explain"} if "解释" in data["message"] else
            {"action": "plan", "people": 1, "meal_type": "晚餐", "restrictions_confirmed": True}
        )
        return httpx.Response(200, json=completion(value))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = MealAgent(catalog, SessionStore(tmp_path / "explain.db"), DeepSeekLLM("mock-only", client=client))
        first = await agent.chat(3, "1人晚餐，按档案忌口")
        assert first.status == "ok"
        second = await agent.chat(3, "先不要换菜，只解释这三道为什么适合我的偏好，以及你怎样检查海鲜和花生过敏。", first.conversation_state.session_id)
        assert second.status == "ok"
        assert [m.recipe_id for m in second.menu] == [m.recipe_id for m in first.menu]
        assert not second.conversation_state.pending_allergy
        assert not second.clarification_questions


@pytest.mark.parametrize("action", ["plan", "clarify"])
async def test_unambiguous_facts_survive_both_paths_even_if_mock_omits_fields(action, tmp_path, catalog):
    catalog.profiles[3].allergies = ["海鲜", "花生"]
    intent = Intent(action=action, clarification="不能安排含花生菜，请确认取消该要求。" if action == "clarify" else None)
    agent = MealAgent(catalog, SessionStore(tmp_path / "facts.db"), ScriptedLLM([intent, Intent()]))
    first = await agent.chat(3, "按档案安排1个人晚餐，总共3道菜，不要汤，没有其他忌口，但必须含花生。")
    state = first.conversation_state
    assert {"people", "meal_type"} <= set(state.confirmed_fields)
    assert state.constraints.people == 1 and state.constraints.meal_type == "晚餐"
    assert not {q.field for q in first.clarification_questions} & {"people", "meal_type"}
    if action == "clarify":
        assert first.status == "clarification_required" and first.menu == []
        assert first.clarification_questions[0].prompt == first.reason
    second = await agent.chat(3, "那就不要花生，海鲜过敏也仍然要遵守，按其余要求继续。", state.session_id)
    assert second.status == "ok"
    assert set(second.conversation_state.constraints.allergies) == {"海鲜", "花生"}


@pytest.mark.parametrize("text", [
    "随便安排", "如果是1人晚餐呢？", "他说，1人晚餐", "解释‘1人晚餐’",
    "不要安排1人晚餐", "取消1人晚餐",
])
async def test_defaults_and_quoted_meal_facts_are_not_confirmed(text, tmp_path, catalog):
    agent = MealAgent(catalog, SessionStore(tmp_path / "uncertain.db"), ScriptedLLM([Intent(action="clarify", clarification="请补充需求。")]))
    result = await agent.chat(3, text)
    assert not {"people", "meal_type"} & set(result.conversation_state.confirmed_fields)


@pytest.mark.parametrize("known_origin", [True, False])
async def test_legacy_empty_terms_need_proven_origin_before_clearing(known_origin, tmp_path, catalog):
    catalog.profiles[3].allergies = ["海鲜", "花生"]
    store = SessionStore(tmp_path / "legacy.db")
    last = "海鲜和花生过敏仍然保持" if known_origin else "我还有一种过敏，具体忘了"
    old = SessionState(session_id="a" * 32, user_id=3, revision=1,
        constraints=Constraints(allergies=["海鲜", "花生"]),
        confirmed_fields=["people", "meal_type", "restrictions"],
        pending_allergy=True, pending_allergy_terms=[], pending_fields=["allergy"],
        last_message=last, history=[{"role": "user", "content": last}])
    store.save(old, None)
    agent = MealAgent(catalog, store, ScriptedLLM([Intent()]))
    result = await agent.chat(3, "按其余要求继续", old.session_id)
    assert result.conversation_state.pending_allergy is not known_origin
    assert (result.status == "ok") is known_origin


async def test_j19_only_pending_fixed_exclusion_is_not_claimed_removed(tmp_path, catalog):
    catalog.profiles[3].allergies = ["海鲜", "花生"]
    agent = MealAgent(catalog, SessionStore(tmp_path / "unsupported.db"), ScriptedLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(action="clarify", clarification="当前不支持删除普通忌口，鸡蛋限制仍保留。"),
    ]))
    first = await agent.chat(3, "1人晚餐，不吃鸡蛋，按档案忌口")
    second = await agent.chat(3, "取消不吃鸡蛋，海鲜和花生过敏仍然保持", first.conversation_state.session_id)
    assert second.status == "clarification_required"
    assert not second.conversation_state.pending_allergy
    assert "鸡蛋" in second.conversation_state.constraints.excluded_ingredients
    assert "仍保留" in second.reason


async def test_known_restatement_does_not_resolve_a_real_unnamed_question(tmp_path, catalog):
    catalog.profiles[3].allergies = ["花生"]
    agent = MealAgent(catalog, SessionStore(tmp_path / "unresolved.db"), ScriptedLLM([
        complete_intent(), Intent(allergies=["花生"]),
    ]))
    first = await agent.chat(3, "1人晚餐，按档案忌口，但我还有一种过敏，具体忘了")
    assert first.conversation_state.pending_allergy
    second = await agent.chat(3, "花生过敏仍然保持", first.conversation_state.session_id)
    assert second.conversation_state.pending_allergy
    assert second.status == "clarification_required" and not second.menu


@pytest.mark.parametrize("text", ["不确定没有过敏", "也许没有过敏", "如果没有过敏呢？"])
def test_uncertain_negative_is_not_proof_of_no_missing_allergen(text):
    state = SessionState(session_id="mock", user_id=3)
    assert requires_allergy_clarification(text, Intent(), state)


def test_known_extraction_does_not_cover_a_second_person_in_same_clause():
    state = SessionState(session_id="mock", user_id=3, constraints=Constraints(allergies=["花生"]))
    assert requires_allergy_clarification(
        "我对花生过敏且父亲也过敏", Intent(allergies=["花生"]), state
    )


def test_forgetting_specific_allergen_is_not_an_explicit_resolution():
    state = SessionState(session_id="mock", user_id=3, pending_allergy=True,
                         pending_fields=["allergy"], constraints=Constraints(allergies=["花生"]))
    assert not explicit_allergy_resolution("具体忘了，花生过敏照旧", state, ["花生"])


def test_legacy_reference_to_unknown_pending_cannot_prove_itself_resolved():
    text = "过敏限制照旧"
    state = SessionState(session_id="mock", user_id=3, revision=1, pending_allergy=True,
                         pending_fields=["allergy"], last_message=text,
                         history=[{"role": "user", "content": text}])
    repair_proven_reference_pending(state)
    assert state.pending_allergy
