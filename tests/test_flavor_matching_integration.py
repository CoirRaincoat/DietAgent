"""Actual menu/transport acceptance for new positive flavor requests."""

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.agent.response_copy import required_fact_ids, response_facts
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine


def dish(name: str, food: str, label: str = "午餐、晚餐") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": food,
                        "烹饪步骤": "炒熟装盘。",
                        "label": label,
                    }
                ]
            ).values()
        )
    )


@pytest.fixture(scope="module")
def original_pair() -> tuple[Recipe, Recipe]:
    root = Path(__file__).resolve().parents[1]
    with (root / "dataset/recipe_kb/recipes_sample_2000.csv").open(
        encoding="gb18030", newline=""
    ) as stream:
        originals = {r.source_row: r for r in normalize_recipes(csv.DictReader(stream)).values()}
    old, sour = originals[320], originals[169]
    assert old.name == "奶汁烤大白菜" and sour.name == "醋溜白菜"
    assert "猪肉片" in old.raw_ingredients
    assert "陈醋" in sour.raw_ingredients and "酸" in sour.raw_label
    return old, sour


def test_added_sour_preference_changes_source_menu_not_just_constraints(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["酸"]),
        current=[old],
    )
    assert result.failure is None
    assert [r.recipe_id for r in result.recipes] == [sour.recipe_id]
    assert any("口味" in change["reason"] for change in result.changes)


def test_source_flavor_missing_disclosed_without_breaking_explicit_meat_quota(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["酸"], meat_dish_count=1),
        current=[old],
    )
    assert result.failure is None and result.recipes == [old]
    assert any("酸" in warning and "口味" in warning for warning in result.warnings)


def test_original_sour_recipe_with_dry_chili_cannot_waive_no_spicy(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    assert "干辣椒" in sour.raw_ingredients
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["酸"], no_spicy=True),
        current=[old],
    )
    assert result.failure is None and result.recipes == [old]
    assert any("酸" in warning and "口味" in warning for warning in result.warnings)


def test_empty_continue_does_not_undo_accepted_menu_for_old_flavor(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["酸"]),
        current=[old],
        recheck_soft_preferences=False,
    )
    assert result.recipes == [old] and result.changes == []
    assert any("酸" in warning and "口味" in warning for warning in result.warnings)


def test_phrase_and_alias_source_metadata_reach_initial_ranking() -> None:
    plain = dish("炒白菜", "白菜200克；盐1克")
    reference = dish("糖醋白菜", "白菜200克；白糖2克；醋10克；盐1克")
    constraints = Constraints(dish_count=1, preferences=["想吃酸甜"])
    rules = RuleEngine()
    assert recipe_relevance_score(reference, [], constraints, rules) > recipe_relevance_score(
        plain, [], constraints, rules
    )
    result = MenuPlanner(rules).plan([plain, reference], constraints)
    assert result.recipes == [reference]


def test_garlic_presence_does_not_self_certify_garlic_flavor() -> None:
    weak = dish("炒白菜", "白菜200克；大蒜2克；盐1克")
    result = MenuPlanner(RuleEngine()).plan(
        [weak],
        Constraints(dish_count=1, preferences=["蒜香"]),
    )
    assert result.failure is None
    assert any("蒜香" in warning and "口味" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "preferences,missing",
    [
        (["咖喱味"], "咖喱味"),
        (["不要酸"], "不要酸"),
        (["酸", "不要酸"], "冲突"),
    ],
)
def test_unknown_negative_and_conflicting_flavor_not_hidden_as_no_adjustment(
    original_pair: tuple[Recipe, Recipe],
    preferences: list[str],
    missing: str,
) -> None:
    old, sour = original_pair
    constraints = Constraints(dish_count=1, preferences=preferences)
    result = MenuPlanner(RuleEngine()).plan([old, sour], constraints, current=[old])
    if missing == "冲突":
        assert result.failure and "冲突" in result.failure and result.recipes == []
        return
    assert result.failure is None
    assert any(missing in warning for warning in result.warnings)
    facts = response_facts(
        intent=Intent(action="plan", preferences=preferences),
        constraints=constraints,
        diners=[],
        previous=[old],
        chosen=result.recipes,
        balance=analyze_menu_balance(result.recipes),
    )
    assert "flavor_preferences" in required_fact_ids(Intent(), facts)
    assert missing in facts["flavor_preferences"]
    assert "当前菜单无需调整" not in facts["opening"]
    if "不要酸" in preferences:
        assert "口味来源参考已覆盖：酸" not in facts["flavor_preferences"]


def test_contrast_clause_reaches_actual_source_menu(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    result = MenuPlanner(RuleEngine()).plan(
        [old, sour],
        Constraints(dish_count=1, preferences=["不要甜但喜欢酸"]),
        current=[old],
    )
    assert result.failure is None and result.recipes == [sour]


def test_accumulated_negative_cannot_leave_old_flavor_ranking_credit(
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    rules = RuleEngine()
    base = Constraints(dish_count=1)
    conflict = base.model_copy(update={"preferences": ["酸", "不要酸"]})
    assert recipe_relevance_score(sour, [], conflict, rules) == recipe_relevance_score(
        sour, [], base, rules
    )
    assert recipe_relevance_score(old, [], conflict, rules) == recipe_relevance_score(
        old, [], base, rules
    )


def test_flavor_repair_cannot_waive_spicy_allergy_or_ingredient_preference() -> None:
    old = dish("炒白菜", "白菜200克；大蒜2克；盐1克")
    spicy = dish("蒜香白菜", "白菜200克；辣椒3克；盐1克")
    allergen = dish("蒜香青菜", "青菜200克；花生油5克；盐1克")
    loses_food = dish("蒜香菠菜", "菠菜200克；大蒜2克；盐1克")
    result = MenuPlanner(RuleEngine()).plan(
        [old, spicy, allergen, loses_food],
        Constraints(
            dish_count=1,
            preferences=["蒜香"],
            preferred_ingredients=["白菜"],
            no_spicy=True,
            allergies=["花生"],
        ),
        current=[old],
    )
    assert result.failure is None and result.recipes == [old]
    assert any("蒜香" in warning and "口味" in warning for warning in result.warnings)


def test_replacement_suggestions_do_not_drop_uniquely_covered_flavor() -> None:
    target = dish("蒜香白菜", "白菜200克；大蒜2克；盐1克")
    other = dish("炒菠菜", "菠菜200克；盐1克")
    preserves = dish("蒜香青菜", "青菜200克；大蒜2克；盐1克")
    options = replacement_candidates(
        [target],
        [other, preserves],
        "synthetic-flavor-suggestions",
        limit=2,
        constraints=Constraints(dish_count=1, preferences=["蒜香"]),
        rules=RuleEngine(),
    )
    assert options == [preserves]


class FlavorLLM(BaseLLM):
    """Explicit scripted extraction, not a real language-model quality test."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "酸" in message:
            return Intent(action="plan", preferences=["酸"])
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
            query_terms=["奶汁"],
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def make_app(tmp_path: Path, pair: tuple[Recipe, Recipe], llm: BaseLLM | None = None) -> Any:
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    settings = Settings.model_construct(session_db=tmp_path / "flavor.db")
    return create_app(
        settings,
        llm or FlavorLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in pair}, {}),
        SessionStore(settings.database_path),
    )


def test_minimal_explanation_cannot_hide_unmet_flavor_or_waive_no_spicy(
    tmp_path: Path,
    original_pair: tuple[Recipe, Recipe],
) -> None:
    class NonSpicyFlavorLLM(FlavorLLM):
        async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
            intent = await super().parse(message, state, profile)
            return intent.model_copy(update={"no_spicy": True})

    old, _sour = original_pair
    with TestClient(make_app(tmp_path, original_pair, NonSpicyFlavorLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐要1道奶汁白菜，不辣，无其他忌口。"}
        ).json()
        update = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": first["conversation_state"]["session_id"],
                "message": "想吃酸的，继续保留不辣。",
            },
        ).json()
    assert update["status"] == "ok"
    assert [r["recipe_id"] for r in update["menu"]] == [old.recipe_id]
    assert "酸" in update["reason"] and "尚未覆盖" in update["reason"]
    assert "当前菜单无需调整" not in update["reason"]


def test_http_accumulated_positive_negative_conflict_is_mandatory_disclosure(
    tmp_path: Path,
    original_pair: tuple[Recipe, Recipe],
) -> None:
    class ConflictLLM(FlavorLLM):
        async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
            if "不要酸" in message:
                return Intent(action="plan", preferences=["不要酸"])
            return await super().parse(message, state, profile)

    with TestClient(make_app(tmp_path, original_pair, ConflictLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道奶汁白菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        client.post(
            "/chat",
            json={"user_id": 900001, "session_id": sid, "message": "这餐想吃酸的，可以调整。"},
        )
        update = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "现在不要酸。"}
        ).json()
    assert update["status"] == "clarification_required" and update["menu"] == []
    assert update["conversation_state"]["constraints"]["preferences"] == ["酸", "不要酸", "现在不要酸"]
    assert "冲突" in update["reason"] and "不" in update["reason"]
    assert "口味来源参考已覆盖：酸" not in update["reason"]
    assert "当前菜单无需调整" not in update["reason"]


def test_http_update_and_restart_execute_source_flavor_request(
    tmp_path: Path,
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    with TestClient(make_app(tmp_path, original_pair)) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐要1道奶汁白菜，无其他忌口。"}
        ).json()
        assert [r["recipe_id"] for r in first["menu"]] == [old.recipe_id]
        sid = first["conversation_state"]["session_id"]
        update = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "这餐现在想吃酸的，可以调整，其他不变。",
            },
        ).json()
    assert update["status"] == "ok"
    assert [r["recipe_id"] for r in update["menu"]] == [sour.recipe_id]
    with TestClient(make_app(tmp_path, original_pair)) as client:
        retry = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
        ).json()
    assert retry["status"] == "ok"
    assert [r["recipe_id"] for r in retry["menu"]] == [sour.recipe_id]


def test_sse_added_flavor_request_renders_actual_changed_source_dish(
    tmp_path: Path,
    original_pair: tuple[Recipe, Recipe],
) -> None:
    old, sour = original_pair
    with TestClient(make_app(tmp_path, original_pair)) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐要1道奶汁白菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "session_id": sid,
                "messages": [{"role": "user", "content": "这餐想吃酸的，可以调整。"}],
            },
        )
    assert response.status_code == 200
    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "") for event in events[:-1]
    )
    # The explanation may honestly name the old dish being replaced. Only
    # the reconstructed selected-menu section must exclude it.
    menu_text = text.split("\n\n", 1)[0]
    assert sour.name in menu_text and old.name not in menu_text
