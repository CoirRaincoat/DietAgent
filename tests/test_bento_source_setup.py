"""Source setup can break a bento tie, never waive a requested food or edit scope."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.scene_preferences import repair_scene_preferences
from app.api.main import create_app
from app.domain.models import Constraints
from app.domain.scene_references import scene_reference
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog


def repair(menu, candidates, constraints, *, slot=None, allow=True, terms=(), scores=None):
    rules = RuleEngine()
    safe = [recipe for recipe in candidates if rules.evaluate(recipe, constraints).allowed]
    return repair_scene_preferences(menu, safe, constraints,
        goal_scores=scores if scores is not None else {r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [*menu, *safe]},
        order={recipe.recipe_id: index for index, recipe in enumerate(safe)},
        food_matches=lambda recipe, term: bool(rules.preference_matches(recipe, term)),
        protected_food_terms=terms, replace_slot=slot, allow_repair=allow)


def test_actual_chestnut_chicken_has_full_source_setup_reference_not_population_inference():
    recipe = catalog()[566]
    original = recipe.model_dump(mode="json")
    reference = scene_reference(recipe)
    assert reference is not None
    assert reference.setup_reference == "main_pot_program"
    assert "便当" in reference.scenes
    assert "盖上锅盖和量杯" in recipe.steps and "烹饪完成，取出食物趁热食用" in recipe.steps
    assert "烤肠模具" not in recipe.steps and "裱花袋" not in recipe.steps
    assert original == recipe.model_dump(mode="json")
    assert scene_reference(recipe.model_copy(update={"steps": recipe.steps + "必须用模具。"})) is None


def test_equal_bento_reference_can_replace_mould_chicken_without_losing_chicken_or_other_slots():
    menu = [catalog()[786], catalog()[469], catalog()[299]]
    constraints = Constraints(people=1, meal_type="午餐", dish_count=3, soup_count=0,
        excluded_ingredients=["葱"], no_spicy=True, preferences=["场景：便当", "场景：一人食"])
    actual = repair(menu, [catalog()[566]], constraints, terms=("鸡肉", "主食", "蔬菜"))
    assert actual.recipes == [menu[0], catalog()[566], menu[2]]
    assert actual.changed_positions == {1}
    assert repair(actual.recipes, [catalog()[469]], constraints, terms=("鸡肉",)).recipes == actual.recipes


@pytest.mark.parametrize("slot,allow,preferences", [(1, True, ["便当"]), (None, False, ["便当"]),
    (None, True, []), (None, True, ["家庭聚餐"]), (None, True, ["便当", "不要便当"])])
def test_setup_reference_does_not_churn_unrelated_slot_readonly_or_other_scene(slot, allow, preferences):
    menu = [catalog()[786], catalog()[469], catalog()[299]]
    result = repair(menu, [catalog()[566]], Constraints(meal_type="午餐", dish_count=3, preferences=preferences),
        slot=slot, allow=allow)
    assert result.recipes == menu


@pytest.mark.parametrize("constraints", [Constraints(allergies=["鸡肉"]), Constraints(excluded_ingredients=["蒜"]),
    Constraints(inventory=["鸡胸肉", "玉米", "胡萝卜", "淀粉", "水"]), Constraints(diet_mode="vegan")])
def test_main_pot_reference_never_waives_known_hard_gate(constraints):
    assert not RuleEngine().evaluate(catalog()[566], constraints).allowed


def test_setup_change_does_not_override_an_explicitly_preferred_corn_source():
    constraints = Constraints(meal_type="午餐", dish_count=1, preferences=["便当"], preferred_ingredients=["玉米"])
    assert repair([catalog()[469]], [catalog()[566]], constraints, terms=("鸡肉",)).recipes == [catalog()[469]]


def test_setup_change_does_not_erase_an_explicit_source_method():
    constraints = Constraints(meal_type="午餐", dish_count=1, preferences=["便当", "做法：蒸制"])
    assert repair([catalog()[469]], [catalog()[566]], constraints).recipes == [catalog()[469]]


def test_setup_reference_never_overrides_a_configured_goal_regression():
    old, new = catalog()[469], catalog()[566]
    constraints = Constraints(meal_type="午餐", dish_count=1, preferences=["便当"], health_goals=["降压"])
    assert repair([old], [new], constraints, scores={old.recipe_id: (3,), new.recipe_id: (2,)}).recipes == [old]


def test_optional_unlisted_vegetable_is_not_a_vegetable_dish_preference():
    rules = RuleEngine()
    assert not rules.preference_matches(catalog()[66], "蔬菜")
    assert rules.preference_matches(catalog()[786], "蔬菜")


def test_scene_repair_does_not_protect_optional_vegetable_words_over_real_vegetable_slot():
    menu = [catalog()[786], catalog()[66], catalog()[299]]
    constraints = Constraints(people=1, meal_type="午餐", dish_count=3, soup_count=0,
        excluded_ingredients=["葱"], no_spicy=True, preferences=["场景：便当", "场景：一人食"])
    actual = repair(menu, [catalog()[566]], constraints, terms=("鸡肉", "主食", "蔬菜"))
    assert actual.recipes == [menu[0], catalog()[566], menu[2]]


def test_default_office_lunch_stream_uses_intact_chicken_vegetable_staple_and_reports_equipment(tmp_path):
    # Frozen public parse from the already-authorized run. No network, key,
    # recipe-generation call or hand-built candidate set reaches this API.
    question = ("一个人的工作日午餐，要带去办公室吃，安排3道，不要喝的汤。想要1道主食、"
                "1道鸡肉菜和1道蔬菜，不辣、不放葱。可以提前准备，但需要准备的地方请如实说明；"
                "没有其他忌口，不赶时间。")
    intent = dict(action="plan", meal_type="午餐", people=1, dish_count=3, soup_count=0,
        no_spicy=True, excluded_ingredients=["葱"],
        preferences=["场景：一人食", "场景：便当", "做法：可提前准备"],
        restrictions_confirmed=True, clear_time_limit=True, query_terms=["主食", "鸡肉", "蔬菜"])
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert payload["message"] == question and not calls
        calls.append(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(intent, ensure_ascii=False)}}]})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    llm = DeepSeekLLM("offline-no-real-key", client=upstream)
    app = create_app(settings=Settings(_env_file=None, deepseek_api_key="", local_profile_path=None,
        allow_recipe_generation=False, session_db=tmp_path / "state.db"), llm=llm)
    with TestClient(app) as client:
        body = dict(model="fangtai-meal-agent", user="900001", stream=True,
            request_id="bento-source-setup", messages=[dict(role="user", content=question)])
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200 and "data: [DONE]" in response.text
        session = response.headers["X-Session-ID"]
        state = app.state.agent.store.get(session, 900001)
        recipes = app.state.agent._recipes(state)
        menu = [recipes[uid] for uid in state.menu_ids]
        assert menu == [catalog()[786], catalog()[566], catalog()[37]]
        assert state.constraints.people == 1 and state.constraints.dish_count == 3
        assert state.constraints.soup_count == 0 and state.constraints.no_spicy
        assert state.constraints.excluded_ingredients == ["葱"]
        assert state.constraints.max_minutes is None
        assert all(RuleEngine().evaluate(r, state.constraints).allowed for r in menu)
        final = "".join(json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                        for line in response.text.splitlines()
                        if line.startswith("data: ") and line != "data: [DONE]")
        assert "仍需原设备" in final and "未核通用锅具替代" in final
        assert "红烧栗子鸡" in final
        retry = client.post("/v1/chat/completions", json={**body, "session_id": session})
        assert retry.status_code == 200 and len(calls) == 1
        assert app.state.agent.store.get(session, 900001).model_dump() == state.model_dump()
        other = client.post("/chat", json=dict(user_id=900002, session_id=session,
            request_id=body["request_id"], message=question))
        assert other.status_code == 409 and len(calls) == 1
    asyncio.run(upstream.aclose())
