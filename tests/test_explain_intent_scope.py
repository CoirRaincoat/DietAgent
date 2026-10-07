"""Explanation extraction cannot authorize edits to accepted meal requirements."""

import pytest

from app.domain.models import DinerUpdate, Ingredient, Intent
from app.infrastructure.sessions import SessionStore
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as api_catalog


@pytest.fixture
def catalog():
    return api_catalog.__wrapped__()

OVEREXTRACTIONS = [
    {"preferences": ["不要香辣"]},
    {"preferred_ingredients": ["虾"]},
    {"excluded_ingredients": ["鸡肉"]},
    {"allergies": ["鱼"]},
    {"allergy_clarifications": {"海鲜": ["虾"]}},
    {"health_goals": ["降压"]},
    {"no_spicy": True},
    {"no_spicy": False},
    {"inventory": ["白菜"]},
    {"diet_mode": "vegan"},
    {"people": 5},
    {"meal_type": "早餐"},
    {"dish_count": 7, "soup_count": 2},
    {"meat_dish_count": 2},
    {"vegetarian_dish_count": 2},
    {"max_minutes": 15},
    {"clear_time_limit": True},
    {"diner_updates": [DinerUpdate(diner="妈妈", attendance=True, allergies=["鱼"])]},
    {"method_meal_priority": "meal"},
    {"replace_slot": 2, "query_terms": ["虾"]},
]

PROTECTED = (
    "constraints",
    "meal_constraints",
    "diners",
    "confirmed_fields",
    "menu_structure_explicit",
    "menu_ids",
    "rejected_recipe_ids",
    "meal_sequence",
    "recent_recommendations",
    "pending_allergy",
    "pending_allergy_terms",
    "pending_diet_mode",
    "pending_dish_composition",
    "pending_soup_composition",
    "pending_method_tradeoff",
    "method_meal_priority_binding",
    "pending_flavor_resolution",
    "flavor_retractions",
)


@pytest.mark.parametrize("overextraction", OVEREXTRACTIONS)
def test_explanation_fields_do_not_edit_accepted_requirements(tmp_path, catalog, overextraction):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain", **overextraction)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": "1人晚餐，3道菜，没有其他忌口。",
            },
        ).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        payload = {
            "user_id": 3,
            "session_id": sid,
            "request_id": "read-only",
            "message": "解释一下不要香辣的含义，不要修改菜单。",
        }
        second = client.post("/chat", json=payload).json()
        assert second["status"] == "ok"
        assert [r["recipe_id"] for r in second["menu"]] == [r["recipe_id"] for r in first["menu"]]
        for field in PROTECTED:
            assert second["conversation_state"][field] == first["conversation_state"][field], field
        assert not {"recipe_search", "menu_modify"} & {t["name"] for t in second["tool_calls"]}
        assert client.post("/chat", json=payload).json() == second
        assert llm.parse_calls == 2
    restored = SessionStore(tmp_path / "state.db").get(sid, 3)
    assert restored is not None and restored.menu_valid
    for field in PROTECTED:
        assert restored.model_dump(mode="json")[field] == first["conversation_state"][field]


@pytest.mark.parametrize(
    "message",
    [
        "解释一下7道菜其中2道汤是什么意思，不要修改菜单。",
        "解释一下荤菜2道素菜2道是什么意思，不要修改菜单。",
        "解释一下鸡肉要烤、场景便当的意思，不要修改菜单。",
    ],
)
def test_explanation_literal_structure_is_not_a_meal_amendment(tmp_path, catalog, message):
    with client_for(
        tmp_path, catalog, ScriptedLLM([complete_intent(), Intent(action="explain")])
    ) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": message,
                "session_id": first["conversation_state"]["session_id"],
            },
        ).json()
        assert first["status"] == second["status"] == "ok"
        for field in PROTECTED:
            assert second["conversation_state"][field] == first["conversation_state"][field], field


def test_explanation_cannot_confirm_missing_context_from_model_defaults(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM([complete_intent(action="explain")])) as client:
        result = client.post("/chat", json={"user_id": 3, "message": "解释晚餐菜单的意思"}).json()
        assert result["status"] == "clarification_required" and not result["menu"]
        assert result["conversation_state"]["confirmed_fields"] == []


def test_explanation_does_not_waive_existing_hard_safety_or_accept_new_unguarded_allergy(
    tmp_path, catalog
):
    llm = ScriptedLLM(
        [
            complete_intent(allergies=["花生"], no_spicy=True),
            Intent(action="explain", no_spicy=False, allergy_clarifications={"花生": []}),
            Intent(action="explain", allergies=["虾"]),
        ]
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，花生过敏，不吃辣，没有其他忌口"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "解释这份菜单"}
        ).json()
        assert first["status"] == second["status"] == "ok"
        assert second["conversation_state"]["constraints"]["allergies"] == ["花生"]
        assert second["conversation_state"]["constraints"]["no_spicy"]
        third = client.post(
            "/chat",
            json={"user_id": 3, "session_id": sid, "message": "解释这份菜单。另外我对虾过敏。"},
        ).json()
        assert third["status"] == "clarification_required" and not third["menu"]
        assert third["conversation_state"]["pending_allergy"]
        assert third["conversation_state"]["constraints"]["allergies"] == ["花生"]


def test_explanation_with_real_nonspicy_clause_rechecks_safety_without_replanning(
    tmp_path, catalog
):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain", no_spicy=True)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "解释这份菜单。另外我不吃辣。",
            },
        ).json()
        assert second["conversation_state"]["constraints"]["no_spicy"]
        assert not {"recipe_search", "menu_modify"} & {t["name"] for t in second["tool_calls"]}


@pytest.mark.parametrize(
    "message",
    [
        "解释一下‘我不吃辣’是什么意思，不要修改菜单。",
        "解释这份菜单。如果我不吃辣。",
        "解释我不吃辣这个例句。",
    ],
)
def test_nonspicy_examples_are_not_new_safety_assertions(tmp_path, catalog, message):
    with client_for(
        tmp_path, catalog, ScriptedLLM([complete_intent(), Intent(action="explain", no_spicy=True)])
    ) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": message,
                "session_id": first["conversation_state"]["session_id"],
            },
        ).json()
        assert second["status"] == "ok"
        assert not second["conversation_state"]["constraints"]["no_spicy"]


def test_explanation_is_not_a_new_recommendation_in_experimental_history(tmp_path, catalog):
    llm = ScriptedLLM(
        [complete_intent(), Intent(action="explain", preferences=["不要香辣"], people=5)]
    )
    with client_for(tmp_path, catalog, llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        before = SessionStore(tmp_path / "state.db").recommendation_history(3)
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "request_id": "explain-history",
                "message": "解释下一餐的意思，不要修改菜单。",
            },
        ).json()
        after = SessionStore(tmp_path / "state.db").recommendation_history(3)
        assert second["status"] == "ok"
        assert before == after
        assert second["conversation_state"]["meal_sequence"] == 0
        assert not second["conversation_state"]["recent_recommendations"]
        assert (
            second["conversation_state"]["last_recommendation"]
            == first["conversation_state"]["last_recommendation"]
        )


def test_explanation_cannot_resolve_existing_unknown_allergy_with_extracted_fields(
    tmp_path, catalog
):
    llm = ScriptedLLM(
        [
            complete_intent(allergies=["神秘食材"]),
            Intent(action="explain", allergies=["虾"], allergy_clarifications={"神秘食材": ["虾"]}),
        ]
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，对神秘食材过敏"}
        ).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "解释这份菜单",
            },
        ).json()
        assert second["status"] == "clarification_required" and not second["menu"]
        assert second["conversation_state"]["pending_allergy"]
        assert (
            second["conversation_state"]["pending_allergy_terms"]
            == first["conversation_state"]["pending_allergy_terms"]
        )
        assert (
            second["conversation_state"]["constraints"]
            == first["conversation_state"]["constraints"]
        )


def test_explanation_revalidates_changed_catalog_against_existing_allergy(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(allergies=["花生"]), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，花生过敏，没有其他忌口"}
        ).json()
        assert first["status"] == "ok"
        old = catalog.recipes[first["menu"][0]["recipe_id"]]
        catalog.recipes[old.recipe_id] = old.model_copy(
            update={
                "raw_ingredients": old.raw_ingredients + "；花生",
                "ingredients": old.ingredients + [Ingredient(raw="花生", name="花生")],
            }
        )
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "解释这份菜单",
            },
        ).json()
        assert second["status"] == "clarification_required" and not second["menu"]
        assert (
            second["conversation_state"]["constraints"]
            == first["conversation_state"]["constraints"]
        )
        assert "menu_modify" not in {t["name"] for t in second["tool_calls"]}
