"""Cross-source behavior: concise copy never drops authoritative state facts."""

import pytest
from test_agent_api import ScriptedLLM, client_for, complete_intent
from test_agent_api import catalog as catalog

from app.agent.menu_restore import (
    apply_menu_restore,
    record_menu_revision,
    record_rejection_action,
)
from app.domain.models import (
    Constraints,
    DinerUpdate,
    Intent,
    RestoreConstraint,
    RestoreMenuIntent,
    SessionState,
)
from app.rules.engine import RuleEngine


class MinimalExplanationLLM(ScriptedLLM):
    async def explain(self, facts):
        return ["balance"]


def send(client, message, session=None):
    body = {"user_id": 3, "message": message}
    if session:
        body["session_id"] = session
    return client.post("/chat", json=body).json()


def test_missing_context_has_concise_copy_and_no_extra_request(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM([Intent()])) as client:
        result = send(client, "帮我安排一餐")
    assert result["status"] == "clarification_required"
    assert result["reason"].startswith("为了把这餐安排准确")
    assert [q["field"] for q in result["clarification_questions"]] == [
        "people", "meal_type", "restrictions",
    ]


def test_concise_diner_copy_keeps_unlinked_profile_and_nutrition(tmp_path, catalog):
    intent = complete_intent(people=2, diner_updates=[
        DinerUpdate(diner="爸爸", attendance=True, allergies=["鱼"]),
        DinerUpdate(diner="妈妈", attendance=True, no_spicy=True),
    ])
    with client_for(tmp_path, catalog, MinimalExplanationLLM([intent])) as client:
        result = send(client, "爸爸和妈妈两人吃晚餐，爸爸对鱼过敏，妈妈不吃辣")
    assert result["status"] == "ok"
    assert "身份待关联" in result["reason"]
    assert "多人要求方面" in result["reason"]
    assert "爸爸需要避开鱼" in result["reason"]
    assert "用户需要" not in result["reason"]
    assert "定性分析" in result["reason"]
    assert "未计算热量" in result["reason"]
    assert "方太菜谱库" in result["reason"]


def test_exact_restore_keeps_original_ids_and_verified_restore_copy(tmp_path, catalog):
    llm = MinimalExplanationLLM([
        complete_intent(dish_count=3), Intent(action="reject"),
        Intent(restore_menu=RestoreMenuIntent(reference="original")),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = send(client, "1人晚餐，3道菜，没有忌口")
        sid = first["conversation_state"]["session_id"]
        second = send(client, "整桌换掉", sid)
        restored = send(client, "恢复最初那桌菜单", sid)
    assert first["status"] == second["status"] == restored["status"] == "ok"
    assert [i["recipe_id"] for i in restored["menu"]] == [i["recipe_id"] for i in first["menu"]]
    assert "恢复此前那桌菜单" in restored["reason"]
    assert "仅撤销" in restored["reason"]
    assert "已加入你刚补充的要求" not in restored["reason"]
    assert not any(e["name"] == "menu_modify" for e in restored["tool_calls"])


@pytest.mark.parametrize("confirmed,expected", [
    (True, "已确认取消普通忌口「鸡蛋」"),
    (False, "已取消本次撤销请求"),
])
def test_revocation_copy_reflects_actual_confirm_or_cancel(tmp_path, catalog, confirmed, expected):
    final = Intent(revoke_confirmed=confirmed, revoke_cancelled=not confirmed)
    llm = MinimalExplanationLLM([
        complete_intent(excluded_ingredients=["鸡蛋"]),
        Intent(revoke_exclusions=["鸡蛋"]), final,
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = send(client, "1人晚餐，不吃鸡蛋")
        sid = first["conversation_state"]["session_id"]
        pending = send(client, "取消鸡蛋忌口", sid)
        result = send(client, "确认" if confirmed else "算了", sid)
    assert pending["status"] == "clarification_required"
    assert "已确认取消" not in pending["reason"]
    assert result["status"] == "ok"
    assert expected in result["reason"]
    assert ("鸡蛋" in result["conversation_state"]["constraints"]["excluded_ingredients"]) != confirmed


def test_unmatched_revocation_never_claims_removal(tmp_path, catalog):
    llm = MinimalExplanationLLM([
        complete_intent(excluded_ingredients=["香菜"]),
        Intent(revoke_exclusions=["鸡蛋"]), Intent(revoke_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = send(client, "1人晚餐，不吃香菜")
        sid = first["conversation_state"]["session_id"]
        send(client, "取消鸡蛋忌口", sid)
        result = send(client, "确认", sid)
    assert result["status"] == "ok"
    assert result["conversation_state"]["constraints"]["excluded_ingredients"] == ["香菜"]
    assert "未删除任何普通忌口" in result["reason"]


def test_constraint_restore_reports_actual_count_and_keeps_exclusions(tmp_path, catalog):
    llm = MinimalExplanationLLM([
        complete_intent(dish_count=3), Intent(dish_count=4, excluded_ingredients=["鱼"]),
        Intent(restore_constraints=[RestoreConstraint(field="dish_count", reference="original")]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = send(client, "1人晚餐，3道菜，没有忌口")
        sid = first["conversation_state"]["session_id"]
        send(client, "改成4道菜，不吃鱼", sid)
        result = send(client, "恢复最初菜数", sid)
    assert result["status"] == "ok"
    assert result["conversation_state"]["constraints"]["dish_count"] == 3
    assert result["conversation_state"]["constraints"]["excluded_ingredients"] == ["鱼"]
    assert "总菜数恢复为 3 道" in result["reason"]


def test_failed_restore_preserves_all_rejection_actions(catalog):
    duplicate = catalog.recipes["test_0"].model_copy(update={"recipe_id": "same-name"})
    recipes = {**catalog.recipes, duplicate.recipe_id: duplicate}
    state = SessionState(session_id="synthetic", user_id=3, constraints=Constraints())
    state.revision = 1
    original = ["test_0", "test_3", "test_6"]
    record_menu_revision(state, original)
    state.rejected_recipe_ids = ["same-name"]
    record_rejection_action(state, original)
    state.rejected_recipe_ids += original
    before = state.model_dump()
    issue = apply_menu_restore(
        state, Intent(restore_menu=RestoreMenuIntent(reference="original")), RuleEngine(), recipes,
    )
    assert issue is not None
    assert state.model_dump() == before


def test_duplicate_history_is_blocked_without_state_mutation(catalog):
    state = SessionState(session_id="synthetic", user_id=3, constraints=Constraints())
    record_menu_revision(state, ["test_0", "test_0", "test_6"])
    before = state.model_dump()
    issue = apply_menu_restore(
        state, Intent(restore_menu=RestoreMenuIntent(reference="original")),
        RuleEngine(), catalog.recipes,
    )
    assert issue is not None
    assert state.model_dump() == before
