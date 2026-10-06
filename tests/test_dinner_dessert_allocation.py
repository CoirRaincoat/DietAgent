"""Explicit dinner dessert slots use real source recipes without adding dishes."""

import json

import pytest

from app.agent.dessert_requests import explicit_dessert_count
from app.agent.planner import MenuPlanner
from app.domain.dish_composition import dish_kind
from app.domain.meal_roles import is_dessert_recipe, is_main_meal_recipe
from app.domain.models import Constraints, Ingredient, Intent, Recipe
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.rules.engine import RuleEngine
from tests.test_recipe_generation import make_client, request_intent, sample_catalog

MESSAGE = "2人晚餐，共4道菜，其中1道饭后甜点，不要汤，不辣，没有其他忌口。"


def dessert(name="蒸红薯甜点", foods=("红薯", "水"), **updates):
    return Recipe(**{
        "recipe_id": name, "name": name, "ingredients": [Ingredient(raw=f, name=f) for f in foods],
        "raw_ingredients": "；".join(foods), "steps": "将食材蒸熟后装盘食用。",
        "source_row": 10, "fingerprint": name, "categories": ["dessert"],
        "labels": ["甜点"], "meal_types": ["晚餐"], **updates,
    })


def dinner_intent(**updates):
    return request_intent(**{"diet_mode": None, "preferred_ingredients": [],
                            "dish_count": 4, **updates})


def with_desserts():
    catalog = sample_catalog()
    for r in (dessert(), dessert("蒸南瓜甜点", ("南瓜", "水"))):
        catalog.recipes[r.recipe_id] = r
    return catalog


@pytest.mark.parametrize("text,count", [
    ("3道菜，其中一道甜点", 1), (MESSAGE, 1),
    ("共5道菜，含2道甜品", 2), ("本餐不要甜点", 0),
])
def test_grounded_counts(text, count):
    assert explicit_dessert_count(text, total_explicit=True) == (count, None)


@pytest.mark.parametrize("text", ["饭后加一道甜点", "3道菜另加一道甜点", "共3道菜，只换第二道为一道甜点",
                                  "共3道菜，不包含一道甜点", "共3道菜，其中不要安排一道甜点"])
def test_ambiguous_extra_or_local_allocation_asks_instead_of_guessing(text):
    count, issue = explicit_dessert_count(text, total_explicit=True)
    assert count is None and issue


@pytest.mark.parametrize("text", ["晚饭可以搭配甜点吗？", "比如共3道菜，其中一道甜点", "解释上次的1道甜点"])
def test_question_or_example_not_permission(text):
    assert explicit_dessert_count(text, total_explicit=True) == (None, None)


def test_dessert_not_main_or_entree_quota_and_drink_not_dessert():
    r = dessert()
    assert is_dessert_recipe(r) and not is_main_meal_recipe(r)
    assert dish_kind(r) == "other"
    assert not is_dessert_recipe(r.model_copy(update={"categories": ["protein"]}))
    assert not is_dessert_recipe(dessert("红薯奶汁", steps="将食材榨汁后倒入杯饮用。", foods=("红薯", "牛奶")))
    assert not is_dessert_recipe(dessert("银耳甜羹", foods=("银耳", "冰糖", "水")))
    assert not is_dessert_recipe(dessert("炖梨&梨挞同烹", steps="将梨挞放入第5层，将炖梨放入第2层。开始烹饪。分别装盘。"))
    assert RuleEngine().evaluate(r, Constraints(excluded_ingredients=["饮料"])).allowed
    for excluded in ("甜点", "甜品"):
        assert not RuleEngine().evaluate(r, Constraints(excluded_ingredients=[excluded])).allowed


def test_default_source_dinner_actual_menu_includes_one_dessert(tmp_path):
    catalog = load_runtime_catalog()
    frozen = {key: r.model_dump() for key, r in catalog.recipes.items()}
    with make_client(tmp_path, catalog, [dinner_intent()]) as client:
        body = dict(user_id=900001, message=MESSAGE, request_id="dinner-dessert")
        result = client.post("/chat", json=body).json()
        assert result["status"] == "ok", result["reason"]
        assert len(result["menu"]) == 4
        chosen = [catalog.recipes[r["recipe_id"]] for r in result["menu"]]
        assert all(is_main_meal_recipe(r) for r in chosen[:3])
        assert is_dessert_recipe(chosen[-1])
        assert all(RuleEngine().evaluate(r, Constraints(no_spicy=True)).allowed for r in chosen)
        assert result["conversation_state"]["constraints"]["dessert_count"] == 1
        assert "不计作蛋白主菜" in result["reason"]
        assert all(r["provenance"]["origin"] == "catalog" for r in result["menu"])
        assert client.post("/chat", json=body).json() == result
        assert client.post("/chat", json={**body, "user_id": 900003,
                                           "session_id": result["conversation_state"]["session_id"]}).status_code == 409
    assert frozen == {key: r.model_dump() for key, r in catalog.recipes.items()}


def test_three_total_including_dessert_never_becomes_four(tmp_path):
    with make_client(tmp_path, with_desserts(), [dinner_intent(dish_count=3)]) as client:
        result = client.post("/chat", json=dict(user_id=900001,
            message="2人晚餐，共3道菜，其中一道甜点，0汤，不辣，没有其他忌口。")).json()
        assert result["status"] == "ok", result["reason"]
        assert len(result["menu"]) == 3
        assert result["menu"][-1]["name"].endswith("甜点")


@pytest.mark.parametrize("updates", [
    {"allergies": ["奶"]}, {"diet_mode": "vegan"}, {"excluded_ingredients": ["牛奶"]},
    {"inventory": ["红薯", "水"]}, {"no_spicy": True},
])
def test_dessert_still_hard_screened(updates):
    catalog = sample_catalog()
    forbidden = dessert("辣味牛奶布丁", ("牛奶", "辣椒", "水"))
    c = Constraints(dish_count=4, dessert_count=1, **updates)
    result = MenuPlanner(RuleEngine()).plan([*catalog.recipes.values(), forbidden], c)
    assert result.failure and not result.recipes


def test_local_dessert_replacement_and_readonly_do_not_change_main(tmp_path):
    catalog = with_desserts()
    intents = [dinner_intent(), Intent(action="replace", replace_slot=4), Intent(action="explain"), Intent()]
    with make_client(tmp_path, catalog, intents) as client:
        first = client.post("/chat", json=dict(user_id=900001, message=MESSAGE)).json()
        sid = first["conversation_state"]["session_id"]
        changed = client.post("/chat", json=dict(user_id=900001, session_id=sid, message="只换第4道甜点，其他不动")).json()
        assert first["status"] == changed["status"] == "ok", changed["reason"]
        assert first["menu"][:3] == changed["menu"][:3]
        assert first["menu"][3]["recipe_id"] != changed["menu"][3]["recipe_id"]
        for message in ("只解释，不换菜", "继续"):
            result = client.post("/chat", json=dict(user_id=900001, session_id=sid, message=message)).json()
            assert result["status"] == "ok" and result["menu"] == changed["menu"]


def test_unrequested_dessert_not_inserted_and_missing_dessert_is_disclosed(tmp_path):
    with make_client(tmp_path, with_desserts(), [dinner_intent(dish_count=3)]) as client:
        result = client.post("/chat", json=dict(user_id=900001, message="2人晚餐，3菜0汤，不辣，没有其他忌口。")).json()
        assert result["status"] == "ok" and len(result["menu"]) == 3
        assert all(not r["name"].endswith("甜点") for r in result["menu"])
    with make_client(tmp_path / "missing", sample_catalog(), [dinner_intent()]) as client:
        result = client.post("/chat", json=dict(user_id=900001, message=MESSAGE)).json()
        assert result["status"] == "no_feasible_menu" and result["menu"] == []


def test_unresolved_extra_dessert_request_survives_unrelated_turn(tmp_path):
    with make_client(tmp_path, with_desserts(), [dinner_intent(dish_count=3), Intent(),
                                               Intent(dish_count=4)]) as client:
        first = client.post("/chat", json=dict(user_id=900001,
            message="2人晚餐，3道菜，饭后加一道甜点，不辣，没有其他忌口。")).json()
        assert first["status"] == "clarification_required" and first["menu"] == []
        sid = first["conversation_state"]["session_id"]
        waiting = client.post("/chat", json=dict(user_id=900001, session_id=sid, message="继续")).json()
        assert waiting["status"] == "clarification_required" and waiting["menu"] == []
        fixed = client.post("/chat", json=dict(user_id=900001, session_id=sid,
            message="共4道菜，其中1道饭后甜点，0汤")).json()
        assert fixed["status"] == "ok" and len(fixed["menu"]) == 4


def test_sse_carries_non_optional_dessert_allocation(tmp_path):
    with make_client(tmp_path, with_desserts(), [dinner_intent()]) as client:
        response = client.post("/v1/chat/completions", json=dict(
            user="900001", model="fangtai-meal-agent", stream=True,
            messages=[{"role": "user", "content": MESSAGE}],
        ))
        text = ""
        for line in response.text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                text += event.get("choices", [{}])[0].get("delta", {}).get("content", "")
        assert "不计作蛋白主菜" in text
