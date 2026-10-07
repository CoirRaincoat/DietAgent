"""Public partial-extraction regressions: each mention needs its own evidence."""

import json

import httpx
import pytest

from app.agent.service import MealAgent
from app.domain.allergy_mentions import requires_allergy_clarification
from app.domain.models import Constraints, Diner, DinerUpdate, Intent, SessionState
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionStore
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_attributed_allergy_continuation import ask, named, public_pool
from tests.test_independent_meat_quota import public_catalog
from tests.test_llm import completion

DAD = dict(diner="爸爸", allergies=["花生"])
MOM = dict(diner="妈妈", attendance=True)
DIRECT_CASES = [
    ("不辣且虾过敏", [], ["虾"], False),
    ("不辣、虾过敏", [], ["虾"], False),
    ("不要辣且花生过敏", [], ["花生"], False),
    ("不辣且虾和另一种食物过敏", [], ["虾"], True),
    ("不辣、虾和另一种食物过敏", [], ["虾"], True),
    ("不要辣且花生和芝麻过敏", [], ["花生"], True),
    ("爸爸花生过敏，妈妈有食物过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏，妈妈对一些东西过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏但妈妈有食物过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏、妈妈有食物过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏，妈妈对神秘酱料过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏，妈妈有食物过敏", [DAD], [], True),
    ("妈妈花生过敏", [DAD, MOM], [], True),
    ("爸爸对花生和芝麻过敏", [DAD], [], True),
    ("爸爸和妈妈都对花生过敏", [DAD, MOM], [], True),
    ("爸爸花生过敏，妈妈芝麻过敏", [DAD, MOM], [], True),
    ("我花生和另一种食物过敏", [], ["花生"], True),
    ("我花生过敏，另外我还有其他食物过敏", [], ["花生"], True),
    ("爸爸花生过敏，妈妈有食物过敏", [MOM], ["花生"], True),
    ("爸爸花生过敏，妈妈花生过敏吗？", [DAD, dict(diner="妈妈", allergies=["花生"])], [], True),
    ("爸爸花生过敏，妈妈没有其他过敏", [DAD, MOM], [], False),
    ("爸爸花生过敏，妈妈无食物过敏史", [DAD, MOM], [], False),
    ("爸爸花生过敏，妈妈芝麻过敏", [DAD, dict(diner="妈妈", allergies=["芝麻"])], [], False),
    ("爸爸和妈妈都对花生过敏", [DAD, dict(diner="妈妈", allergies=["花生"])], [], False),
    ("爸爸花生和芝麻过敏", [dict(diner="爸爸", allergies=["花生", "芝麻"])], [], False),
    ("爸爸对神秘酱料过敏", [dict(diner="爸爸", allergies=["神秘酱料"])], [], False),
    ("我对花生过敏", [], ["花生"], False),
    ("1人晚餐，没有其他忌口，安排三道菜", [], [], False),
]


@pytest.mark.parametrize("message,updates,global_foods,expected", DIRECT_CASES)
def test_partial_fields_do_not_blanket_cover_unrelated_mentions(
    message, updates, global_foods, expected
):
    state = SessionState(session_id="a" * 32, user_id=3)
    intent = Intent(
        allergies=global_foods, diner_updates=[DinerUpdate(**value) for value in updates]
    )
    assert requires_allergy_clarification(message, intent, state) is expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("爸爸的过敏限制照旧，妈妈有食物过敏", True),
        ("爸爸的过敏限制照旧，妈妈的过敏限制照旧", True),
        ("爸爸的过敏限制照旧，妈妈没有其他过敏", False),
        ("我爸的过敏限制照旧", False),
    ],
)
def test_reference_covers_only_the_named_known_person(message, expected):
    state = SessionState(
        session_id="a" * 32,
        user_id=3,
        constraints=Constraints(allergies=["花生"]),
        diners=[Diner(diner_id="dad", display_name="爸爸", aliases=["我爸"], allergies=["花生"])],
    )
    assert (
        requires_allergy_clarification(message, Intent(diner_updates=[DinerUpdate(**DAD)]), state)
        is expected
    )


def initial_intent(include_mom=True):
    return complete_intent(
        people=3,
        dish_count=3,
        soup_count=1,
        no_spicy=True,
        diner_updates=[DinerUpdate(**DAD), *([DinerUpdate(**MOM)] if include_mom else [])],
    )


FIRST = "我和爸妈3人晚餐，爸爸花生过敏，妈妈有食物过敏。都不吃辣，安排3道，其中1道汤。"
ANSWER = "妈妈具体是芝麻过敏，没有其他忌口，继续安排晚餐。"


@pytest.mark.parametrize("restart", [False, True])
def test_missing_mom_is_blocked_then_resumes_with_known_dad_constraints(tmp_path, restart):
    catalog = public_catalog(public_pool())
    catalog.profiles[3].allergies = ["鸡蛋"]
    answer = Intent(action="clarify", diner_updates=[DinerUpdate(diner="妈妈", allergies=["芝麻"])])
    llm = ScriptedLLM([initial_intent(), Intent(action="plan")])
    if not restart:
        llm.intents.append(answer)
    with client_for(tmp_path, catalog, llm) as client:
        first = ask(client, FIRST)
        sid = first["conversation_state"]["session_id"]
        wait = ask(client, "先安排一份吧，原来要求不变。", sid)
        if not restart:
            result = ask(client, ANSWER, sid)
    if restart:
        with client_for(tmp_path, catalog, ScriptedLLM([answer])) as client:
            result = ask(client, ANSWER, sid)
    for pending in (first, wait):
        assert pending["status"] == "clarification_required" and pending["menu"] == []
        assert named(pending, "妈妈")["pending_allergy"]
        assert pending["conversation_state"]["pending_plan"]
        assert pending["conversation_state"]["pending_allergy"] is False
        assert named(pending, "爸爸")["allergies"] == ["花生"]
    assert result["status"] == "ok" and len(result["menu"]) == 3
    assert named(result, "妈妈")["allergies"] == ["芝麻"]
    assert not named(result, "妈妈")["pending_allergy"]
    assert named(result, "爸爸")["diner_id"] == named(first, "爸爸")["diner_id"]
    assert set(result["conversation_state"]["constraints"]["allergies"]) == {"花生", "鸡蛋", "芝麻"}
    assert result["conversation_state"]["constraints"]["no_spicy"]
    assert not result["conversation_state"]["pending_plan"]


def test_literal_known_relative_missing_from_extraction_gets_stable_pending_record(tmp_path):
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial_intent(include_mom=False)])
    ) as client:
        result = ask(client, FIRST)
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result, "妈妈")["pending_allergy"]
    assert not result["conversation_state"]["pending_allergy"]


def test_local_new_missing_allergy_does_not_publish_or_replan_old_menu(tmp_path):
    first_intent = initial_intent()
    first_intent.diner_updates[1].allergies = ["芝麻"]
    followup = Intent(action="replace", replace_slot=2, diner_updates=[DinerUpdate(**DAD)])
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM([first_intent, followup, Intent(action="plan")]),
    ) as client:
        first = ask(client, FIRST.replace("妈妈有食物过敏", "妈妈芝麻过敏"))
        assert first["status"] == "ok"
        second = ask(
            client,
            "只换第二道，爸爸花生过敏照旧，妈妈另外还有其他食物过敏。",
            first["conversation_state"]["session_id"],
        )
        assert second["status"] == "clarification_required" and not second["menu"]
        assert named(second, "妈妈")["pending_allergy"]
        assert not second["conversation_state"]["menu_valid"]
        assert second["conversation_state"]["menu_ids"] == first["conversation_state"]["menu_ids"]
        assert not any(event["name"] == "menu_modify" for event in second["tool_calls"])


def test_unknown_absent_person_is_retained_but_does_not_block_active_table(tmp_path):
    intent = complete_intent(
        people=2,
        no_spicy=True,
        diner_updates=[DinerUpdate(**DAD), DinerUpdate(diner="妈妈", attendance=False)],
    )
    with client_for(tmp_path, public_catalog(public_pool()), ScriptedLLM([intent])) as client:
        result = ask(
            client,
            "爸爸和我2人晚餐，爸爸花生过敏；妈妈今晚不来，妈妈有食物过敏；不辣，三道含一汤。",
        )
    assert result["status"] == "ok" and len(result["menu"]) == 3
    assert named(result, "妈妈")["pending_allergy"] and not named(result, "妈妈")["attendance"]


@pytest.mark.parametrize("complete", [False, True])
async def test_real_provider_boundary_matches_service_without_paid_calls(tmp_path, complete):
    catalog = public_catalog(public_pool())
    first = initial_intent()
    if complete:
        first.diner_updates[1].allergies = ["芝麻"]
    first_message = FIRST.replace("妈妈有食物过敏", "妈妈芝麻过敏") if complete else FIRST
    answers = {
        first_message: first.model_dump(mode="json"),
        ANSWER: Intent(
            action="clarify",
            clarification="已记录，是否安排原餐？",
            diner_updates=[DinerUpdate(diner="妈妈", allergies=["芝麻"])],
        ).model_dump(mode="json"),
    }

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(
            200,
            json=completion(
                {"reason_ids": ["opening"]} if "facts" in payload else answers[payload["message"]]
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        agent = MealAgent(
            catalog,
            SessionStore(tmp_path / "provider.db"),
            DeepSeekLLM("fake-test-secret", client=http),
        )
        result = await agent.chat(3, first_message)
        if complete:
            assert result.status == "ok"
        else:
            assert result.status == "clarification_required" and result.menu == []
            assert result.conversation_state.pending_plan
            resumed = await agent.chat(3, ANSWER, result.conversation_state.session_id)
            assert resumed.status == "ok" and len(resumed.menu) == 3
    assert "_allergy_guard_plan" not in Intent.model_json_schema()["properties"]


def test_model_output_cannot_select_internal_pending_plan_authority():
    with pytest.raises(ValueError):
        Intent.model_validate(dict(action="clarify", _allergy_guard_plan=True))


def test_pending_mixed_reply_replay_and_other_user_preserve_scope(tmp_path):
    llm = ScriptedLLM(
        [
            initial_intent(),
            Intent(action="clarify", diner_updates=[DinerUpdate(diner="妈妈", allergies=["芝麻"])]),
        ]
    )
    with client_for(tmp_path, public_catalog(public_pool()), llm) as client:
        first = ask(client, FIRST, request_id="mixed-new-unknown")
        assert first["status"] == "clarification_required" and not first["menu"]
        sid = first["conversation_state"]["session_id"]
        replay = ask(client, FIRST, sid, request_id="mixed-new-unknown")
        assert first == replay and llm.parse_calls == 1
        cross = client.post("/chat", json=dict(user_id=4, session_id=sid, message=ANSWER))
        assert cross.status_code == 409 and llm.parse_calls == 1
        final = ask(client, ANSWER, sid)
    assert final["status"] == "ok" and named(final, "妈妈")["allergies"] == ["芝麻"]


def test_partial_food_list_retains_known_food_and_blocks_unaccounted_part(tmp_path):
    first = complete_intent(people=2, no_spicy=True, diner_updates=[DinerUpdate(**DAD)])
    answer = Intent(action="clarify", diner_updates=[DinerUpdate(diner="爸爸", allergies=["芝麻"])])
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first, answer])
    ) as client:
        pending = ask(client, "我和爸爸2人晚餐，爸爸花生和芝麻过敏；不辣，三道含一汤。")
        assert pending["status"] == "clarification_required" and not pending["menu"]
        assert named(pending, "爸爸")["allergies"] == ["花生"]
        assert named(pending, "爸爸")["pending_allergy"]
        final = ask(
            client,
            "爸爸具体是芝麻过敏，继续安排晚餐。",
            pending["conversation_state"]["session_id"],
        )
    assert final["status"] == "ok" and set(named(final, "爸爸")["allergies"]) == {"花生", "芝麻"}


def test_dynamic_existing_alias_is_not_an_unattributed_global_question(tmp_path):
    first = initial_intent()
    first.diner_updates[1].allergies = ["芝麻"]
    first.diner_updates[1].aliases = ["小林"]
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM([first, Intent(diner_updates=[DinerUpdate(**DAD)])]),
    ) as client:
        initial = ask(client, FIRST.replace("妈妈有食物过敏", "妈妈芝麻过敏"))
        assert initial["status"] == "ok"
        result = ask(
            client,
            "爸爸花生过敏照旧，小林还有其他食物过敏。",
            initial["conversation_state"]["session_id"],
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result, "妈妈")["pending_allergy"]
    assert not result["conversation_state"]["pending_allergy"]
    assert named(result, "妈妈")["diner_id"] == named(initial, "妈妈")["diner_id"]


def test_absent_pending_owner_blocks_on_return_then_can_answer(tmp_path):
    initial = complete_intent(
        people=2,
        no_spicy=True,
        diner_updates=[DinerUpdate(**DAD), DinerUpdate(diner="妈妈", attendance=False)],
    )
    join = Intent(people=3, diner_updates=[DinerUpdate(diner="妈妈", attendance=True)])
    answer = Intent(action="clarify", diner_updates=[DinerUpdate(diner="妈妈", allergies=["芝麻"])])
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, join, answer])
    ) as client:
        first = ask(
            client, "我和爸爸2人晚餐，爸爸花生过敏；妈妈不来，妈妈有食物过敏；不辣，三道含一汤。"
        )
        assert first["status"] == "ok" and named(first, "妈妈")["pending_allergy"]
        sid = first["conversation_state"]["session_id"]
        pending = ask(client, "妈妈也来吃，改成3人。", sid)
        assert pending["status"] == "clarification_required" and not pending["menu"]
        assert named(pending, "妈妈")["attendance"] and named(pending, "妈妈")["pending_allergy"]
        final = ask(client, ANSWER, sid)
    assert (
        final["status"] == "ok"
        and named(final, "妈妈")["diner_id"] == named(first, "妈妈")["diner_id"]
    )


def test_explanation_guard_is_not_new_planning_authority(tmp_path):
    first = initial_intent()
    first.diner_updates[1].allergies = ["芝麻"]
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM(
            [
                first,
                Intent(action="explain", diner_updates=[DinerUpdate(**DAD)]),
                Intent(
                    action="clarify", diner_updates=[DinerUpdate(diner="妈妈", allergies=["牛奶"])]
                ),
            ]
        ),
    ) as client:
        initial = ask(client, FIRST.replace("妈妈有食物过敏", "妈妈芝麻过敏"))
        sid = initial["conversation_state"]["session_id"]
        pending = ask(client, "解释一下这份菜单，爸爸花生过敏，妈妈另外有食物过敏。", sid)
        assert pending["status"] == "clarification_required" and not pending["menu"]
        assert not pending["conversation_state"]["pending_plan"]
        resolved = ask(client, "妈妈具体是牛奶过敏。", sid)
    assert resolved["status"] == "clarification_required" and not resolved["menu"]
    assert not named(resolved, "妈妈")["pending_allergy"]
    assert set(named(resolved, "妈妈")["allergies"]) == {"芝麻", "牛奶"}


def test_private_guard_provenance_survives_copy_but_never_serializes():
    intent = Intent(action="clarify", clarification="等待具体食材")
    intent._allergy_guard_plan = True
    assert intent.model_copy()._allergy_guard_plan
    assert "_allergy_guard_plan" not in intent.model_dump()
    assert "_allergy_guard_plan" not in Intent.model_json_schema()["properties"]
