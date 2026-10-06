"""Public source-reference tradeoffs through the actual persisted service."""

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Intent
from tests.test_explicit_method_protection import dish
from tests.test_flavor_matching_integration import FlavorLLM, make_app


class TradeoffLLM(FlavorLLM):
    async def parse(self, message, state, profile):
        if message.startswith("优先"):
            return Intent(action="clarify", clarification="请确认优先项。")
        if state.menu_ids or state.pending_plan:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
            preferences=["蒸"],
        )


def pair():
    return (dish("早餐蒸鸡肉", "鸡肉蒸熟装盘。", "早餐"), dish("晚餐煮鸡肉", "鸡肉煮熟装盘。"))


def ask(client, message, session=None, **extra):
    payload = {"user_id": 900001, "message": message, **extra}
    if session:
        payload["session_id"] = session
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def test_tradeoff_asks_before_claiming_success_or_selecting_a_priority(tmp_path):
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        result = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["clarification_questions"][0]["field"] == "method_meal_priority"
    assert result["clarification_questions"][0]["options"] == [
        "优先餐次参考",
        "优先明确做法参考",
        "暂不规划",
    ]
    assert result["conversation_state"]["constraints"].get("method_meal_priority") is None
    assert "有界" in result["reason"] and "不是" in result["reason"]


@pytest.mark.parametrize(
    "answer,expected,priority",
    [
        ("优先餐次参考", "晚餐煮鸡肉", "meal"),
        ("优先明确做法参考", "早餐蒸鸡肉", "method"),
    ],
)
def test_literal_selection_resumes_and_persists_without_deleting_preferences(
    tmp_path,
    answer,
    expected,
    priority,
):
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        sid = first["conversation_state"]["session_id"]
        selected = ask(client, answer, sid)
    assert selected["status"] == "ok" and [r["name"] for r in selected["menu"]] == [expected]
    assert selected["conversation_state"]["constraints"]["method_meal_priority"] == priority
    assert "蒸" in selected["conversation_state"]["constraints"]["preferences"]
    assert "优先" in selected["reason"] and "不是" in selected["reason"]
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        continued = ask(client, "继续", sid)
    assert continued["status"] == "ok"
    assert [r["recipe_id"] for r in continued["menu"]] == [r["recipe_id"] for r in selected["menu"]]


@pytest.mark.parametrize("answer", ["继续", "随便", "如果优先餐次参考", "优先餐次参考？"])
def test_ambiguous_quoted_or_question_reply_does_not_select(tmp_path, answer):
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        result = ask(client, answer, first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["conversation_state"]["constraints"].get("method_meal_priority") is None


def test_joint_source_candidate_does_not_create_a_false_tradeoff(tmp_path):
    joint = dish("晚餐蒸鸡肉", "鸡肉蒸熟装盘。")
    with TestClient(make_app(tmp_path, (*pair(), joint), TradeoffLLM())) as client:
        result = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
    assert result["status"] == "ok" and [r["name"] for r in result["menu"]] == [joint.name]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("优先餐次参考。", "meal"),
        ("优先明确做法参考", "method"),
        ("暂不规划", "pause"),
        ("第二个", None),
        ("随便", None),
        ("继续", None),
        ("‘优先餐次参考’", None),
        ("如果优先餐次参考", None),
        ("不要优先餐次参考", None),
        ("优先餐次参考？", None),
        ("优先餐次参考和优先明确做法参考", None),
    ],
)
def test_priority_answers_are_bounded_literals_not_mentions(message, expected):
    from app.agent.method_meal_tradeoff import literal_priority_choice

    assert literal_priority_choice(message) == expected


@pytest.mark.parametrize(
    "restriction,foods",
    [
        ({"no_spicy": True}, "鸡肉200克；辣椒2克；盐1克"),
        ({"allergies": ["花生"]}, "鸡肉200克；花生油2克；盐1克"),
        ({"diet_mode": "vegan"}, "鸡肉200克；盐1克"),
    ],
)
def test_unsafe_or_disallowed_recipe_is_not_a_tradeoff_option(restriction, foods):
    from app.agent.method_meal_tradeoff import find_tradeoff
    from app.domain.models import Constraints
    from app.rules.engine import RuleEngine

    old = dish("晚餐煮白菜", "白菜煮熟装盘。", foods="白菜200克；盐1克")
    bad = dish("早餐蒸白菜", "白菜和配料蒸熟装盘。", "早餐", foods=foods).model_copy(
        update={"categories": ["vegetable"]}
    )
    assert (
        find_tradeoff(
            [old], [bad], Constraints(dish_count=1, preferences=["蒸"], **restriction), RuleEngine()
        )
        is None
    )


def test_source_component_cannot_be_offered_through_stale_role_metadata():
    from app.agent.method_meal_tradeoff import find_tradeoff
    from app.domain.models import Constraints
    from app.rules.engine import RuleEngine

    old = pair()[1]
    sauce = dish("蒸鸡肉酱", "鸡肉蒸熟后打成酱即可使用。", "早餐").model_copy(
        update={"categories": ["protein"], "eligible": True}
    )
    assert (
        find_tradeoff([old], [sauce], Constraints(dish_count=1, preferences=["蒸"]), RuleEngine())
        is None
    )


def test_pause_does_not_select_or_discard_the_question(tmp_path):
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        result = ask(client, "暂不规划", first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert "暂不规划" in result["reason"] and not result["conversation_state"]["pending_plan"]
    assert result["conversation_state"]["pending_method_tradeoff"] is not None
    assert result["conversation_state"]["constraints"]["method_meal_priority"] is None


def test_source_change_with_same_id_invalidates_old_question_after_restart(tmp_path):
    rows = pair()
    with TestClient(make_app(tmp_path, rows, TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
    changed = rows[0].model_copy(update={"steps": "鸡肉煮熟装盘。"})
    with TestClient(make_app(tmp_path, (changed, rows[1]), TradeoffLLM())) as client:
        result = ask(client, "优先明确做法参考", first["conversation_state"]["session_id"])
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert "失效" in result["reason"]
    assert result["conversation_state"]["constraints"]["method_meal_priority"] is None


def test_model_invented_choice_cannot_set_policy_and_known_allergy_survives(tmp_path):
    class InventedChoiceLLM(TradeoffLLM):
        async def parse(self, message, state, profile):
            if state.pending_plan:
                return Intent(action="plan", method_meal_priority="meal", allergies=["花生"])
            return await super().parse(message, state, profile)

    with TestClient(make_app(tmp_path, pair(), InventedChoiceLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        result = ask(
            client, "另有花生过敏，但尚未选优先项。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert result["conversation_state"]["constraints"]["method_meal_priority"] is None
    assert "花生" in result["conversation_state"]["constraints"]["allergies"]


def test_replayed_confirmation_is_identical_and_not_reparsed(tmp_path):
    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        sid = first["conversation_state"]["session_id"]
        result = ask(client, "优先餐次参考", sid, request_id="method-priority-confirm-1")
        replay = ask(client, "优先餐次参考", sid, request_id="method-priority-confirm-1")
    assert result == replay and result["status"] == "ok"


@pytest.mark.parametrize(
    "answer,expected", [("优先餐次参考", "晚餐炖鸡肉"), ("优先明确做法参考", "早餐蒸鸡肉")]
)
def test_confirmation_resumes_original_local_slot_only(tmp_path, answer, expected):
    veg = dish("晚餐煮白菜", "白菜煮熟装盘。", foods="白菜200克；盐1克")
    old = dish("晚餐煮鸡肉", "鸡肉煮熟装盘。")
    dinner = dish("晚餐炖鸡肉", "鸡肉炖熟装盘。")
    steam = pair()[0]

    class LocalLLM(FlavorLLM):
        async def parse(self, message, state, profile):
            if message.startswith("优先"):
                return Intent(action="clarify", clarification="请确认原范围。")
            if state.menu_ids:
                target = state.menu_ids.index(old.recipe_id) + 1
                return Intent(action="replace", replace_slot=target, preferences=["蒸"])
            return Intent(
                action="plan",
                people=1,
                meal_type="晚餐",
                dish_count=2,
                soup_count=0,
                restrictions_confirmed=True,
                query_terms=[veg.name, old.name],
            )

    # Establish the accepted menu in a controlled pool. Default variety may
    # choose 炖 over 煮 otherwise; that is not this local-scope test's premise.
    with TestClient(make_app(tmp_path, (veg, old), LocalLLM())) as client:
        initial = ask(client, "1人晚餐2道菜，无其他忌口。")
        ids = [r["recipe_id"] for r in initial["menu"]]
        assert old.recipe_id in ids and veg.recipe_id in ids
        slot = ids.index(old.recipe_id) + 1
        sid = initial["conversation_state"]["session_id"]
    with TestClient(make_app(tmp_path, (veg, old, dinner, steam), LocalLLM())) as client:
        question = ask(client, f"只换第{slot}道，想吃蒸菜。", sid)
        assert question["status"] == "clarification_required"
        assert question["conversation_state"]["pending_method_tradeoff"]["replace_slot"] == slot
        selected = ask(client, answer, sid)
    assert selected["status"] == "ok"
    assert selected["menu"][slot - 1]["name"] == expected
    assert selected["menu"][1 - (slot - 1)]["recipe_id"] == veg.recipe_id


def test_joint_candidate_is_not_ignored_while_resolving_a_local_tradeoff():
    from app.agent.method_meal_tradeoff import find_tradeoff
    from app.domain.models import Constraints
    from app.rules.engine import RuleEngine

    joint = dish("晚餐蒸鸡肉", "鸡肉蒸熟装盘。")
    assert (
        find_tradeoff(
            [pair()[1]],
            [*pair(), joint],
            Constraints(dish_count=1, preferences=["蒸"]),
            RuleEngine(),
        )
        is None
    )


def test_old_authorized_priority_expires_when_known_requirements_change(tmp_path):
    class ChangedRequirementsLLM(TradeoffLLM):
        async def parse(self, message, state, profile):
            if "花生过敏" in message:
                return Intent(action="plan", allergies=["花生"])
            return await super().parse(message, state, profile)

    with TestClient(make_app(tmp_path, pair(), ChangedRequirementsLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        sid = first["conversation_state"]["session_id"]
        selected = ask(client, "优先餐次参考", sid)
        assert selected["status"] == "ok"
        changed = ask(client, "另外花生过敏，重新核对。", sid)
    assert changed["status"] == "clarification_required"
    assert changed["conversation_state"]["constraints"]["method_meal_priority"] is None
    assert "花生" in changed["conversation_state"]["constraints"]["allergies"]


def test_question_does_not_erase_existing_hard_constraints(tmp_path):
    class SafeLLM(TradeoffLLM):
        async def parse(self, message, state, profile):
            intent = await super().parse(message, state, profile)
            if not state.pending_plan and not state.menu_ids:
                return intent.model_copy(update={"no_spicy": True, "allergies": ["花生"]})
            return intent

    spicy = dish("晚餐蒸鸡肉", "鸡肉和辣椒蒸熟装盘。", foods="鸡肉200克；辣椒2克；盐1克")
    with TestClient(make_app(tmp_path, (*pair(), spicy), SafeLLM())) as client:
        first = ask(client, "1人晚餐1道菜，不辣，花生过敏，想吃蒸菜。")
        assert first["status"] == "clarification_required"
        ids = first["conversation_state"]["pending_method_tradeoff"]["method_option_ids"]
        assert spicy.recipe_id not in ids
        result = ask(client, "优先明确做法参考", first["conversation_state"]["session_id"])
    assert result["status"] == "ok" and result["menu"][0]["name"] == pair()[0].name
    assert result["conversation_state"]["constraints"]["no_spicy"] is True
    assert "花生" in result["conversation_state"]["constraints"]["allergies"]


@pytest.mark.parametrize("stream", [False, True])
def test_openai_confirmation_keeps_mandatory_priority_and_gap_explanation(tmp_path, stream):
    import json

    with TestClient(make_app(tmp_path, pair(), TradeoffLLM())) as client:
        first = ask(client, "1人晚餐1道菜，无其他忌口，想吃蒸菜。")
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": stream,
                "session_id": first["conversation_state"]["session_id"],
                "messages": [{"role": "user", "content": "优先餐次参考"}],
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
    assert "已确认的优先项：餐次参考" in text and "尚缺" in text
    assert "未删除原要求" in text


def test_explicit_diversity_is_not_waived_by_meal_reference_priority():
    from app.agent.meal_context import repair_meal_context
    from app.domain.models import Constraints
    from app.rules.engine import RuleEngine

    first = dish("炒白菜", "白菜炒熟装盘。", foods="白菜200克；盐1克")
    old = dish("早餐蒸菠菜", "菠菜蒸熟装盘。", "早餐", foods="菠菜200克；盐1克")
    new = dish("晚餐炒青菜", "青菜炒熟装盘。", foods="青菜200克；盐1克")
    constraints = Constraints(
        dish_count=2, preferences=["蒸", "做法多样"], method_meal_priority="meal"
    )
    result = repair_meal_context(
        [first, old],
        [new],
        constraints,
        scores={},
        order={},
        food_matches=lambda r, t: bool(RuleEngine().food_matches(r, t)),
        replace_slot=2,
    )
    assert result.recipes == [first, old]
