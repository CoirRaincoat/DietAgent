"""Known missing no-spicy meal dish, not generic recipe generation."""

import json

import pytest

from app.agent.recipe_generation import (
    propose_missing_liangfen,
    resolve_generated_recipe,
    verified_generated_recipe,
)
from app.domain.models import Constraints, Intent
from app.domain.recipe_origin import generator_version
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.rules.engine import RuleEngine
from tests.test_recipe_generation import make_client, request_intent, sample_catalog

MESSAGE = "2人晚餐，安排3道菜，不要汤，明确不要辣，想吃凉粉，没有其他忌口。"


def liangfen_intent(**updates):
    return request_intent(**{"diet_mode": None, "preferred_ingredients": ["凉粉"], **updates})


def proposal(**updates):
    constraints = Constraints.model_validate({
        "people": 2, "meal_type": "晚餐", "no_spicy": True,
        "preferred_ingredients": ["凉粉"], **updates,
    })
    return propose_missing_liangfen(constraints, [], [], set(), RuleEngine())


def test_default_source_m03_actual_menu_improves_and_other_source_dishes_stay(tmp_path):
    catalog = load_runtime_catalog()
    frozen = {key: recipe.model_dump() for key, recipe in catalog.recipes.items()}
    # Keep the original query-only parse; literal intent recovery is production.
    intent = request_intent(diet_mode=None, preferred_ingredients=[], query_terms=["凉粉"])
    with make_client(tmp_path, catalog, [intent]) as client:
        body = dict(user_id=900001, message=MESSAGE, request_id="liangfen-m03")
        result = client.post("/chat", json=body).json()
        assert result["status"] == "ok"
        assert [item["name"] for item in result["menu"]] == [
            "白灼芥蓝", "柠檬白斩鸡", "黄瓜清拌凉粉（新生成）",
        ]
        item = result["menu"][2]
        assert item["provenance"]["origin"] == "generated"
        assert item["provenance"]["source_row"] is None
        assert item["provenance"]["generator_version"] == "local-non-spicy-liangfen-v1"
        assert item["ingredients"] == ["豌豆淀粉", "水", "黄瓜", "米醋", "盐"]
        assert item["card"]["servings"] is None and item["card"]["cooking_minutes"] is None
        assert "当前菜单未覆盖食材偏好：凉粉" not in result["reason"]
        assert "待试做" in result["reason"] and "冷却凝固" in result["reason"]
        assert "不是原2000菜谱" in result["reason"] and "不承诺" in result["reason"]
        state = client.app.state.agent.store.get(result["conversation_state"]["session_id"], 900001)
        chosen = [client.app.state.agent._recipes(state)[key] for key in state.menu_ids]
        assert all(client.app.state.agent.rules.evaluate(r, state.constraints).allowed for r in chosen)
        assert sum("soup" in r.categories for r in chosen) == 0
        assert state.constraints.people == 2 and state.constraints.dish_count == 3
        assert state.constraints.soup_count == 0 and state.constraints.no_spicy
        for entry in result["menu"][:2]:
            assert client.app.state.agent._recipes(state)[entry["recipe_id"]] == catalog.recipes[entry["recipe_id"]]
        assert client.post("/chat", json=body).json() == result
        assert client.post("/chat", json={**body, "session_id": state.session_id, "user_id": 900002}).status_code == 409
    assert {key: r.model_dump() for key, r in catalog.recipes.items()} == frozen
    assert len(catalog.recipes) == 2000


@pytest.mark.parametrize("updates", [
    {"allergies": ["豌豆"]}, {"allergies": ["豆类"]}, {"allergies": ["未知过敏原"]},
    {"excluded_ingredients": ["豌豆"]}, {"excluded_ingredients": ["黄瓜"]},
    {"excluded_ingredients": ["醋"]}, {"excluded_ingredients": ["凉粉"]},
    {"inventory": ["水", "大米"]}, {"inventory": []}, {"max_minutes": 15},
    {"meal_type": "早餐"}, {"preferred_ingredients": []}, {"no_spicy": False},
    {"preferences": ["不要酸"]},
])
def test_no_constraint_waiver_or_unrequested_generation(updates):
    assert proposal(**updates) is None


def test_vegan_draft_and_versioned_identity_are_exact_not_arbitrary():
    draft = proposal(diet_mode="vegan")
    assert draft is not None
    assert RuleEngine().evaluate(draft, Constraints(diet_mode="vegan", no_spicy=True)).allowed
    assert resolve_generated_recipe(draft.recipe_id) == draft
    assert generator_version(draft) == "local-non-spicy-liangfen-v1"
    assert verified_generated_recipe(draft)
    assert not verified_generated_recipe(draft.model_copy(update={"steps": draft.steps + "加入蛋清。"}))
    assert resolve_generated_recipe("generated_unknown_liangfen") is None
    assert propose_missing_liangfen(Constraints(preferred_ingredients=["凉粉"], no_spicy=True),
                                   [], [], {draft.recipe_id}, RuleEngine()) is None


def test_safe_source_dish_or_existing_menu_prevents_generation_even_when_rejected():
    draft = proposal()
    source = draft.model_copy(update={"recipe_id": "source_liangfen", "source_row": 3, "quality_flags": []})
    c = Constraints(preferred_ingredients=["凉粉"], no_spicy=True)
    rules = RuleEngine()
    assert propose_missing_liangfen(c, [source], [], {source.recipe_id}, rules) is None
    assert propose_missing_liangfen(c, [], [draft], set(), rules) is None
    # Milk/dessert word hits must not occupy a main-meal slot.
    drink = source.model_copy(update={"categories": ["drink"], "name": "凉粉奶饮"})
    assert propose_missing_liangfen(c, [drink], [], set(), rules) == draft


def test_restart_readonly_continuation_and_other_user_do_not_synthesize(tmp_path):
    catalog = sample_catalog()
    with make_client(tmp_path, catalog, [liangfen_intent()]) as client:
        first = client.post("/chat", json=dict(user_id=900001, message=MESSAGE)).json()
        assert first["status"] == "ok" and first["menu"][2]["provenance"]["origin"] == "generated"
    sid = first["conversation_state"]["session_id"]
    with make_client(tmp_path, catalog, [Intent(action="explain"), Intent(),
                                       liangfen_intent(preferred_ingredients=[])]) as client:
        for text in ("解释当前菜单，不换菜", "继续"):
            same = client.post("/chat", json=dict(user_id=900001, session_id=sid, message=text)).json()
            assert same["menu"] == first["menu"]
            assert "待试做" in same["reason"]
            assert not any(e["name"] == "recipe_generate" for e in same["tool_calls"])
        other = client.post("/chat", json=dict(user_id=900003, message="2人晚餐三菜零汤不辣，没有其他忌口")).json()
        assert all(e["provenance"]["origin"] == "catalog" for e in other["menu"])


def test_local_slot_generation_preserves_other_two_dishes(tmp_path):
    catalog = sample_catalog()
    with make_client(tmp_path, catalog, [liangfen_intent(preferred_ingredients=[]),
                    Intent(action="replace", replace_slot=3, preferred_ingredients=["凉粉"])]) as client:
        first = client.post("/chat", json=dict(user_id=900001, message="2人晚餐三菜零汤不辣，没有其他忌口")).json()
        second = client.post("/chat", json=dict(user_id=900001,
            session_id=first["conversation_state"]["session_id"], message="只换第三道，要不辣凉粉")).json()
        assert second["status"] == "ok"
        assert second["menu"][:2] == first["menu"][:2]
        assert second["menu"][2]["provenance"]["origin"] == "generated"


def test_readonly_explicit_preference_cannot_create_new_menu(tmp_path):
    with make_client(tmp_path, sample_catalog(), [liangfen_intent(preferred_ingredients=[]),
                    Intent(action="explain", preferred_ingredients=["凉粉"])]) as client:
        first = client.post("/chat", json=dict(user_id=900001, message="2人晚餐三菜零汤不辣，没有其他忌口")).json()
        second = client.post("/chat", json=dict(user_id=900001,
            session_id=first["conversation_state"]["session_id"], message="只解释凉粉方案，不换菜")).json()
        assert second["menu"] == first["menu"]
        assert not any(e["name"] == "recipe_generate" for e in second["tool_calls"])


def test_compatibility_stream_includes_generated_source_and_mandatory_boundary(tmp_path):
    with make_client(tmp_path, sample_catalog(), [liangfen_intent()]) as client:
        response = client.post("/v1/chat/completions", json={
            "user": "900001", "model": "fangtai-meal-agent", "stream": True,
            "messages": [{"role": "user", "content": MESSAGE}],
        })
        assert response.status_code == 200
        contents = []
        for line in response.text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                contents.append(event["choices"][0]["delta"].get("content", ""))
        text = "".join(contents)
        assert "黄瓜清拌凉粉" in text and "不是原2000菜谱" in text and "待试做" in text
        assert "[DONE]" in response.text
