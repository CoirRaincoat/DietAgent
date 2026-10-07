"""Actual source waiting stages belong in replies, never guessed total time."""

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.response_copy import required_fact_ids, response_facts
from app.domain.advance_preparation import advance_preparation_copy
from app.domain.models import Constraints, Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog
from tests.test_component_slots import catalog as source_catalog
from tests.test_component_slots import dish


def source(name):
    return next(r for r in source_catalog().values() if r.name == name)


@pytest.mark.parametrize("name,witnesses", [
    ("白切肘子", ("放入冰箱冷藏2小时以上",)),
    ("玉米面包", ("静置10分钟", "静置2小时，发酵至原来体积的两倍大", "静置1小时，让面饼充分发酵")),
    ("蒸米饭", ("浸泡30分钟",)),
])
def test_actual_waiting_stages_are_mandatory_source_facts_without_changing_recipe(name, witnesses):
    recipe = source(name)
    before = recipe.model_dump(mode="json")
    facts = response_facts(intent=Intent(), constraints=Constraints(), diners=[], previous=[],
        chosen=[recipe], balance=analyze_menu_balance([recipe]))
    assert "advance_preparation" in required_fact_ids(Intent(), facts)
    assert all(value in facts["advance_preparation"] for value in witnesses)
    assert recipe.model_dump(mode="json") == before
    assert "总备餐耗时" in facts["advance_preparation"]
    assert "保存条件" in facts["advance_preparation"]
    assert not any(value in facts["advance_preparation"] for value in ("总共3小时", "保证安全", "保存一天"))


@pytest.mark.parametrize("steps,witness", [
    ("面团静置2小时，发酵至两倍大。", "静置2小时，发酵至两倍大"),
    ("将面团松弛半小时，再成形。", "松弛半小时"),
    ("浸泡30分钟，沥干。", "浸泡30分钟"),
    ("放入冰箱冷藏2小时以上，切片。", "冷藏2小时以上"),
    ("放入冰箱冷藏，2小时后取出切片。", "冷藏，2小时后取出切片"),
    ("不加蒜，面团发酵3小时。", "面团发酵3小时"),
    ("面团发酵，持续2小时。", "面团发酵，持续2小时"),
    ("冷藏2小时后脱模，可以保存三天。", "冷藏2小时后脱模"),
])
def test_adjacent_source_action_and_duration_are_quoted_without_summing_or_advising_storage(steps, witness):
    text = advance_preparation_copy([dish("公开自编配菜", "豆腐200克；水30克", steps)])
    assert text and witness in text
    assert "保存三天" not in text


@pytest.mark.parametrize("steps", [
    "豆腐蒸熟装盘。", "冷藏保存3天。", "可保存3天，冷藏2小时。",
    "不要冷藏2小时。", "无需静置2小时。", "不需要松弛半小时。",
    "例如，放入冰箱冷藏2小时。", "如果已经准备好，面团发酵3小时。",
    "可以放在室温，发酵3小时。", "参考‘冷藏2小时’；豆腐蒸熟装盘。",
    "‘静置2小时，发酵至两倍大’；豆腐蒸熟装盘。",
    "面团发酵至两倍大，烘烤20分钟。", "静置，蒸制30分钟。",
])
def test_optional_negated_storage_quoted_or_other_cooking_time_not_mandatory_preparation(steps):
    assert advance_preparation_copy([dish("公开自编配菜", "豆腐200克；水30克", steps)]) is None


@pytest.mark.parametrize("name,witness", [
    ("白切肘子", "冷藏2小时以上"),
    ("玉米面包", "静置2小时，发酵至原来体积的两倍大"),
    ("蒸米饭", "浸泡30分钟"),
])
def test_actual_source_notice_survives_native_reply_details_explain_retry_and_cross_user(
    tmp_path, catalog, name, witness,
):
    class OpeningOnly(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    recipe = source(name)
    catalog.profiles[3].health_goals = []
    other_names = {"蒸鸡肉", "蒸南瓜"} if "staple" in recipe.categories else {"米饭", "蒸南瓜"}
    others = [r for r in catalog.recipes.values() if r.name in other_names]
    catalog.recipes = {r.recipe_id: r for r in [recipe, *others]}
    llm = OpeningOnly([complete_intent(dish_count=3, soup_count=0), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        body = dict(user_id=3, message="1人晚餐，3道菜，0汤，没有其他忌口。", request_id="prep-source")
        first = client.post("/chat", json=body).json()
        assert first["status"] == "ok" and len(first["menu"]) == 3
        assert witness in first["reason"]
        item = next(item for item in first["menu"] if item["name"] == name)
        assert item["name"] == name and item["steps"] == recipe.steps
        assert witness in "".join(item["reasons"])
        sid = first["conversation_state"]["session_id"]
        assert client.post("/chat", json={**body, "session_id": sid}).json() == first
        assert client.post("/chat", json={**body, "session_id": sid, "user_id": 4}).status_code == 409
        second = client.post("/chat", json=dict(user_id=3, session_id=sid, message="解释，不换菜。")).json()
        assert second["menu"] == first["menu"] and witness in second["reason"]
