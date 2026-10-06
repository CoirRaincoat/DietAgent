"""Explicit meat-body quotas, not garnish, servings or clinical adequacy."""

from itertools import combinations
from pathlib import Path

import pytest

from app.agent.dish_composition import plan_composition
from app.domain.dish_composition import composition_counts, composition_satisfied, dish_kind
from app.domain.models import Constraints, Intent, UserProfile
from app.infrastructure.data import DataCatalog
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_dish_composition import fixture_dish, recipe_pool

SOURCE_CASES = [
    ("蒸鸡肉", "鸡肉200克；盐1克", "meat", "meat"),
    ("清蒸鲈鱼", "鲈鱼200克；盐1克", "meat", "meat"),
    ("蒸虾仁", "虾仁200克；盐1克", "meat", "meat"),
    ("肉末白菜", "白菜200克；猪肉末20克；盐1克", "meat", "other"),
    ("鸡汤白菜", "白菜200克；鸡汤10克；盐1克", "soup", "soup"),
    ("虾皮鸡蛋羹", "鸡蛋200克；虾皮5克；水100克", "meat", "other"),
    ("猪油蒸蛋", "鸡蛋100克；猪油5克", "meat", "other"),
    ("蒸豆腐", "豆腐200克；盐1克", "vegetarian", "vegetarian"),
    ("蒸鸡蛋", "鸡蛋100克；水100克", "vegetarian", "vegetarian"),
    ("素鸡", "素鸡200克；盐1克", "other", "other"),
    ("排骨汤", "排骨200克；水500克", "soup", "soup"),
    ("肉末米饭", "大米100克；猪肉末20克；水200克", "other", "other"),
]


@pytest.mark.parametrize("name,foods,legacy,independent", SOURCE_CASES)
@pytest.mark.parametrize("scope", ["any_meat_source", "independent_entree"])
def test_explicit_scope_counts_body_not_animal_flavoring(name, foods, legacy, independent, scope):
    recipe = fixture_dish(name, foods)
    constraints = Constraints(meat_dish_scope=scope)
    assert dish_kind(recipe, constraints) == (legacy if scope == "any_meat_source" else independent)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("两道独立荤菜一道素菜", True),
        ("两道独立的荤菜、一道素汤", True),
        ("0道独立荤菜三道素菜", True),
        ("两道荤菜一道独立素菜", False),
        ("两道荤菜一道素菜", False),
        ("不要两道独立荤菜", False),
        ("两到三道独立荤菜", False),
        ("两道独立荤菜还是三道独立荤菜？", False),
        ("解释上次的两道独立荤菜", False),
        ("例如‘两道独立荤菜’", False),
        ("两道独立荤汤", False),
    ],
)
def test_only_confirmed_literal_independent_meat_count_ground_scope(message, expected):
    from app.agent.dish_composition import independent_meat_requested

    assert independent_meat_requested(message) is expected


def public_catalog(recipes=None):
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    rows = recipe_pool() if recipes is None else recipes
    return DataCatalog(
        {3: profile, 4: profile.model_copy(update={"user_id": 4})},
        {r.recipe_id: r for r in rows},
        {},
    )


def names(result):
    return [r["name"] for r in result["menu"]]


def test_actual_chat_scope_survives_replay_restart_continue_plain_count_and_local_edit(
    tmp_path: Path,
):
    catalog = public_catalog()
    llm = ScriptedLLM([complete_intent(people=4, no_spicy=True)])
    request = dict(
        user_id=3,
        request_id="independent-meat-first",
        message="4人晚餐，没有其他忌口，不辣。总共四道菜，两道独立荤菜、一道素菜、一道汤。",
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=request).json()
        sid = first["conversation_state"]["session_id"]
        replay = client.post("/chat", json=dict(request, session_id=sid)).json()
    assert first["status"] == "ok" and replay == first and llm.parse_calls == 1
    assert len(names(first)) == 4 and len(set(names(first)) & {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}) == 2
    assert "肉末白菜" not in names(first)
    assert "独立荤菜" in first["reason"] and "份量" in first["reason"]
    restarted = ScriptedLLM(
        [Intent(action="explain"), Intent(), Intent(), Intent(action="replace", replace_slot=2)]
    )
    with client_for(tmp_path, catalog, restarted) as client:
        explain = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="解释这餐")
        ).json()
        continued = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="继续")
        ).json()
        ordinary = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="仍然两道荤菜一道素菜")
        ).json()
        local = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只换第二道，其他保持不变")
        ).json()
        other_user = client.post("/chat", json=dict(user_id=4, session_id=sid, message="继续"))
    for result in (first, explain, continued, ordinary, local):
        assert result["status"] == "ok"
        assert (
            result["conversation_state"]["constraints"]["meat_dish_scope"] == "independent_entree"
        )
        assert result["conversation_state"]["constraints"]["no_spicy"] is True
        assert len(set(names(result)) & {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}) == 2
    assert names(first) == names(explain) == names(continued) == names(ordinary)
    assert all(a == b for i, (a, b) in enumerate(zip(names(ordinary), names(local))) if i != 1)
    assert names(ordinary)[1] != names(local)[1] and other_user.status_code == 409


def test_safe_candidate_shortage_does_not_fill_independent_quota_with_garnish(tmp_path: Path):
    rows = [r for r in recipe_pool() if r.name not in {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}]
    rows += [
        fixture_dish("蒸虾仁", "虾仁200克；盐1克"),
        fixture_dish("辣椒鸡肉", "鸡肉200克；辣椒20克"),
    ]
    llm = ScriptedLLM([complete_intent(people=3, no_spicy=True, allergies=["虾"])])
    with client_for(tmp_path, public_catalog(rows), llm) as client:
        result = client.post(
            "/chat",
            json=dict(
                user_id=3,
                message="3人晚餐，虾过敏，不辣。总共三道菜，一道独立荤菜、一道素菜、一道汤。",
            ),
        ).json()
    assert result["status"] == "no_feasible_menu" and result["menu"] == []
    assert result["conversation_state"]["constraints"]["allergies"] == ["虾"]
    assert result["conversation_state"]["constraints"]["meat_dish_scope"] == "independent_entree"


def test_legacy_snapshots_keep_old_definition_and_garnish_never_becomes_vegetarian():
    legacy = Constraints.model_validate(
        {"dish_count": 2, "meat_dish_count": 1, "vegetarian_dish_count": 1}
    )
    recipes = {r.name: r for r in recipe_pool()}
    menu = [recipes["肉末白菜"], recipes["清炒白菜"]]
    assert legacy.meat_dish_scope == "any_meat_source" and composition_satisfied(menu, legacy)
    strict = legacy.model_copy(update={"meat_dish_scope": "independent_entree"})
    assert not composition_satisfied(menu, strict)
    assert composition_counts(menu, strict) == {"other": 1, "vegetarian": 1}


def test_independent_zero_is_not_a_vegetarian_or_allergen_waiver():
    recipe = fixture_dish("肉末白菜", "白菜200克；猪肉末20克")
    c = Constraints(meat_dish_scope="independent_entree", meat_dish_count=0)
    assert composition_satisfied([recipe], c)
    assert not composition_satisfied([recipe], c.model_copy(update={"vegetarian_dish_count": 1}))


def test_small_capacity_solver_agrees_with_authored_principal_names():
    recipes = recipe_pool()[:8]
    principal = {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}
    veggie = {"清炒白菜", "清炒青菜", "蒸豆腐", "炒鸡蛋"}
    for total in range(1, 5):
        for meat in range(total + 1):
            for veg in range(total - meat + 1):
                c = Constraints(
                    dish_count=total,
                    meat_dish_count=meat,
                    vegetarian_dish_count=veg,
                    meat_dish_scope="independent_entree",
                )
                feasible = any(
                    sum(r.name in principal for r in m) == meat
                    and sum(r.name in veggie for r in m) == veg
                    for m in combinations(recipes, total)
                )
                result = plan_composition(
                    recipes, c, [], choose=lambda options, selected: options[0]
                )
                assert (result.failure is None) is feasible
                if feasible:
                    assert sum(r.name in principal for r in result.recipes) == meat
                    assert sum(r.name in veggie for r in result.recipes) == veg


@pytest.mark.parametrize(
    "name,foods,expected",
    [
        ("肉末炒白菜", "白菜200克；猪肉末20克", False),
        ("肉末香菇", "香菇200克；猪肉末20克", False),
        ("肉末蒸豆腐", "豆腐200克；猪肉末20克", False),
        ("肉末蒸蛋", "鸡蛋100克；猪肉末20克", False),
        ("肉末蒸肉饼", "猪肉末200克；盐1克", True),
        ("蒸肉末", "肉末200克；盐1克", True),
        ("清蒸鸡肉", "鸡汤200克；盐1克", False),
    ],
)
def test_finite_garnish_body_boundary_does_not_ban_minced_meat_mains(name, foods, expected):
    from app.domain.entree_preferences import independent_meat_entree_reference

    assert independent_meat_entree_reference(fixture_dish(name, foods)) is expected


def test_new_independent_scope_cannot_expand_only_one_slot_authority(tmp_path: Path):
    catalog = public_catalog()
    llm = ScriptedLLM([complete_intent(), Intent(action="replace", replace_slot=2)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=3, message="1人晚餐，没有其他忌口，不辣。总共三道，两道荤菜一道素菜。"
            ),
        ).json()
        assert first["status"] == "ok"
        assert "肉末白菜" in names(first)
        garnish_slot = names(first).index("肉末白菜")
        target_slot = next(
            i for i, n in enumerate(names(first)) if n in {"蒸鸡肉", "蒸牛肉", "蒸猪肉"}
        )
        llm.intents = [Intent(action="replace", replace_slot=target_slot + 1)]
        updated = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=first["conversation_state"]["session_id"],
                message=f"改为两道独立荤菜一道素菜，但只换第{target_slot + 1}道，其他保持不变",
            ),
        ).json()
    assert garnish_slot != target_slot
    assert updated["status"] == "no_feasible_menu" and updated["menu"] == []
    assert "仅换指定菜" in updated["reason"]
    assert updated["conversation_state"]["constraints"]["meat_dish_scope"] == "independent_entree"


def test_suggestions_cannot_replace_principal_meat_with_same_role_garnish():
    from app.agent.suggestions import replacement_candidates

    rows = recipe_pool()
    by_name = {r.name: r for r in rows}
    c = Constraints(
        dish_count=2,
        meat_dish_count=1,
        vegetarian_dish_count=1,
        meat_dish_scope="independent_entree",
    )
    menu = [by_name["蒸鸡肉"], by_name["清炒青菜"]]
    suggestions = replacement_candidates(menu, rows, "principal-reference", limit=5, constraints=c)
    assert suggestions and all(r.name in {"蒸牛肉", "蒸猪肉"} for r in suggestions)
