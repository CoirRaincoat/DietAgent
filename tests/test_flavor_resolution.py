"""Actual persisted soft-flavor retraction, never safety or scope removal."""

import pytest
from fastapi.testclient import TestClient

from app.agent.flavor_resolution import binding, choice, make_pending, retract
from app.agent.response_copy import required_fact_ids
from app.domain.matching_tags import flavor_conflicts
from app.domain.models import Constraints, Diner, Intent, SessionState
from tests.test_negative_flavor import IgnoringNegativeLLM, make_app, recipe


class ConflictLLM(IgnoringNegativeLLM):
    async def parse(self, message, state, profile):
        intent = await super().parse(message, state, profile)
        if not state.menu_ids:
            return intent.model_copy(update={"preferences": ["酸甜"]})
        return intent


def ask(client, message, session=None, **extra):
    payload = {"user_id": 900001, "message": message, **extra}
    if session:
        payload["session_id"] = session
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def conflict(client):
    initial = ask(client, "1人晚餐1道菜，想吃酸甜，无其他忌口。")
    sid = initial["conversation_state"]["session_id"]
    return sid, ask(client, "现在不要酸。", sid)


def option(question, text):
    choices = question["clarification_questions"][0]["options"]
    found = [value for value in choices if value.endswith(text)]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("withdraw,expected", [("酸甜", "蒸白菜"), ("现在不要酸", "糖醋白菜")])
def test_selected_source_clause_retraction_resumes_and_survives_restart(
    tmp_path, withdraw, expected
):
    records = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        sid, question = conflict(client)
        assert question["status"] == "clarification_required" and question["menu"] == []
        choice = option(question, withdraw)
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        selected = ask(client, choice, sid, request_id="confirmed-1")
        assert selected == ask(client, choice, sid, request_id="confirmed-1")
    assert selected["status"] == "ok"
    assert [r["name"] for r in selected["menu"]] == [expected]
    assert withdraw not in selected["conversation_state"]["meal_constraints"]["preferences"]
    assert "撤回" in selected["reason"] and withdraw in selected["reason"]
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        continued = ask(client, "继续", sid)
    assert [r["name"] for r in continued["menu"]] == [expected]


@pytest.mark.parametrize("answer", ["继续", "随便", "取消酸甜", "保留不酸"])
def test_ambiguous_reply_does_not_remove_or_plan(tmp_path, answer):
    records = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        sid, question = conflict(client)
        result = ask(client, answer, sid)
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["conversation_state"]["meal_constraints"]["preferences"] == ["酸甜", "现在不要酸"]
    assert (
        result["clarification_questions"][0]["options"]
        == question["clarification_questions"][0]["options"]
    )


def source_state(meal, diners=()):
    from app.agent.diners import aggregate_constraints

    constraints = Constraints(preferences=list(meal), no_spicy=True, allergies=["花生"])
    return SessionState(
        session_id="f" * 32,
        user_id=900001,
        meal_constraints=constraints,
        diners=list(diners),
        constraints=aggregate_constraints(constraints, list(diners)),
    )


@pytest.mark.parametrize(
    "entry,removed,remaining",
    [
        ("喜欢酸甜、护心、喜欢蒜香", "喜欢酸甜", "护心、喜欢蒜香"),
        ("喜欢酸甜、不要辣", "喜欢酸甜", "不要辣"),
        ("不要甜但喜欢酸", "不要甜", "但喜欢酸"),
        ("喜欢蒜香；喜欢酸甜；只用冰箱食材", "喜欢酸甜", "喜欢蒜香；；只用冰箱食材"),
    ],
)
def test_only_exact_pure_clause_removed_other_raw_text_and_safety_preserved(
    entry, removed, remaining
):
    opposing = "喜欢甜" if removed == "不要甜" else "不要酸"
    state = source_state([entry, opposing])
    rows = [recipe("蒸白菜")]
    pending = make_pending(state, rows, "plan", None)
    selected = next(o for o in pending.options if o.removed_clause == removed)
    retract(state, pending, selected, selected.display)
    assert state.meal_constraints.preferences == [remaining, opposing]
    assert state.meal_constraints.no_spicy and state.meal_constraints.allergies == ["花生"]
    assert state.last_flavor_retraction.after == remaining


@pytest.mark.parametrize(
    "entry", ["喜欢酸甜不辣", "想吃酸甜白菜", "酸甜降压", "如果喜欢酸甜", "“喜欢酸甜”"]
)
def test_unknown_safety_mixed_food_hypothetical_and_quoted_not_auto_removable(entry):
    state = source_state([entry, "不要酸"])
    pending = make_pending(state, [recipe("蒸白菜")], "plan", None)
    assert not any(o.before == entry for o in pending.options)


@pytest.mark.parametrize("entry", ["不要辣", "不要酸辣", "不吃辣", "不辣"])
def test_spicy_exclusion_never_offered_for_retraction(entry):
    state = source_state(["喜欢酸辣", entry])
    pending = make_pending(state, [recipe("蒸白菜")], "plan", None)
    assert not any(o.before == entry for o in pending.options)


def test_forged_remainder_cannot_remove_other_text_or_safety():
    state = source_state(["喜欢酸甜、不要辣", "不要酸"])
    pending = make_pending(state, [recipe("蒸白菜")], "plan", None)
    selected = next(o for o in pending.options if o.removed_clause == "喜欢酸甜")
    forged = selected.model_copy(update={"after": ""})
    pending.options = [forged]
    with pytest.raises(ValueError, match="pure-flavor"):
        retract(state, pending, forged, forged.display)
    assert state.meal_constraints.preferences[0] == "喜欢酸甜、不要辣"


def test_diner_identity_retraction_only_this_attending_owner():
    diners = [
        Diner(
            diner_id="owner",
            display_name="用户",
            preferences=["喜欢酸甜"],
            profile_owner=True,
            allergies=["花生"],
            no_spicy=True,
        ),
        Diner(diner_id="guest", display_name="朋友", preferences=["喜欢酸甜"]),
    ]
    state = source_state(["不要酸"], diners)
    rows = [recipe("蒸白菜")]
    pending = make_pending(state, rows, "plan", None)
    selected = next(o for o in pending.options if o.diner_id == "owner")
    retract(state, pending, selected, selected.display)
    assert state.diners[0].preferences == [] and state.diners[1].preferences == ["喜欢酸甜"]
    assert diners[0].preferences == []  # Session-owned objects; source profile is separate.
    assert state.diners[0].allergies == ["花生"] and state.diners[0].no_spicy
    from app.agent.diners import aggregate_constraints

    state.constraints = aggregate_constraints(state.meal_constraints, state.diners)
    assert flavor_conflicts(state.constraints.preferences)
    second = make_pending(state, rows, "plan", None)
    assert not any(o.diner_id == "owner" for o in second.options)
    assert any(o.diner_id == "guest" for o in second.options)


@pytest.mark.parametrize(
    "change", ["catalog", "attendance", "allergy", "inventory", "menu", "scope"]
)
def test_binding_changes_for_source_safety_menu_and_scope(change):
    state = source_state(["酸甜", "不要酸"], [Diner(diner_id="a", display_name="用户")])
    records = [recipe("蒸白菜")]
    before = binding(state, records, "replace", 1)
    slot = 1
    if change == "catalog":
        records = [records[0].model_copy(update={"steps": "白菜煮熟装盘。"})]
    elif change == "attendance":
        state.diners[0].attendance = False
    elif change == "allergy":
        state.diners[0].allergies.append("牛奶")
    elif change == "inventory":
        state.meal_constraints.inventory = ["白菜"]
    elif change == "menu":
        state.menu_ids = [records[0].recipe_id]
    else:
        slot = 2
    assert before != binding(state, records, "replace", slot)


def test_source_change_same_id_after_restart_refuses_old_choice(tmp_path):
    rows = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, rows, ConflictLLM())) as client:
        sid, question = conflict(client)
        answer = option(question, "酸甜")
    changed = rows[0].model_copy(update={"raw_label": "早餐、酸甜"})
    with TestClient(make_app(tmp_path, [changed, rows[1]], ConflictLLM())) as client:
        result = ask(client, answer, sid)
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert "失效" in result["reason"]
    assert result["conversation_state"]["meal_constraints"]["preferences"] == ["酸甜", "现在不要酸"]
    assert result["conversation_state"]["flavor_retractions"] == []


def test_confirmation_bypasses_unavailable_parser_pause_and_ledger_replay(tmp_path):
    class UnavailableAfterQuestion(ConflictLLM):
        async def parse(self, message, state, profile):
            if state.pending_flavor_resolution:
                raise AssertionError("A literal confirmation must not call model parse")
            return await super().parse(message, state, profile)

    rows = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, rows, UnavailableAfterQuestion())) as client:
        sid, question = conflict(client)
        paused = ask(client, "暂不规划", sid)
        assert not paused["conversation_state"]["pending_plan"]
        assert paused["conversation_state"]["pending_flavor_resolution"]
        answer = option(question, "酸甜")
        selected = ask(client, answer, sid, request_id="once")
        assert selected == ask(client, answer, sid, request_id="once")
    assert (
        selected["status"] == "ok"
        and len(selected["conversation_state"]["flavor_retractions"]) == 1
    )


@pytest.mark.parametrize(
    "transform",
    [
        lambda x: x + "？",
        lambda x: "如果" + x,
        lambda x: "“" + x + "”",
        lambda x: x + "，再取消不辣",
    ],
)
def test_question_quoted_hypothetical_or_mixed_choice_never_removes(tmp_path, transform):
    rows = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, rows, ConflictLLM())) as client:
        sid, question = conflict(client)
        result = ask(client, transform(option(question, "酸甜")), sid)
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["conversation_state"]["meal_constraints"]["preferences"][:2] == [
        "酸甜",
        "现在不要酸",
    ]
    assert result["conversation_state"]["flavor_retractions"] == []


def test_original_named_local_replacement_scope_preserved(tmp_path):
    veg = recipe("糖醋白菜")
    old = recipe("糖醋鸡肉", foods="鸡肉200克；盐1克", steps="鸡肉蒸熟装盘。")
    replacement = recipe("清蒸鸡肉", foods="鸡肉200克；盐1克", steps="鸡肉蒸熟装盘。")

    class LocalLLM(ConflictLLM):
        async def parse(self, message, state, profile):
            if not state.menu_ids:
                return Intent(
                    action="plan",
                    people=1,
                    meal_type="晚餐",
                    dish_count=2,
                    soup_count=0,
                    restrictions_confirmed=True,
                    preferences=["酸甜"],
                )
            return Intent(action="replace", replace_name=old.name)

    with TestClient(make_app(tmp_path, [veg, old], LocalLLM())) as client:
        initial = ask(client, "1人晚餐2道菜，酸甜，无其他忌口。")
        sid = initial["conversation_state"]["session_id"]
        names = [r["name"] for r in initial["menu"]]
        slot = names.index(old.name) + 1
    with TestClient(make_app(tmp_path, [veg, old, replacement], LocalLLM())) as client:
        question = ask(client, "只换糖醋鸡肉，现在不要酸。", sid)
        assert question["conversation_state"]["pending_flavor_resolution"]["replace_slot"] == slot
        selected = ask(client, option(question, "现在不要酸"), sid)
    assert selected["status"] == "ok"
    assert selected["menu"][slot - 1]["name"] == replacement.name
    assert (
        selected["menu"][1 - (slot - 1)]["recipe_id"]
        == initial["menu"][1 - (slot - 1)]["recipe_id"]
    )


def test_retraction_fact_mandatory_and_old_state_defaults():
    assert "flavor_retraction" in required_fact_ids(
        Intent(), {"flavor_retraction": "已明确撤回这一段。"}
    )
    state = SessionState.model_validate({"session_id": "f" * 32, "user_id": 900001})
    assert state.pending_flavor_resolution is None and state.flavor_retractions == []
    assert choice("撤回本餐第1条口味第1段：酸甜", None) is None


def test_profile_retraction_session_only_and_new_session_reuses_original_profile(tmp_path):
    rows = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, rows)) as client:
        profile = client.app.state.agent.catalog.profiles[900001]
        profile.preferences = ["喜欢酸甜、喜欢蒜香"]
        original = profile.model_dump(mode="json")
        first = ask(client, "1人晚餐1道菜，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
        question = ask(client, "现在不要酸。", sid)
        selected = ask(client, option(question, "喜欢酸甜"), sid)
        assert profile.model_dump(mode="json") == original
        new = ask(client, "1人晚餐1道菜，无其他忌口。")
    assert selected["status"] == "ok"
    owner = next(d for d in selected["conversation_state"]["diners"] if d["profile_owner"])
    assert owner["preferences"] == ["喜欢蒜香"]
    assert new["conversation_state"]["diners"][0]["preferences"] == ["喜欢酸甜、喜欢蒜香"]


@pytest.mark.parametrize("stream", [False, True])
def test_openai_reply_preserves_mandatory_retraction_and_safety_statement(tmp_path, stream):
    import json

    rows = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, rows, ConflictLLM())) as client:
        sid, question = conflict(client)
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": stream,
                "session_id": sid,
                "messages": [{"role": "user", "content": option(question, "酸甜")}],
            },
        )
    assert response.status_code == 200
    if stream:
        chunks = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    else:
        text = response.json()["choices"][0]["message"]["content"]
    assert "仅在本会话撤回" in text and "原档案未修改" in text and "安全限制" in text


async def test_provider_parse_context_only_pending_flag_not_source_options():
    import json

    import httpx

    from app.domain.models import FlavorResolution, FlavorRetractionOption, UserProfile
    from app.infrastructure.llm.deepseek import DeepSeekLLM
    from tests.test_llm import completion

    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    state = SessionState(session_id="f" * 32, user_id=900001)
    selected = FlavorRetractionOption(
        display="撤回本餐第1条口味第1段：酸甜",
        preference_index=0,
        before="local-before",
        after="local-after",
        removed_clause="local-part",
    )
    state.pending_flavor_resolution = FlavorResolution(
        binding_hash="local-hash",
        action="replace",
        replace_slot=2,
        original_menu_ids=["local-menu"],
        conflicts=["酸甜"],
        options=[selected],
        prompt="local-prompt",
    )
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(
            200, json=completion({"action": "clarify", "clarification": "请确认。"})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        await adapter.parse("只是问题", state, profile)
    payload = json.loads(observed[0].content)
    context = json.loads(payload["messages"][1]["content"])
    assert context["pending_flavor_resolution"] is True
    assert selected.display not in str(context)
    for marker in (
        "local-before",
        "local-after",
        "local-part",
        "local-hash",
        "local-menu",
        "local-prompt",
    ):
        assert marker not in str(payload)
