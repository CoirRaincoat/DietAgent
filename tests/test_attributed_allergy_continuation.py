"""Public owner/term resolution and authorized pending-plan continuation."""

import pytest

from app.domain.models import DinerUpdate, Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_explicit_method_protection import dish
from tests.test_independent_meat_quota import public_catalog

FIRST = "我和朋友2人晚餐，朋友说对籽类过敏，我也不吃辣。三道包括一汤，帮我们安排一荤一素加汤。"
ANSWER = "朋友说这里只指花生过敏，我们都不吃辣，没有其他忌口。继续安排刚才那顿晚餐。"


def first_intent(extra=None, action="plan"):
    return complete_intent(
        action=action,
        people=2 + len(extra or []),
        dish_count=3,
        soup_count=1,
        diner_updates=[
            DinerUpdate(diner="朋友", aliases=["小林"], allergies=["籽类"]),
            DinerUpdate(diner="我", no_spicy=True),
            *(extra or []),
        ],
    )


def answer_intent(owner="朋友", replacements=None):
    return Intent(
        action="clarify",
        no_spicy=True,
        restrictions_confirmed=True,
        diner_updates=[
            DinerUpdate(
                diner=owner,
                allergy_clarifications=replacements or {"籽类": ["花生"]},
                no_spicy=True,
            )
        ],
    )


def public_pool():
    rows = [
        ("清炒鸡肉", "鸡肉200克；盐1克", "鸡肉炒熟后装盘。"),
        ("清蒸豆腐", "豆腐200克；盐1克", "豆腐蒸熟后装盘。"),
        ("玉米冬瓜汤", "玉米100克；冬瓜200克；水500克", "玉米冬瓜煮熟后装碗。"),
        ("花生炒鸡", "鸡肉200克；花生5克", "鸡肉炒熟后装盘。"),
        ("辣炒鸡", "鸡肉200克；辣椒5克", "鸡肉炒熟后装盘。"),
        ("鸡蛋羹", "鸡蛋200克；水100克", "鸡蛋蒸熟后装盘。"),
        ("清蒸鱼", "鲈鱼200克；盐1克", "鲈鱼蒸熟后装盘。"),
    ]
    return [dish(name, steps, foods=foods) for name, foods, steps in rows]


def ask(client, message, session=None, **extra):
    payload = dict(user_id=3, message=message, **extra)
    if session:
        payload["session_id"] = session
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def named(result, name="朋友"):
    return next(d for d in result["conversation_state"]["diners"] if d["display_name"] == name)


@pytest.mark.parametrize("restart", [False, True])
def test_known_friend_answer_resumes_meal_and_preserves_identity_after_restart(tmp_path, restart):
    catalog = public_catalog(public_pool())
    catalog.profiles[3].allergies = ["鸡蛋"]
    llm = ScriptedLLM([first_intent(), Intent(action="clarify")])
    if not restart:
        llm.intents.append(answer_intent())
    with client_for(tmp_path, catalog, llm) as client:
        first = ask(client, FIRST)
        sid = first["conversation_state"]["session_id"]
        wait = ask(client, "继续。", sid)
        if not restart:
            result = ask(client, ANSWER, sid)
    if restart:
        with client_for(tmp_path, catalog, ScriptedLLM([answer_intent()])) as client:
            result = ask(client, ANSWER, sid)
    for pending in (first, wait):
        assert pending["status"] == "clarification_required" and not pending["menu"]
        assert named(pending)["pending_allergy_terms"] == ["籽类"]
        assert named(pending)["attendance"] and pending["conversation_state"]["pending_plan"]
    assert result["status"] == "ok" and len(result["menu"]) == 3
    assert named(result)["diner_id"] == named(first)["diner_id"]
    assert named(result)["allergies"] == ["花生"] and not named(result)["pending_allergy_terms"]
    assert set(result["conversation_state"]["constraints"]["allergies"]) == {"花生", "鸡蛋"}
    assert result["conversation_state"]["constraints"]["no_spicy"]
    assert result["conversation_state"]["constraints"]["soup_count"] == 1
    assert result["conversation_state"]["pending_plan"] is False
    for item in result["menu"] + result["replacement_suggestions"]:
        assert not {"花生", "鸡蛋", "辣椒"} & set(item["ingredients"])


@pytest.mark.parametrize(
    "message",
    [
        "朋友说这里只指花生过敏，没有其他忌口。继续安排刚才那顿晚餐。",
        "小林说籽类具体指花生，其他要求不变，继续安排晚餐。",
        "朋友的籽类只指花生，继续安排。",
        "朋友过敏的籽类具体是花生，继续安排晚餐。",
    ],
)
def test_finite_attribution_or_alias_maps_only_pending_owner(tmp_path, message):
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first_intent(), answer_intent()])
    ) as client:
        first = ask(client, FIRST)
        result = ask(client, message, first["conversation_state"]["session_id"])
    assert result["status"] == "ok" and named(result)["allergies"] == ["花生"]


@pytest.mark.parametrize(
    "message",
    [
        "朋友说这里只指花生过敏吗？继续安排晚餐。",
        "如果朋友这里指花生，继续安排晚餐。",
        "朋友说‘籽类就是花生’，先解释这句话。",
        "朋友另外也对花生过敏，继续安排晚餐。",
        "朋友说籽类不是花生过敏，继续安排晚餐。",
        "明天具体吃什么，今天花生先别放，继续安排晚餐。",
        "朋友说这里只指芝麻过敏，继续安排晚餐。",
    ],
)
def test_question_addition_negation_and_unmentioned_model_values_never_clear_pending(
    tmp_path, message
):
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first_intent(), answer_intent()])
    ) as client:
        first = ask(client, FIRST)
        result = ask(client, message, first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result)["pending_allergy_terms"] == ["籽类"]


def test_named_answer_does_not_resolve_another_person_with_same_pending_word(tmp_path):
    extra = [DinerUpdate(diner="爸爸", allergies=["籽类"])]
    bogus = answer_intent()
    bogus.diner_updates.append(DinerUpdate(diner="爸爸", allergy_clarifications={"籽类": ["花生"]}))
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first_intent(extra), bogus])
    ) as client:
        first = ask(client, FIRST + "爸爸也来吃，他的籽类还没有说明。")
        result = ask(
            client,
            "朋友说这里只指花生过敏，继续安排晚餐。",
            first["conversation_state"]["session_id"],
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result)["allergies"] == ["花生"]
    assert named(result, "爸爸")["pending_allergy_terms"] == ["籽类"]
    assert "花生" not in named(result, "爸爸")["allergies"]


def test_one_term_resolution_does_not_clear_a_second_term_from_the_same_owner(tmp_path):
    initial = first_intent()
    initial.diner_updates[0].allergies = ["籽类", "神秘酱料"]
    bogus = answer_intent(replacements={"籽类": ["花生"], "神秘酱料": ["花生"]})
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, bogus])
    ) as client:
        first = ask(client, FIRST + "朋友的神秘酱料也没说明。")
        result = ask(
            client, "朋友说籽类只指花生，继续安排晚餐。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result)["pending_allergy_terms"] == ["神秘酱料"]
    assert named(result)["allergies"] == ["花生"]


@pytest.mark.parametrize(
    "suffix", ["先别推荐。", "只解释菜单。", "计算精确蛋白质。", "改成午餐四道菜。"]
)
def test_valid_mapping_with_a_different_operation_does_not_silently_resume(tmp_path, suffix):
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first_intent(), answer_intent()])
    ) as client:
        first = ask(client, FIRST)
        result = ask(
            client, "朋友说籽类只指花生，" + suffix, first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result)["allergies"] == ["花生"] and not named(result)["pending_allergy_terms"]
    assert result["conversation_state"]["pending_plan"]


def test_allergy_answer_without_a_requested_meal_never_starts_one(tmp_path):
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM([first_intent(action="clarify"), answer_intent()]),
    ) as client:
        first = ask(client, "朋友籽类过敏，先问清楚，不需要排菜。")
        result = ask(client, "朋友说这里指花生过敏。", first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and not result["menu"]
    assert not result["conversation_state"]["pending_plan"]
    assert named(result)["allergies"] == ["花生"]


def test_named_resolution_replay_stale_request_and_other_user_do_not_mutate(tmp_path):
    llm = ScriptedLLM([first_intent(), answer_intent(), Intent(action="explain")])
    with client_for(tmp_path, public_catalog(public_pool()), llm) as client:
        first = ask(client, FIRST)
        sid = first["conversation_state"]["session_id"]
        result = ask(client, ANSWER, sid, request_id="friend-specific-answer")
        replay = ask(client, ANSWER, sid, request_id="friend-specific-answer")
        assert result == replay and result["status"] == "ok" and llm.parse_calls == 2
        after = ask(client, "只解释这餐，不改菜。", sid)
        stale = client.post(
            "/chat",
            json=dict(
                user_id=3, session_id=sid, message=ANSWER, request_id="friend-specific-answer"
            ),
        )
        cross = client.post("/chat", json=dict(user_id=4, session_id=sid, message="继续"))
    assert stale.status_code == cross.status_code == 409 and llm.parse_calls == 3
    assert after["menu"] == result["menu"]


def test_two_named_owners_can_answer_different_foods_in_one_reply(tmp_path):
    initial = first_intent([DinerUpdate(diner="爸爸", allergies=["籽类"])])
    answer = answer_intent()
    answer.diner_updates.append(
        DinerUpdate(diner="爸爸", allergy_clarifications={"籽类": ["芝麻"]})
    )
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, answer])
    ) as client:
        first = ask(client, FIRST + "爸爸也来吃，爸爸籽类过敏。")
        result = ask(
            client,
            "朋友说籽类只指花生，爸爸说籽类只指芝麻，继续安排晚餐。",
            first["conversation_state"]["session_id"],
        )
    assert result["status"] == "ok" and len(result["menu"]) == 3
    assert named(result)["allergies"] == ["花生"]
    assert named(result, "爸爸")["allergies"] == ["芝麻"]
    assert all(
        not diner["pending_allergy_terms"] for diner in result["conversation_state"]["diners"]
    )


def test_bare_answer_cannot_resolve_two_owners_with_the_same_unknown_term(tmp_path):
    initial = first_intent([DinerUpdate(diner="爸爸", allergies=["籽类"])])
    bogus = answer_intent()
    bogus.diner_updates.append(DinerUpdate(diner="爸爸", allergy_clarifications={"籽类": ["花生"]}))
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, bogus])
    ) as client:
        first = ask(client, FIRST + "爸爸也来吃，爸爸籽类过敏。")
        result = ask(
            client, "具体是花生，继续安排晚餐。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert (
        named(result)["pending_allergy_terms"]
        == named(result, "爸爸")["pending_allergy_terms"]
        == ["籽类"]
    )


def test_last_remaining_named_term_resumes_after_partial_answer(tmp_path):
    initial = first_intent()
    initial.diner_updates[0].allergies = ["籽类", "神秘酱料"]
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM(
            [
                initial,
                answer_intent(),
                answer_intent(replacements={"神秘酱料": ["芝麻"]}),
            ]
        ),
    ) as client:
        first = ask(client, FIRST + "朋友的神秘酱料也过敏。")
        sid = first["conversation_state"]["session_id"]
        partial = ask(client, "朋友说籽类只指花生。", sid)
        final = ask(client, "朋友说神秘酱料只指芝麻，继续安排刚才那顿晚餐。", sid)
    assert partial["status"] == "clarification_required" and not partial["menu"]
    assert named(partial)["pending_allergy_terms"] == ["神秘酱料"]
    assert final["status"] == "ok" and set(named(final)["allergies"]) == {"花生", "芝麻"}
    assert not final["conversation_state"]["pending_plan"]


@pytest.mark.parametrize(
    "message", ["花生、芝麻", "花生，芝麻", "朋友说籽类只指花生和芝麻，继续安排晚餐。"]
)
def test_single_target_accepts_complete_multi_food_answers(tmp_path, message):
    with client_for(
        tmp_path,
        public_catalog(public_pool()),
        ScriptedLLM(
            [
                first_intent(),
                answer_intent(replacements={"籽类": ["花生", "芝麻"]}),
            ]
        ),
    ) as client:
        first = ask(client, FIRST)
        result = ask(client, message, first["conversation_state"]["session_id"])
    assert result["status"] == "ok" and set(named(result)["allergies"]) == {"花生", "芝麻"}


@pytest.mark.parametrize(
    "message", ["花生过敏吗？", "不是花生过敏。", "如果是花生过敏。", "‘花生过敏’是什么意思？"]
)
def test_unnamed_global_pending_allergy_needs_a_raw_affirmative_answer(tmp_path, message):
    llm = ScriptedLLM([complete_intent(action="clarify"), Intent(allergies=["花生"])])
    with client_for(tmp_path, public_catalog(public_pool()), llm) as client:
        first = ask(client, "1人晚餐，对一些东西过敏。")
        assert first["conversation_state"]["pending_allergy"]
        result = ask(client, message, first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and not result["menu"]
    assert result["conversation_state"]["pending_allergy"]


def test_global_and_friend_pending_terms_cannot_be_resolved_by_one_bare_mapping(tmp_path):
    initial = first_intent()
    initial.allergies = ["籽类"]
    bogus = answer_intent()
    bogus.allergy_clarifications = {"籽类": ["花生"]}
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, bogus])
    ) as client:
        first = ask(client, FIRST + "我的籽类也未说明。")
        result = ask(
            client, "具体是花生，继续安排晚餐。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert result["conversation_state"]["pending_allergy_terms"] == ["籽类"]
    assert named(result)["pending_allergy_terms"] == ["籽类"]


def test_global_and_friend_terms_accept_separate_named_answers(tmp_path):
    initial = first_intent()
    initial.allergies = ["籽类"]
    answer = answer_intent()
    answer.allergy_clarifications = {"籽类": ["芝麻"]}
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([initial, answer])
    ) as client:
        first = ask(client, FIRST + "我的籽类也未说明。")
        result = ask(
            client,
            "我说籽类只指芝麻，朋友说籽类只指花生，继续安排晚餐。",
            first["conversation_state"]["session_id"],
        )
    assert result["status"] == "ok" and len(result["menu"]) == 3
    assert not result["conversation_state"]["pending_allergy_terms"]
    assert named(result)["allergies"] == ["花生"]
    assert set(result["conversation_state"]["constraints"]["allergies"]) == {"花生", "芝麻"}


def test_resume_allowlist_is_bound_to_the_previously_requested_meal(tmp_path):
    answer = answer_intent()
    answer.meal_type = "午餐"
    with client_for(
        tmp_path, public_catalog(public_pool()), ScriptedLLM([first_intent(), answer])
    ) as client:
        first = ask(client, FIRST)
        result = ask(
            client, "朋友说籽类只指花生，继续安排午餐。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and not result["menu"]
    assert named(result)["allergies"] == ["花生"]
    assert result["conversation_state"]["pending_plan"]
