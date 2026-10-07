"""Source disclosure does not relax known allergy or dietary gates."""
import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.response_copy import required_fact_ids, response_facts
from app.domain.models import Constraints, Intent
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog
from tests.test_component_slots import catalog as source_catalog
from tests.test_component_slots import dish


def facts_for(recipe, constraints=None):
    return response_facts(intent=Intent(), constraints=constraints or Constraints(), diners=[],
        previous=[], chosen=[recipe], balance=analyze_menu_balance([recipe]))


def test_actual_fish_egg_and_brand_warning_is_mandatory_without_changing_source_or_ranking():
    recipe = next(r for r in source_catalog().values() if r.name == "翡翠鳕鱼")
    original = recipe.model_dump(mode="json")
    rules = RuleEngine()
    decision = rules.evaluate(recipe, Constraints(no_spicy=True, allergies=["花生"]))
    assert decision.allowed
    text = facts_for(recipe, Constraints(no_spicy=True, allergies=["花生"]))["ingredient_safety"]
    assert all(word in text for word in ("翡翠鳕鱼", "蛋清", "蛋类", "鸡精", "配料表", "交叉接触"))
    assert "ingredient_safety" in required_fact_ids(Intent(), facts_for(recipe))
    assert any("鸡精" in warning for warning in decision.warnings)
    assert not rules.evaluate(recipe, Constraints(allergies=["鸡蛋"])).allowed
    assert recipe.model_dump(mode="json") == original


@pytest.mark.parametrize("name", ["鸡精", "鸡粉", "鸡汁", "香肠", "火腿", "蒸鱼豉油", "蚝油"])
def test_brand_unknown_gets_named_warning_not_an_invented_allergen_or_new_rejection(name):
    recipe = dish("蒸豆腐", f"豆腐200克；{name}1克", "蒸熟装盘。")
    text = facts_for(recipe)["ingredient_safety"]
    assert name in text and "品牌" in text and "不能仅凭名称" in text
    assert "一定含鸡蛋" not in text and "确认安全" not in text
    assert RuleEngine().evaluate(recipe, Constraints()).allowed


@pytest.mark.parametrize("steps", ["加入鸡精后蒸熟。", "淋入蒸鱼豉油后装盘。"])
def test_step_only_brand_term_is_disclosed(steps):
    recipe = dish("蒸豆腐", "豆腐200克", steps)
    assert "配料表" in facts_for(recipe)["ingredient_safety"]


@pytest.mark.parametrize("steps", ["不加鸡精，蒸熟。", "无需鸡粉，蒸熟。", "豆腐富含蛋白质，蒸熟。"])
def test_absent_or_negated_terms_are_not_new_ingredients(steps):
    recipe = dish("鸡精蛋清风味蒸豆腐", "豆腐200克", steps)
    assert "ingredient_safety" not in facts_for(recipe)


@pytest.mark.parametrize("name", ["植物蛋白", "大豆蛋白", "豌豆蛋白", "乳清蛋白", "胶原蛋白", "蛋白粉"])
def test_non_egg_or_unspecified_protein_is_not_disclosed_as_confirmed_egg(name):
    recipe = dish("蒸豆腐", f"豆腐200克；{name}1克", "蒸熟装盘。")
    assert "ingredient_safety" not in facts_for(recipe)


@pytest.mark.parametrize("name", ["蛋清", "蛋白", "蛋黄", "蛋液"])
def test_explicit_egg_terms_are_never_reduced_to_uncertainty(name):
    recipe = dish("蒸鱼", f"鳕鱼200克；{name}15克", "将鱼蒸熟装盘。")
    assert "蛋类" in facts_for(recipe)["ingredient_safety"]
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["鸡蛋"])).allowed


def test_native_default_reply_and_details_keep_warning_when_model_selects_only_opening(tmp_path, catalog):
    class OpeningOnly(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    recipe = next(r for r in source_catalog().values() if r.name == "翡翠鳕鱼")
    others = [r for r in catalog.recipes.values() if r.name in {"米饭", "蒸南瓜"}]
    catalog.profiles[3].health_goals = []
    catalog.recipes = {r.recipe_id: r for r in [recipe, *others]}
    llm = OpeningOnly([complete_intent(dish_count=3, soup_count=0, allergies=["花生"]),
                      Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，3菜0汤，花生过敏。")).json()
        assert first["status"] == "ok" and len(first["menu"]) == 3
        assert "蛋清" in first["reason"] and "鸡精" in first["reason"]
        item = next(r for r in first["menu"] if r["name"] == recipe.name)
        assert "鸡精" in "".join(item["reasons"])
        assert item["steps"] == recipe.steps
        second = client.post("/chat", json=dict(user_id=3,
            session_id=first["conversation_state"]["session_id"], message="解释，不换菜。")).json()
        assert second["menu"] == first["menu"]
        assert "蛋清" in second["reason"] and "鸡精" in second["reason"]
