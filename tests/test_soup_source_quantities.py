"""Public source soup quantities; no nutrient, private-user or clinical labels."""

from itertools import combinations
from pathlib import Path

import pytest

from app.agent.dish_composition import plan_composition
from app.agent.menu_structure import explicit_menu_structure
from app.domain.dish_composition import composition_satisfied
from app.domain.models import Constraints, Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_dish_composition import fixture_dish, recipe_pool
from tests.test_independent_meat_quota import public_catalog

SOURCE_SOUPS = [
    ("冬瓜汤", "冬瓜200克；水500克；盐1克", "煮熟后装碗。", "vegetarian"),
    ("番茄蛋汤", "番茄100克；鸡蛋100克；水500克", "煮熟后装碗。", "vegetarian"),
    ("排骨汤", "排骨200克；水500克", "煮熟后装碗。", "meat"),
    ("虾皮冬瓜汤", "冬瓜200克；虾皮5克；水500克", "煮熟后装碗。", "meat"),
    ("素菜汤", "白菜200克；鸡汤300克", "煮熟后装碗。", "meat"),
    ("蔬菜汤", "白菜200克；水500克", "加入猪油煮熟装碗。", "meat"),
    ("高汤白菜汤", "白菜200克；高汤300克", "煮熟后装碗。", "other"),
    ("鸡精白菜汤", "白菜200克；鸡精1克；水500克", "煮熟后装碗。", "other"),
    ("神秘素汤", "白菜200克；神秘酱1克；水500克", "煮熟后装碗。", "other"),
]


@pytest.mark.parametrize("name,foods,steps,expected", SOURCE_SOUPS)
def test_soup_source_is_not_certified_from_title_or_soup_slot(name, foods, steps, expected):
    from app.domain.dish_composition import soup_source_counts

    recipe = fixture_dish(name, foods, steps)
    assert soup_source_counts([recipe]) == {expected: 1}
    c = Constraints(dish_count=1, soup_count=1, vegetarian_soup_count=1)
    assert composition_satisfied([recipe], c) is (expected == "vegetarian")


SOUP_QUANTITIES = [
    ("四道菜其中一道素汤", {"vegetarian_soup_count": 1}, {"dish_count": 4, "soup_count": 1}),
    (
        "七道菜其中一道素汤一道荤汤",
        {"vegetarian_soup_count": 1, "meat_soup_count": 1},
        {"dish_count": 7, "soup_count": 2},
    ),
    (
        "共五道菜，两道素汤一道肉汤",
        {"vegetarian_soup_count": 2, "meat_soup_count": 1},
        {"dish_count": 5, "soup_count": 3},
    ),
    (
        "四道菜加一道素汤和一道荤汤",
        {"vegetarian_soup_count": 1, "meat_soup_count": 1},
        {"dish_count": 6, "soup_count": 2},
    ),
    (
        "七道菜共两道汤，其中一道素菜汤一道荤菜汤",
        {"vegetarian_soup_count": 1, "meat_soup_count": 1},
        {"dish_count": 7, "soup_count": 2},
    ),
    ("3道菜其中1道清汤", {}, {"dish_count": 3, "soup_count": 1}),
    (
        "共三道菜，0道荤汤1道素菜汤",
        {"vegetarian_soup_count": 1, "meat_soup_count": 0},
        {"dish_count": 3, "soup_count": 1},
    ),
]


@pytest.mark.parametrize("message,expected_source,expected_total", SOUP_QUANTITIES)
def test_literal_source_quantities_and_total_soup_are_different_namespaces(
    message, expected_source, expected_total
):
    from app.agent.menu_structure import explicit_soup_composition

    assert explicit_soup_composition(message) == (expected_source, None)
    assert explicit_menu_structure(message) == (expected_total, None)


@pytest.mark.parametrize(
    "message",
    [
        "一道素汤还是一道荤汤？",
        "不要一道素汤",
        "一到两道素汤",
        "两道素汤又写一道素汤",
        "四道素汤",
        "两道汤其中三道素汤",
    ],
)
def test_uncertain_or_conflicting_soup_quantities_cannot_publish_a_source_quota(message):
    from app.agent.menu_structure import explicit_soup_composition

    counts, issue = explicit_soup_composition(message)
    assert counts == {} and issue


@pytest.mark.parametrize(
    "message",
    [
        "解释上次的一道素汤",
        "比如一道荤汤",
        "‘一道素汤’",
        "一碗素汤面",
        "第二道素汤",
        "一道清淡汤",
        "一道蔬菜汤",
    ],
)
def test_quotes_ordinals_food_names_and_light_flavor_do_not_certify_soup_diet(message):
    from app.agent.menu_structure import explicit_soup_composition

    assert explicit_soup_composition(message) == ({}, None)


def soup_pool():
    return recipe_pool() + [
        fixture_dish(name, foods, steps)
        for name, foods, steps, _ in SOURCE_SOUPS
        if name not in {"冬瓜汤", "排骨汤"}
    ]


def test_known_four_dish_gap_fixed_and_soup_requirement_survives_replay_restart_local(
    tmp_path: Path,
):
    llm = ScriptedLLM(
        [complete_intent(people=4, dish_count=8, soup_count=0, no_spicy=True, allergies=["虾"])]
    )
    catalog = public_catalog(soup_pool())
    payload = dict(
        user_id=3,
        request_id="soup-source-four",
        message="4人晚餐，没有其他忌口，不辣且虾过敏。总共四道菜，包括一道素汤、两道独立荤菜和一道素菜。",
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=payload).json()
        sid = first["conversation_state"]["session_id"]
        replay = client.post("/chat", json=dict(payload, session_id=sid)).json()
    assert first["status"] == "ok" and first == replay and llm.parse_calls == 1
    soups = [r for r in first["menu"] if "汤" in r["name"]]
    assert len(soups) == 1 and soups[0]["name"] in {"冬瓜汤", "番茄蛋汤"}
    assert len({r["name"] for r in first["menu"]} & {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}) == 2
    assert "素汤" in first["reason"] and "允许蛋奶" in first["reason"]
    slot = soups[0]["slot"]
    next_llm = ScriptedLLM(
        [Intent(action="explain"), Intent(), Intent(action="replace", replace_slot=slot)]
    )
    with client_for(tmp_path, catalog, next_llm) as client:
        explained = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="解释这餐")
        ).json()
        continued = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="继续")
        ).json()
        local = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message=f"只换第{slot}道汤，其余不变")
        ).json()
        other = client.post("/chat", json=dict(user_id=4, session_id=sid, message="继续"))
    for result in (first, explained, continued, local):
        assert result["status"] == "ok"
        assert result["conversation_state"]["constraints"]["vegetarian_soup_count"] == 1
        assert result["conversation_state"]["constraints"]["no_spicy"]

    def ids(result):
        return [r["recipe_id"] for r in result["menu"]]

    assert ids(first) == ids(explained) == ids(continued)
    assert all(a == b for i, (a, b) in enumerate(zip(ids(continued), ids(local))) if i != slot - 1)
    assert ids(continued)[slot - 1] != ids(local)[slot - 1]
    assert local["menu"][slot - 1]["name"] in {"冬瓜汤", "番茄蛋汤"} and other.status_code == 409


@pytest.mark.parametrize("message", ["总共三道菜，其中一道素汤", "总共三道菜，其中一道荤汤"])
def test_source_quota_failure_cannot_use_unknown_soup_to_fill_slots(tmp_path: Path, message):
    rows = [r for r in recipe_pool() if not r.name.endswith("汤")]
    rows += [fixture_dish("高汤白菜汤", "白菜200克；高汤300克")]
    with client_for(tmp_path, public_catalog(rows), ScriptedLLM([complete_intent()])) as client:
        result = client.post(
            "/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口。" + message)
        ).json()
    assert result["status"] == "no_feasible_menu" and result["menu"] == []


def test_cancel_soup_clears_old_source_counts_but_not_allergy_or_independent_meat(tmp_path: Path):
    llm = ScriptedLLM([complete_intent(people=4, no_spicy=True, allergies=["虾"]), Intent()])
    with client_for(tmp_path, public_catalog(), llm) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=3,
                message="4人晚餐，不辣，虾过敏。四道菜，一道素汤、两道独立荤菜、一道素菜。",
            ),
        ).json()
        final = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message="改为三道菜，不要汤，两道独立荤菜一道素菜",
            ),
        ).json()
    assert final["status"] == "ok" and len(final["menu"]) == 3
    c = final["conversation_state"]["constraints"]
    assert (
        c["soup_count"] == 0 and c["vegetarian_soup_count"] is None and c["meat_soup_count"] is None
    )
    assert (
        c["meat_dish_scope"] == "independent_entree" and c["allergies"] == ["虾"] and c["no_spicy"]
    )


def test_soup_counts_are_not_non_soup_meat_or_vegetarian_quotas(tmp_path: Path):
    with client_for(
        tmp_path, public_catalog(soup_pool()), ScriptedLLM([complete_intent(people=4)])
    ) as client:
        result = client.post(
            "/chat",
            json=dict(
                user_id=3,
                message="4人晚餐，没有其他忌口。总共五道菜，两道独立荤菜一道素菜，加一道素汤一道荤汤。",
            ),
        ).json()
    assert result["status"] == "ok" and len(result["menu"]) == 5
    assert len({r["name"] for r in result["menu"]} & {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}) == 2
    assert sum(r["name"] in {"冬瓜汤", "番茄蛋汤"} for r in result["menu"]) == 1
    assert (
        sum(r["name"] in {"排骨汤", "虾皮冬瓜汤", "素菜汤", "蔬菜汤"} for r in result["menu"]) == 1
    )


def test_small_disjoint_capacity_matches_public_source_truth():
    specifications = [SOURCE_SOUPS[i] for i in (0, 1, 2, 6)]
    recipes = (
        [fixture_dish(n, f, s) for n, f, s, _ in specifications]
        + recipe_pool()[:1]
        + recipe_pool()[4:6]
    )
    truth = {n: kind for n, _, _, kind in specifications}
    for total in range(1, 6):
        for soups in range(min(3, total) + 1):
            for meat in range(soups + 1):
                for veg in range(soups - meat + 1):
                    c = Constraints(
                        dish_count=total,
                        soup_count=soups,
                        meat_soup_count=meat,
                        vegetarian_soup_count=veg,
                    )
                    feasible = any(
                        sum(r.name in truth for r in menu) == soups
                        and sum(truth.get(r.name) == "meat" for r in menu) == meat
                        and sum(truth.get(r.name) == "vegetarian" for r in menu) == veg
                        for menu in combinations(recipes, total)
                    )
                    result = plan_composition(
                        recipes, c, [], choose=lambda options, selected: options[0]
                    )
                    assert (result.failure is None) is feasible
                    if feasible:
                        assert composition_satisfied(result.recipes, c)


@pytest.mark.parametrize(
    "message", ["总共四道菜加一道素汤", "共四道菜和一道荤汤", "合计四道菜+一道素汤"]
)
def test_explicit_absolute_total_and_additional_soup_need_confirmation(message):
    assert explicit_menu_structure(message)[1]


@pytest.mark.parametrize("unit", ["道", "个", "款"])
def test_dish_units_ground_soup_count_but_bowls_do_not(unit):
    from app.agent.menu_structure import explicit_soup_composition

    assert explicit_soup_composition(f"一{unit}素汤") == ({"vegetarian_soup_count": 1}, None)
    assert explicit_soup_composition("八碗素汤") == ({}, None)


def test_unresolved_soup_question_survives_followup_until_confirmed(tmp_path: Path):
    llm = ScriptedLLM([complete_intent(), Intent(), Intent()])
    with client_for(tmp_path, public_catalog(), llm) as client:
        first = client.post(
            "/chat", json=dict(user_id=3, message="1人晚餐，无其他忌口。三道菜，一到两道素汤。")
        ).json()
        sid = first["conversation_state"]["session_id"]
        still = client.post("/chat", json=dict(user_id=3, session_id=sid, message="继续")).json()
        resolved = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="三道菜，其中一道素汤")
        ).json()
    assert first["status"] == still["status"] == "clarification_required" and not still["menu"]
    assert (
        first["conversation_state"]["pending_soup_composition"]
        and still["conversation_state"]["pending_soup_composition"]
    )
    assert (
        resolved["status"] == "ok"
        and not resolved["conversation_state"]["pending_soup_composition"]
    )
    assert any(r["name"] == "冬瓜汤" for r in resolved["menu"])


def test_whole_meal_vegetarian_and_positive_meat_soup_are_a_conflict(tmp_path: Path):
    llm = ScriptedLLM([complete_intent(diet_mode="ovo_lacto_vegetarian")])
    with client_for(tmp_path, public_catalog(), llm) as client:
        result = client.post(
            "/chat",
            json=dict(
                user_id=3, message="1人晚餐，整餐蛋奶素，没有其他忌口。三道菜，其中一道荤汤。"
            ),
        ).json()
    assert result["status"] == "clarification_required" and result["menu"] == []
    assert "整餐素食" in result["reason"]
    assert "荤汤数量" in result["reason"]


def test_source_update_cannot_change_protected_old_soup_when_only_entree_is_replaced(
    tmp_path: Path,
):
    llm = ScriptedLLM([complete_intent(), Intent()])
    with client_for(tmp_path, public_catalog(), llm) as client:
        first = client.post(
            "/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口。三道菜其中一道荤汤。")
        ).json()
        assert first["status"] == "ok" and any(r["name"] == "排骨汤" for r in first["menu"])
        slot = next(r["slot"] for r in first["menu"] if r["name"] != "排骨汤")
        llm.intents = [Intent(action="replace", replace_slot=slot)]
        blocked = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message=f"汤要一道素汤、零道荤汤，但只换第{slot}道，其余不变",
            ),
        ).json()
    assert blocked["status"] == "no_feasible_menu" and blocked["menu"] == []
    assert "仅换指定菜" in blocked["reason"]


def test_source_quantity_kept_in_suggestions_not_just_final_response():
    from app.agent.suggestions import replacement_candidates

    rows = soup_pool()
    by_name = {r.name: r for r in rows}
    c = Constraints(dish_count=2, soup_count=1, vegetarian_soup_count=1)
    menu = [by_name["冬瓜汤"], by_name["蒸鸡肉"]]
    alternatives = replacement_candidates(menu, rows, "soup-source-public", constraints=c)
    assert alternatives and all(r.name == "番茄蛋汤" for r in alternatives)


@pytest.mark.parametrize("message", ["‘一道素汤’", "“五道菜其中两道荤汤”"])
def test_quoted_source_count_does_not_sneak_into_the_total_count_parser(message):
    assert explicit_menu_structure(message) == ({}, None)
