"""Actual multi-turn local editing, atomic safety and scoped dislikes."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.menu_edit_scope import ground_menu_edit
from app.api.main import create_app
from app.domain.models import Constraints, Ingredient, Intent, Recipe, SessionState, UserProfile
from app.domain.slot_food_exclusions import local_food_hits
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.deepseek import DeepSeekLLM, _OutputViolation
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine
from tests.test_agent_api import openai_payload
from tests.test_llm import completion
from tests.test_recipe_generation import LocalIntents


def source(name, foods, role):
    return Recipe(recipe_id=name, name=name, raw_ingredients="；".join(foods),
        ingredients=[Ingredient(raw=f, name=f) for f in foods], steps="将原料煮熟装盘。",
        categories=[role], methods=["煮"], meal_types=["晚餐"], source_row=2, fingerprint=name)


def setup(tmp_path, intents, limited=False, allergies=None):
    recipes = [source("葱烧蘑菇", ["蘑菇", "葱", "盐"], "vegetable"),
        source("菌菇蒸鸡", ["鸡肉", "香菇", "盐"], "protein"),
        source("香菇鸡肉焖饭", ["香菇", "鸡肉", "大米", "盐"], "staple"),
        source("清炒白菜", ["白菜", "食用油", "盐"], "vegetable"),
        source("蒸米饭", ["大米", "水"], "staple"),
        source("炒青菜", ["青菜", "食用油", "盐"], "vegetable"),
        source("蒸红薯", ["红薯", "水"], "staple"),
        source("清蒸豆腐", ["豆腐", "水"], "protein")]
    if limited:
        recipes = recipes[:4]
    profile = UserProfile(user_id=900001, data_scope="synthetic", age=30, sex="未指定")
    other = profile.model_copy(update={"user_id": 900003})
    catalog = DataCatalog({900001: profile, 900003: other}, {r.recipe_id: r for r in recipes}, {})
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "batch.db")
    seed = SessionState(session_id="a" * 32, user_id=900001, confirmed_fields=["people", "meal_type", "restrictions"],
        constraints=Constraints(people=1, dish_count=3, soup_count=0, no_spicy=True, allergies=allergies or []),
        menu_ids=[r.recipe_id for r in recipes[:3]], menu_valid=True)
    seed.meal_constraints = seed.constraints.model_copy(deep=True)
    store = SessionStore(settings.database_path)
    store.save(seed, None)
    app = create_app(settings, LocalIntents(intents), catalog, store)
    return app, recipes, {"user_id": 900001, "session_id": seed.session_id}, settings, catalog


@pytest.mark.parametrize("message", [
    "保留第2道，其他两道换成别的菜，不要菌菇。",
    "保留菌菇蒸鸡，另外两道给我换成别的菜，其他不要菌菇。",
    "第1、3道换成别的菜，第1、3道不要菌菇，保留第2道。",
    "保留第2道，其他不要菌菇。",
    "只换第1道和第3道，不要菌菇。",
])
def test_actual_batch_keeps_source_and_does_not_make_global_ban(tmp_path, message):
    app, recipes, payload, _, _ = setup(tmp_path, [Intent(action="plan", excluded_ingredients=["菌菇"])])
    with TestClient(app) as client:
        response = client.post("/chat", json={**payload, "message": message}).json()
    assert response["status"] == "ok", response["reason"]
    menu = response["menu"]
    assert len(menu) == 3
    assert menu[1]["recipe_id"] == recipes[1].recipe_id
    assert menu[1]["ingredients"] == [i.name for i in recipes[1].ingredients]
    assert menu[1]["steps"] == recipes[1].steps
    assert all(menu[s - 1]["recipe_id"] != recipes[s - 1].recipe_id for s in [1, 3])
    constraints = response["conversation_state"]["constraints"]
    assert constraints["excluded_ingredients"] == []
    assert constraints["allergies"] == []
    assert constraints["slot_food_exclusions"] == {"1": ["菌菇"], "3": ["菌菇"]}
    rules = RuleEngine()
    assert all(not local_food_hits(app.state.agent.catalog.recipes[menu[s - 1]["recipe_id"]], "菌菇", rules) for s in [1, 3])
    assert constraints["no_spicy"] and constraints["dish_count"] == 3 and constraints["soup_count"] == 0
    assert "第1道" in response["reason"] and "第3道" in response["reason"]
    assert "不等于整餐禁用" in response["reason"]


def test_batch_retry_explain_restart_next_edit_and_identity(tmp_path):
    app, recipes, payload, settings, catalog = setup(tmp_path, [Intent(), Intent(action="explain")])
    request = {**payload, "message": "保留第2道，其他两道换成别的菜，不要菌菇", "request_id": "batch-one"}
    with TestClient(app) as client:
        first = client.post("/chat", json=request).json()
        assert first["status"] == "ok", first["reason"]
        assert client.post("/chat", json=request).json()["menu"] == first["menu"]
        explained = client.post("/chat", json={**payload, "message": "解释这份菜单"}).json()
        assert explained["menu"] == first["menu"]
        assert client.post("/chat", json={**payload, "user_id": 900003, "message": "继续"}).status_code == 409
    app = create_app(settings, LocalIntents([Intent(action="replace", replace_slot=1)]), catalog, SessionStore(settings.database_path))
    with TestClient(app) as client:
        later = client.post("/chat", json={**payload, "message": "只换第1道，其他不动"}).json()
    assert later["status"] == "ok", later["reason"]
    assert later["menu"][1:] == first["menu"][1:]
    assert not local_food_hits(catalog.recipes[later["menu"][0]["recipe_id"]], "菌菇", RuleEngine())


def test_no_partial_edit_when_batch_cannot_fill(tmp_path):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent(), Intent()], limited=True)
    with TestClient(app) as client:
        first = client.post("/chat", json={**payload, "message": "保留第2道，其他两道换掉，不要菌菇"}).json()
        second = client.post("/chat", json={**payload, "message": "继续"}).json()
    assert first["status"] != "ok" and second["status"] != "ok"
    saved = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    assert saved.menu_ids == [r.recipe_id for r in recipes[:3]]
    assert saved.pending_context_replacement.replace_slots == [1, 3]
    assert saved.meal_constraints.excluded_ingredients == []


def test_new_allergy_overrides_unsafe_keep_without_mutating_other_slots(tmp_path):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent(allergies=["鸡肉"])])
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "我对鸡肉过敏，保留第2道，其他两道换掉，不要菌菇"}).json()
    assert result["status"] != "ok"
    assert "保留" in result["reason"] and "安全" in result["reason"]
    saved = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    assert "鸡肉" in saved.constraints.allergies
    assert saved.menu_ids == [r.recipe_id for r in recipes[:3]]


@pytest.mark.parametrize("message", [
    "保留这道，其他两道换掉，不要菌菇", "保留第2道，另外一道换掉，不要菌菇",
    "保留第2道，只换第2道", "只换第1、9道", "保留第2道，其他不要神秘未收录料汁",
])
def test_ambiguous_or_conflicting_batch_does_not_edit(tmp_path, message):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent(replace_slots=[1, 3], action="replace")])
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": message}).json()
    assert result["status"] == "clarification_required"
    assert SessionStore(settings.database_path).get(payload["session_id"], 900001).menu_ids == [r.recipe_id for r in recipes[:3]]


@pytest.mark.parametrize("message", ["比如保留第2道，其他两道换掉", "是否可以保留第2道，其他两道换掉？"])
def test_examples_are_not_literal_batch_authority(message):
    recipes = [source(str(i), ["水"], "vegetable") for i in range(3)]
    grounded, issue = ground_menu_edit(Intent(action="replace", replace_slots=[1, 3]), message, recipes, RuleEngine())
    assert issue and grounded.action == "clarify" and not grounded.replace_slots


def test_adapter_accepts_in_range_batch_but_rejects_overlap():
    state = SessionState(session_id="test", user_id=900001, menu_ids=["a", "b", "c"])
    DeepSeekLLM._validate_intent(Intent(action="replace", replace_slots=[1, 3], keep_slots=[2], local_excluded_ingredients=["菌菇"]), state)
    with pytest.raises(_OutputViolation):
        DeepSeekLLM._validate_intent(Intent(action="replace", replace_slots=[1, 3], keep_slots=[1]), state)


async def test_real_adapter_batch_contract_uses_mock_http_only():
    captured = []
    def reply(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"action": "replace", "replace_slots": [1, 3],
            "keep_slots": [2], "local_excluded_ingredients": ["菌菇"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        llm = DeepSeekLLM(api_key="fake-local-test", client=client)
        state = SessionState(session_id="a" * 32, user_id=900001, menu_ids=["PRIVATE_A", "PRIVATE_B", "PRIVATE_C"])
        profile = UserProfile(user_id=900001, age=30, sex="未指定", raw={"secret": "PRIVATE_PROFILE"})
        parsed = await llm.parse("保留第2道，其他两道换掉，其他不要菌菇", state, profile)
    assert parsed.replace_slots == [1, 3] and parsed.keep_slots == [2]
    assert len(captured) == 1
    text = json.dumps(captured, ensure_ascii=False)
    assert "PRIVATE_" not in text
    assert "替换多项时 action=clarify" not in captured[0]["messages"][0]["content"]


def test_local_constraints_apply_to_the_mentioned_slots_not_every_edit():
    recipes = [source(str(i), ["水"], "vegetable") for i in range(3)]
    intent, issue = ground_menu_edit(Intent(), "保留第2道，第1、3道换成别的菜，第1道不要菌菇，第3道不要鸡肉", recipes, RuleEngine())
    assert issue is None
    assert intent._local_food_scopes == {1: ["菌菇"], 3: ["鸡肉"]}


@pytest.mark.parametrize("food", ["白菜", "青菜", "芹菜", "菌菇"])
def test_whole_food_name_is_not_truncated_by_the_word_cai(food):
    recipes = [source(str(i), ["水"], "vegetable") for i in range(3)]
    intent, issue = ground_menu_edit(Intent(), f"我想保留第2道，其他两道换成别的菜，其他不要{food}", recipes, RuleEngine())
    assert issue is None
    assert intent._local_food_scopes == {1: [food], 3: [food]}


def test_local_model_fact_cannot_disappear_when_literal_form_is_unknown():
    recipes = [source(str(i), ["水"], "vegetable") for i in range(3)]
    intent, issue = ground_menu_edit(Intent(action="replace", replace_slots=[1, 3], local_excluded_ingredients=["菌菇"]),
        "只换第1、3道，口蘑一类按上次说的处理", recipes, RuleEngine())
    assert issue and not intent.local_excluded_ingredients


def test_other_clause_cannot_enlarge_an_explicit_only_edit():
    recipes = [source(str(i), ["水"], "vegetable") for i in range(3)]
    _, issue = ground_menu_edit(Intent(action="replace", replace_slot=1),
        "只换第1道，保留第2道，其他不要菌菇", recipes, RuleEngine())
    assert issue and "扩大" in issue


def test_explicit_whole_ban_is_not_silently_localized(tmp_path):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent(excluded_ingredients=["香菇"])])
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "保留第2道，其他两道换掉，整餐不要香菇"}).json()
    assert result["status"] != "ok"
    saved = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    assert "香菇" in saved.constraints.excluded_ingredients
    assert saved.menu_ids == [r.recipe_id for r in recipes[:3]]


def test_vegan_and_new_allergies_on_targeted_dishes_are_not_lost(tmp_path):
    app, recipes, payload, settings, catalog = setup(tmp_path, [Intent(diet_mode="vegan", allergies=["鸡肉"])])
    # Keep a vegan vegetable, replace BOTH chicken-containing targets at once.
    seed = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    seed.menu_ids = [recipes[1].recipe_id, recipes[3].recipe_id, recipes[2].recipe_id]
    SessionStore(settings.database_path).save(seed, seed.revision)
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "改为纯素，我对鸡肉过敏，保留第2道，第1、3道换掉"}).json()
    assert result["status"] == "ok", result["reason"]
    state = Constraints.model_validate(result["conversation_state"]["constraints"])
    assert state.diet_mode == "vegan" and "鸡肉" in state.allergies and state.no_spicy
    assert result["menu"][1]["recipe_id"] == recipes[3].recipe_id
    assert all(RuleEngine().evaluate(catalog.recipes[item["recipe_id"]], state).allowed for item in result["menu"])


def test_real_catalog_three_mushroom_menu_adjusts_two_slots(tmp_path):
    catalog = load_runtime_catalog()
    names = ["葱烧蘑菇", "菌菇蒸乳鸽", "香菇鸡肉焖饭"]
    chosen = [next(r for r in catalog.recipes.values() if r.name == n) for n in names]
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "actual.db")
    state = SessionState(session_id="b" * 32, user_id=900001, confirmed_fields=["people", "meal_type", "restrictions"],
        constraints=Constraints(people=1, no_spicy=True, allergies=["虾"], preferences=["家常"], preferred_ingredients=["香菇"]),
        menu_ids=[r.recipe_id for r in chosen], menu_valid=True)
    state.meal_constraints = state.constraints.model_copy(deep=True)
    store = SessionStore(settings.database_path)
    store.save(state, None)
    app = create_app(settings, LocalIntents([Intent(action="clarify", clarification="请明确菜位")]), catalog, store)
    with TestClient(app) as client:
        result = client.post("/chat", json={"user_id": 900001, "session_id": state.session_id,
            "message": "保留菌菇蒸乳鸽，其他两道换成别的菜，其他不要菌菇"}).json()
    assert result["status"] == "ok", result["reason"]
    assert result["menu"][1]["recipe_id"] == chosen[1].recipe_id
    assert result["menu"][1]["steps"] == chosen[1].steps
    for slot in [1, 3]:
        assert result["menu"][slot - 1]["recipe_id"] != chosen[slot - 1].recipe_id
        assert not local_food_hits(catalog.recipes[result["menu"][slot - 1]["recipe_id"]], "菌菇", RuleEngine())
    assert result["conversation_state"]["constraints"]["allergies"] == ["虾"]


@pytest.mark.parametrize("stream", [False, True])
def test_batch_works_on_compat_json_and_sse(tmp_path, stream):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent()])
    message = "保留第2道，其他两道换掉，其他不要菌菇"
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", headers={"X-Session-ID": payload["session_id"]},
            json=openai_payload(user="900001", stream=stream, messages=[{"role": "user", "content": message}]))
    assert response.status_code == 200, response.text
    if stream:
        chunks = [json.loads(row[6:]) for row in response.text.splitlines()
                  if row.startswith("data: ") and row != "data: [DONE]"]
        content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    else:
        content = response.json()["choices"][0]["message"]["content"]
    assert "第1道" in content and "第3道" in content and "不等于整餐禁用" in content
    saved = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    assert saved.menu_ids[1] == recipes[1].recipe_id
    assert saved.menu_ids[0] != recipes[0].recipe_id and saved.menu_ids[2] != recipes[2].recipe_id


def test_scoped_dislike_does_not_remove_an_existing_global_ban(tmp_path):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent()])
    store = SessionStore(settings.database_path)
    seed = store.get(payload["session_id"], 900001)
    seed.meal_constraints.excluded_ingredients = ["香菇"]
    seed.constraints.excluded_ingredients = ["香菇"]
    store.save(seed, seed.revision)
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "保留第2道，其他两道换掉，其他不要香菇"}).json()
    assert result["status"] != "ok"
    saved = store.get(payload["session_id"], 900001)
    assert saved.constraints.excluded_ingredients == ["香菇"]
    assert saved.menu_ids == [r.recipe_id for r in recipes[:3]]


def test_dish_count_change_cannot_truncate_locked_menu(tmp_path):
    app, recipes, payload, settings, _ = setup(tmp_path, [Intent(dish_count=2)])
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "改为2道菜，保留第2道，第1、3道换掉"}).json()
    assert result["status"] != "ok"
    assert SessionStore(settings.database_path).get(payload["session_id"], 900001).menu_ids == [r.recipe_id for r in recipes[:3]]


def test_soup_meat_and_vegetarian_quantities_remain_exact(tmp_path):
    app, _, payload, settings, catalog = setup(tmp_path, [Intent()])
    old_soup = source("香菇汤", ["香菇", "水"], "soup")
    new_soup = source("冬瓜汤", ["冬瓜", "水"], "soup")
    catalog.recipes.update({r.recipe_id: r for r in [old_soup, new_soup]})
    seed = SessionStore(settings.database_path).get(payload["session_id"], 900001)
    seed.menu_ids[2] = old_soup.recipe_id
    seed.constraints.people = seed.meal_constraints.people = 5
    seed.constraints.soup_count = seed.meal_constraints.soup_count = 1
    seed.constraints.meat_dish_count = seed.meal_constraints.meat_dish_count = 1
    seed.constraints.vegetarian_dish_count = seed.meal_constraints.vegetarian_dish_count = 1
    SessionStore(settings.database_path).save(seed, seed.revision)
    with TestClient(app) as client:
        result = client.post("/chat", json={**payload, "message": "保留第2道，第1、3道换掉，其他不要菌菇"}).json()
    assert result["status"] == "ok", result["reason"]
    state = Constraints.model_validate(result["conversation_state"]["constraints"])
    assert state.people == 5 and state.soup_count == 1 and state.dish_count == 3
    from app.domain.dish_composition import composition_satisfied
    chosen = [catalog.recipes[item["recipe_id"]] for item in result["menu"]]
    assert composition_satisfied(chosen, state)
    assert sum("soup" in r.categories for r in chosen) == 1
