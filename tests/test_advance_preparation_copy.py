"""Source-backed prep disclosure is not a new exclusion or storage instruction."""
import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.response_copy import required_fact_ids, response_facts
from app.domain.models import Constraints, Intent
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog
from tests.test_component_slots import catalog as source_catalog
from tests.test_component_slots import dish


def facts_for(recipe):
    return response_facts(intent=Intent(), constraints=Constraints(), diners=[],
                          previous=[], chosen=[recipe], balance=analyze_menu_balance([recipe]))


def test_actual_clam_source_discloses_day_ahead_without_excluding_or_rewriting():
    source = source_catalog()[1370]
    original = source.model_dump(mode="json")
    facts = facts_for(source)
    assert "advance_preparation" in facts
    text = facts["advance_preparation"]
    assert "鲜蒸冬瓜" in text and "蛤蜊提前一天用海盐水浸泡吐沙" in text
    assert "advance_preparation" in required_fact_ids(Intent(), facts)
    assert not any(word in text for word in ("冷藏", "安全保证", "24小时", "保存一天"))
    assert RuleEngine().evaluate(source, Constraints(soup_count=0)).allowed
    assert source.model_dump(mode="json") == original


@pytest.mark.parametrize("steps", [
    "豆腐蒸熟装盘。", "无需提前一天浸泡，豆腐蒸熟装盘。",
    "例如提前一天浸泡；豆腐蒸熟装盘。", "参考‘提前一天浸泡’；豆腐蒸熟装盘。",
    "可以提前一晚浸泡；豆腐蒸熟装盘。", "提前一天不用浸泡，豆腐蒸熟装盘。",
    "如果提前一晚浸泡，第二天煮熟。",
])
def test_no_invented_mandatory_prep_from_absent_negated_optional_or_quoted_source(steps):
    assert "advance_preparation" not in facts_for(dish("蒸豆腐", "豆腐200克；水50克", steps))


@pytest.mark.parametrize("steps, witness", [
    ("黄豆提前一晚浸泡，随后煮熟。", "黄豆提前一晚浸泡"),
    ("面团发酵3小时，蒸熟。", "面团发酵3小时"),
])
def test_declared_prep_only_copies_source_time(steps, witness):
    source = dish("豆腐配菜", "豆腐200克；黄豆100克；水50克", steps)
    assert witness in facts_for(source)["advance_preparation"]


def test_native_reply_keeps_source_prep_even_if_adapter_selects_only_opening(tmp_path, catalog):
    class OpeningOnly(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    source = source_catalog()[1370]
    rice = next(r for r in catalog.recipes.values() if r.name == "米饭")
    chicken = next(r for r in catalog.recipes.values() if r.name == "蒸鸡肉")
    catalog.profiles[3].health_goals = []
    catalog.recipes = {r.recipe_id: r for r in (source, rice, chicken)}
    llm = OpeningOnly([complete_intent(dish_count=3, soup_count=0), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，3菜不要汤，没有其他忌口。")).json()
        assert first["status"] == "ok" and len(first["menu"]) == 3
        assert "蛤蜊提前一天用海盐水浸泡吐沙" in first["reason"]
        second = client.post("/chat", json=dict(user_id=3,
            session_id=first["conversation_state"]["session_id"], message="解释，不换菜。")).json()
    assert second["menu"] == first["menu"]
    assert "蛤蜊提前一天用海盐水浸泡吐沙" in second["reason"]
