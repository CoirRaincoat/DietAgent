"""Authored whole-meal role expectations, with hard-rule and edit boundaries."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.meal_structure import minimum_role_counts, repair_menu_roles
from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine

FIXTURE = Path(__file__).resolve().parents[1] / "evaluation/cases/meal_structure_v1.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def dish(name: str, food: str, steps: str = "煮熟后装盘。") -> Recipe:
    """Make a finite source-text fixture without user profiles or external calls."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": food, "烹饪步骤": steps, "label": "早餐、午餐、晚餐"}]
            ).values()
        )
    )


def pool() -> list[Recipe]:
    """Provide independent whole-dish roles with two plant protein choices."""
    return [
        dish("清炒白菜", "白菜200克"),
        dish("清炒青菜", "青菜200克"),
        dish("蒸胡萝卜", "胡萝卜200克"),
        dish("炒西兰花", "西兰花200克"),
        dish("清蒸鸡肉", "鸡肉200克"),
        dish("蒸豆腐", "豆腐200克"),
        dish("煮豆干", "豆干200克"),
        dish("煮米饭", "大米200克"),
        dish("冬瓜汤", "冬瓜200克；水1000克"),
        dish("萝卜汤", "萝卜200克；水1000克"),
    ]


def roles(menu: list[Recipe]) -> Counter[str]:
    return Counter(role for recipe in menu for role in recipe.categories)


def ids(menu: list[Recipe]) -> list[str]:
    return [r.recipe_id for r in menu]


def current_menu(candidates: list[Recipe]) -> list[Recipe]:
    """The known structural counterexample: one protein, three vegetables."""
    return [
        candidates[4],
        candidates[0],
        candidates[1],
        candidates[2],
        candidates[7],
        candidates[8],
    ]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_new_menu_meets_contextual_roles_without_counting_soups_as_entrees(
    case: dict[str, Any],
) -> None:
    constraints = Constraints(
        **{k: case[k] for k in ("people", "meal_type", "dish_count", "soup_count")}
    )
    result = MenuPlanner(RuleEngine()).plan(pool(), constraints)
    assert result.failure is None
    assert len(result.recipes) == len(set(ids(result.recipes))) == constraints.dish_count
    counts = roles(result.recipes)
    assert counts["soup"] == constraints.soup_count
    for role, minimum in case["minimum_roles"].items():
        assert counts[role] >= minimum


def test_existing_group_menu_repairs_one_surplus_slot_and_retry_is_stable() -> None:
    candidates = pool()
    current = current_menu(candidates)
    constraints = Constraints(people=5, dish_count=6, soup_count=1)
    planner = MenuPlanner(RuleEngine())
    result = planner.plan(candidates, constraints, current=current)
    assert result.failure is None
    assert roles(result.recipes) == {"protein": 2, "vegetable": 2, "staple": 1, "soup": 1}
    assert len(result.changes) == 1
    assert result.recipes[5].recipe_id == current[5].recipe_id
    again = planner.plan(list(reversed(candidates)), constraints, current=result.recipes)
    assert ids(again.recipes) == ids(result.recipes)
    assert again.changes == []


def test_new_people_requirement_changes_existing_single_diner_menu() -> None:
    candidates = pool()
    current = current_menu(candidates)
    planner = MenuPlanner(RuleEngine())
    solo = planner.plan(
        candidates, Constraints(people=1, dish_count=6, soup_count=1), current=current
    )
    assert ids(solo.recipes) == ids(current)
    shared = planner.plan(
        candidates, Constraints(people=5, dish_count=6, soup_count=1), current=solo.recipes
    )
    assert roles(shared.recipes)["protein"] == 2
    assert len(shared.changes) == 1


def test_explicit_replacement_never_repairs_an_unrelated_structural_gap() -> None:
    candidates = pool()
    current = current_menu(candidates)
    result = MenuPlanner(RuleEngine()).plan(
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1),
        current=current,
        replace_slot=1,
    )
    assert ids(result.recipes)[1:] == ids(current)[1:]
    assert [change["slot"] for change in result.changes] == [1]
    assert roles(result.recipes)["protein"] == 1
    assert any("角色" in warning and "蛋白质" in warning for warning in result.warnings)


def test_structural_repair_preserves_already_covered_ingredient_preference() -> None:
    candidates = pool()
    current = current_menu(candidates)
    result = MenuPlanner(RuleEngine()).plan(
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1, preferred_ingredients=["胡萝卜"]),
        current=current,
    )
    assert current[3].recipe_id in ids(result.recipes)
    assert roles(result.recipes)["protein"] == 2
    assert len(result.changes) == 1


def test_structural_repair_does_not_reintroduce_rejected_or_spicy_recipe() -> None:
    candidates = pool()
    current = current_menu(candidates)
    spicy = dish("油泼豆干", "豆干200克；油泼辣子10克")
    result = MenuPlanner(RuleEngine()).plan(
        [spicy, *candidates],
        Constraints(people=5, dish_count=6, soup_count=1, no_spicy=True),
        current=current,
        reject_ids={candidates[5].recipe_id},
    )
    assert roles(result.recipes)["protein"] == 2
    assert candidates[6].recipe_id in ids(result.recipes)
    assert spicy.recipe_id not in ids(result.recipes)
    assert candidates[5].recipe_id not in ids(result.recipes)


def test_plant_only_catalog_can_meet_shared_protein_role_without_forcing_meat() -> None:
    candidates = [r for r in pool() if not any("鸡肉" in i.name for i in r.ingredients)]
    result = MenuPlanner(RuleEngine()).plan(
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1),
    )
    assert roles(result.recipes)["protein"] == 2
    assert all("鸡肉" not in r.raw_ingredients for r in result.recipes)


def test_insufficient_safe_roles_warn_instead_of_claiming_full_balance() -> None:
    candidates = pool()
    current = current_menu(candidates)
    candidates = [
        r
        for r in candidates
        if r.recipe_id not in {candidates[5].recipe_id, candidates[6].recipe_id}
    ]
    result = MenuPlanner(RuleEngine()).plan(
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1),
        current=current,
    )
    assert result.failure is None
    assert ids(result.recipes) == ids(current)
    assert any("角色" in warning and "蛋白质" in warning for warning in result.warnings)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_default_role_minima_fit_only_non_soup_slots(case: dict[str, Any]) -> None:
    constraints = Constraints(
        **{k: case[k] for k in ("people", "meal_type", "dish_count", "soup_count")}
    )
    minimum = minimum_role_counts(constraints.dish_count, constraints)
    assert minimum == case["minimum_roles"]
    assert sum(minimum.values()) <= constraints.dish_count - constraints.soup_count


def test_empty_and_invalid_role_targets_are_explicit() -> None:
    assert minimum_role_counts(0) == {"vegetable": 0, "protein": 0, "staple": 0}
    assert minimum_role_counts(4) == {"vegetable": 2, "protein": 1, "staple": 1}
    with pytest.raises(ValueError, match="negative"):
        minimum_role_counts(-1)


def test_multiple_missing_roles_are_repaired_without_churning_on_retry() -> None:
    candidates = pool()
    extra = dish("清炒花菜", "花菜200克")
    current = [*candidates[:4], extra, candidates[8]]
    constraints = Constraints(people=5, dish_count=6, soup_count=1)
    planner = MenuPlanner(RuleEngine())
    result = planner.plan([*candidates, extra], constraints, current=current)
    assert roles(result.recipes) == {"protein": 2, "vegetable": 2, "staple": 1, "soup": 1}
    assert len(result.changes) == 3
    assert result.recipes[5] == current[5]
    assert planner.plan(candidates, constraints, current=result.recipes).changes == []


def test_covered_food_preferences_can_prevent_structural_repair() -> None:
    candidates = pool()
    current = current_menu(candidates)
    result = MenuPlanner(RuleEngine()).plan(
        candidates,
        Constraints(
            people=5, dish_count=6, soup_count=1, preferred_ingredients=["白菜", "青菜", "胡萝卜"]
        ),
        current=current,
    )
    assert ids(result.recipes) == ids(current)
    assert any("未能补齐" in warning for warning in result.warnings)


def test_already_changed_slot_is_reused_before_touching_another_confirmed_slot() -> None:
    candidates = pool()
    current = current_menu(candidates)
    original = list(current)
    original[3] = candidates[3]
    repair = repair_menu_roles(
        current,
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1),
        original=original,
        scores={},
        order={r.recipe_id: i for i, r in enumerate(candidates)},
        food_matches=lambda recipe, term: bool(RuleEngine().food_matches(recipe, term)),
    )
    assert repair.changed_indices == {3}
    assert repair.gaps == {}


def test_higher_rule_score_wins_among_feasible_role_repairs() -> None:
    candidates = pool()
    current = current_menu(candidates)
    repair = repair_menu_roles(
        current,
        candidates,
        Constraints(people=5, dish_count=6, soup_count=1),
        original=current,
        scores={candidates[6].recipe_id: 5.0},
        order={},
        food_matches=lambda recipe, term: bool(RuleEngine().food_matches(recipe, term)),
    )
    assert candidates[6].recipe_id in ids(repair.recipes)
    assert len(repair.changed_indices) == 1


def test_balance_explains_a_partial_protein_gap_instead_of_no_protein_claim() -> None:
    current = current_menu(pool())
    balance = analyze_menu_balance(current, Constraints(people=5, dish_count=6, soup_count=1))
    assert balance.status != "balanced"
    assert any("蛋白质" in gap and "还缺 1 道" in gap for gap in balance.gaps)
    assert "未识别到蛋白质来源菜品。" not in balance.gaps


class StructureLLM(BaseLLM):
    """Known intents and minimal explanation selection, with no provider call."""

    def __init__(self, intents: list[Intent]) -> None:
        self.intents = list(intents)

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        return self.intents.pop(0)

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def profile() -> UserProfile:
    """Use a publicly defined synthetic user, not one of the private profiles."""
    return UserProfile(
        data_scope="synthetic",
        user_id=900001,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )


@pytest.mark.parametrize("has_second_protein", [False, True])
def test_http_context_update_survives_restart_and_required_gap_explanation(
    tmp_path: Path,
    has_second_protein: bool,
) -> None:
    candidates = pool()
    if not has_second_protein:
        candidates = [
            r
            for r in candidates
            if r.recipe_id not in {candidates[5].recipe_id, candidates[6].recipe_id}
        ]
    user = profile()
    catalog = DataCatalog({user.user_id: user}, {r.recipe_id: r for r in candidates}, {})
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    llm = StructureLLM(
        [
            Intent(
                people=1,
                meal_type="晚餐",
                dish_count=6,
                soup_count=1,
                no_spicy=True,
                restrictions_confirmed=True,
            ),
            Intent(people=5),
        ]
    )
    store = SessionStore(settings.database_path)
    with TestClient(create_app(settings, llm, catalog, store)) as client:
        first = client.post(
            "/chat",
            json={"user_id": user.user_id, "message": "1人晚餐六道含一汤，不辣，无其他忌口"},
        ).json()
        sid = first["conversation_state"]["session_id"]
        result = client.post(
            "/chat", json={"user_id": user.user_id, "session_id": sid, "message": "改为5个人吃"}
        ).json()
    menu = [catalog.recipes[r["recipe_id"]] for r in result["menu"]]
    assert roles(menu)["protein"] == (2 if has_second_protein else 1)
    assert result["status"] == "ok"
    if not has_second_protein:
        assert "当前菜单仍缺" in result["reason"]
        assert "蛋白质来源菜 1 道" in result["reason"]
    restart_llm = StructureLLM([Intent()])
    with TestClient(
        create_app(settings, restart_llm, catalog, SessionStore(settings.database_path))
    ) as client:
        retry = client.post(
            "/chat", json={"user_id": user.user_id, "session_id": sid, "message": "仍按刚才的要求"}
        ).json()
    assert [r["recipe_id"] for r in retry["menu"]] == ids(menu)
    assert "当前菜单无需调整" in retry["reason"]
