"""Exposed count-scope regressions, not general Chinese understanding gold."""

from pathlib import Path

import pytest

from app.agent.dish_composition import explicit_dish_composition
from app.agent.menu_structure import explicit_menu_structure
from app.domain.models import Intent, UserProfile
from app.infrastructure.data import DataCatalog
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_dish_composition import recipe_pool

TOTAL_CASES = (
    ("四道总数包括汤：两道独立荤菜、一道素菜、一道素汤。", {"dish_count": 4, "soup_count": 1}),
    ("三道，不要汤，两道独立荤菜一道素菜。", {"dish_count": 3, "soup_count": 0}),
    ("两道不要汤，主食为主，再配一道豆腐。", {"dish_count": 2, "soup_count": 0}),
    ("要三道，一道独立荤菜、一道素菜加一道汤。", {"dish_count": 3, "soup_count": 1}),
    ("共7道菜，其中2道汤，总共七道菜。", {"dish_count": 7, "soup_count": 2}),
    ("七道，其中两道汤，合计七道。", {"dish_count": 7, "soup_count": 2}),
    (
        "总共七道菜，两道独立的荤菜、两道素菜、一道主食、两道素汤。",
        {"dish_count": 7, "soup_count": 2},
    ),
    ("四道菜加一道素汤", {"dish_count": 5, "soup_count": 1}),
    ("四菜一素汤", {"dish_count": 5, "soup_count": 1}),
    ("安排五道菜，其中一道清汤。", {"dish_count": 5, "soup_count": 1}),
    ("三道菜，两道素菜，再配一道菜心。", {"dish_count": 3}),
    ("再配一道豆腐", {}),
    ("两道独立荤菜、一道素菜", {}),
    ("一道主食、两道蔬菜", {}),
    ("一道汤面", {}),
    ("三道就好", {"dish_count": 3}),
    ("三道分别是一道荤菜、一道素菜和一道汤", {"dish_count": 3, "soup_count": 1}),
)
COMPOSITION_CASES = (
    ("两道独立荤菜一道素菜", {"meat_dish_count": 2, "vegetarian_dish_count": 1}),
    ("两道独立的荤菜、两道独立素菜", {"meat_dish_count": 2, "vegetarian_dish_count": 2}),
    (
        "四道包括汤，两道独立荤菜一道素菜一道素汤",
        {"meat_dish_count": 2, "vegetarian_dish_count": 1},
    ),
    ("两道素菜加一道素汤", {"vegetarian_dish_count": 2}),
    ("一道荤汤，两道素汤", {}),
    ("一道素菜汤", {}),
    ("0道独立荤菜三道素菜", {"meat_dish_count": 0, "vegetarian_dish_count": 3}),
)


@pytest.mark.parametrize("message,expected", TOTAL_CASES)
def test_total_and_soup_quantities_do_not_absorb_entree_or_ingredient_subcounts(message, expected):
    assert explicit_menu_structure(message) == (expected, None)


@pytest.mark.parametrize("message,expected", COMPOSITION_CASES)
def test_independent_entree_quotas_exclude_qualified_soup_quantities(message, expected):
    assert explicit_dish_composition(message) == (expected, None)


@pytest.mark.parametrize(
    "message",
    (
        "共七道菜，其中两道汤，总共六道菜。",
        "共四道菜，其中一道素汤、两道汤。",
        "安排四道菜加一道素汤，总共四道菜。",
        "安排两道菜，其中三道素汤。",
        "如果要四道菜，其中一道素汤呢？",
        "不要两道素汤，要一道汤。",
        "要四道菜，其中三到四道素汤。",
    ),
)
def test_real_total_or_soup_ambiguity_still_requires_confirmation(message):
    counts, issue = explicit_menu_structure(message)
    assert counts == {} and issue


@pytest.mark.parametrize(
    "message",
    (
        "不要两道独立荤菜，要一道独立荤菜。",
        "两到三道独立荤菜、两道素菜",
        "两道独立荤菜还是三道独立荤菜？",
        "九道独立荤菜",
        "一二道独立荤菜",
    ),
)
def test_independent_entree_ranges_negations_conflicts_and_limits_remain_pending(message):
    counts, issue = explicit_dish_composition(message)
    assert counts == {} and issue


def public_catalog():
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    return DataCatalog({3: profile}, {r.recipe_id: r for r in recipe_pool()}, {})


def test_chat_literal_counts_override_incorrect_model_total_and_ground_all_entree_quotas(
    tmp_path: Path,
):
    llm = ScriptedLLM(
        [
            complete_intent(
                people=4,
                dish_count=8,
                soup_count=0,
                meat_dish_count=5,
                vegetarian_dish_count=3,
                no_spicy=True,
            )
        ]
    )
    with client_for(tmp_path, public_catalog(), llm) as client:
        response = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": "4人晚餐，没有其他忌口。总共四道菜，包括一道素汤、两道独立荤菜和一道素菜，不辣。",
            },
        )
    result = response.json()
    assert response.status_code == 200 and result["status"] == "ok"
    constraints = result["conversation_state"]["constraints"]
    assert (
        constraints["dish_count"],
        constraints["soup_count"],
        constraints["meat_dish_count"],
        constraints["vegetarian_dish_count"],
    ) == (4, 1, 2, 1)
    assert len(result["menu"]) == 4
    assert sum(r["name"] in {"冬瓜汤", "排骨汤"} for r in result["menu"]) == 1
    assert sum(r["name"] in {"蒸鸡肉", "蒸牛肉", "蒸猪肉", "肉末白菜"} for r in result["menu"]) == 2
    assert all(r["name"] != "米饭" for r in result["menu"])
    assert constraints["no_spicy"] is True


def test_seven_including_two_soups_is_preserved_on_replay_restart_continue_and_local_edit(
    tmp_path: Path,
):
    catalog = public_catalog()
    llm = ScriptedLLM([complete_intent(people=5, dish_count=8, soup_count=0, no_spicy=True)])
    request = dict(
        user_id=3,
        request_id="count-ns-seven",
        message="5人晚餐，不辣，没有其他忌口。7道菜，其中2道汤，总共七道菜。",
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=request).json()
        sid = first["conversation_state"]["session_id"]
        replay = client.post("/chat", json=dict(request, session_id=sid)).json()
    assert first["status"] == "ok" and len(first["menu"]) == 7
    assert sum(r["name"] in {"冬瓜汤", "排骨汤"} for r in first["menu"]) == 2
    assert replay == first
    restart = ScriptedLLM([Intent(action="plan"), Intent(action="replace", replace_slot=2)])
    with client_for(tmp_path, catalog, restart) as client:
        continued = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="继续")
        ).json()
        local = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只换第二道，其余保持不变")
        ).json()
    assert continued["status"] == local["status"] == "ok"
    assert [r["recipe_id"] for r in first["menu"]] == [r["recipe_id"] for r in continued["menu"]]
    assert all(
        a["recipe_id"] == b["recipe_id"]
        for i, (a, b) in enumerate(zip(continued["menu"], local["menu"]))
        if i != 1
    )
    assert local["conversation_state"]["constraints"]["dish_count"] == 7
    assert local["conversation_state"]["constraints"]["soup_count"] == 2
