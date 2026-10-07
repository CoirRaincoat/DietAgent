"""Hand-authored non-spicy counterexamples independent of production vocabulary."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.api.presentation import recipe_provenance
from app.domain.models import Constraints, Ingredient, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine
from evaluation.no_spicy_oracle import no_spicy_findings
from evaluation.real_data import check_allergens
from evaluation.regression_suite import evaluate_turn


def dish(food: str = "白菜", *, labels: list[str] | None = None) -> Recipe:
    """Create explicit culinary evidence without clinical or private data."""
    return Recipe(
        recipe_id="acceptance-dish",
        name="蒸白菜",
        raw_ingredients=food,
        ingredients=[Ingredient(raw=food, name=food)],
        steps="蒸熟装盘。",
        labels=labels or [],
        raw_label="、".join(labels or []),
        categories=["vegetable"],
        methods=["蒸"],
        source_row=2,
        fingerprint="acceptance",
    )


@pytest.mark.parametrize("food", ["小米辣", "油泼辣子", "辣子油", "糍粑辣子", "辣椒面"])
@pytest.mark.parametrize("origin", ["ingredients", "steps"])
def test_explicit_spicy_evidence_is_rejected(food: str, origin: str) -> None:
    recipe = dish(food if origin == "ingredients" else "白菜")
    if origin == "steps":
        recipe.steps = f"蒸熟后可选加入{food}。"
    decision = RuleEngine().evaluate(recipe, Constraints(no_spicy=True))
    assert not decision.allowed
    assert food in " ".join(decision.reasons)
    assert RuleEngine().evaluate(recipe, Constraints()).allowed


@pytest.mark.parametrize("label", ["辣", "微辣", "香辣", "酸辣"])
def test_explicit_flavor_label_is_conservative_rejection(label: str) -> None:
    decision = RuleEngine().evaluate(dish(labels=[label]), Constraints(no_spicy=True))
    assert not decision.allowed
    assert "辣味标签" in " ".join(decision.reasons)


@pytest.mark.parametrize("label", ["不辣", "清淡", "甜", "黑椒味", "辣味已去除"])
def test_labels_are_exact_not_substrings(label: str) -> None:
    assert RuleEngine().evaluate(dish(labels=[label]), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("food", ["白胡椒", "黑胡椒", "甜椒", "彩椒", "白菜"])
def test_non_chili_foods_are_not_blanket_banned(food: str) -> None:
    assert RuleEngine().evaluate(dish(food), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize("food", ["火锅底料", "酱料包", "调味料", "未知配料"])
def test_unknown_seasoning_cannot_be_claimed_non_spicy(food: str) -> None:
    decision = RuleEngine().evaluate(dish(food), Constraints(no_spicy=True))
    assert not decision.allowed
    assert "无法确认辣味" in " ".join(decision.reasons)
    assert RuleEngine().evaluate(dish(food), Constraints()).allowed


def test_unparsed_food_cannot_be_claimed_non_spicy() -> None:
    recipe = dish()
    recipe.quality_flags = ["unparsed_ingredients"]
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_no_spicy_revalidates_existing_slot_and_preserves_unrelated_slot() -> None:
    spicy = dish("油泼辣子")
    retained = dish().model_copy(update={"recipe_id": "retained", "name": "蒸青菜"})
    replacement = dish().model_copy(update={"recipe_id": "replacement", "name": "蒸南瓜"})
    planned = MenuPlanner(RuleEngine()).plan(
        [spicy, retained, replacement],
        Constraints(no_spicy=True, dish_count=2),
        current=[spicy, retained],
        replace_slot=1,
    )
    assert planned.failure is None
    assert [r.recipe_id for r in planned.recipes] == ["replacement", "retained"]


@pytest.mark.parametrize("food", ["小米辣", "油泼辣子"])
@pytest.mark.parametrize("location", ["menu", "replacement_suggestions"])
def test_independent_oracle_does_not_trust_suitability_or_incomplete_case_terms(
    food: str, location: str
) -> None:
    recipe = dish(food)
    item: dict[str, Any] = {
        "recipe_id": recipe.recipe_id,
        "name": recipe.name,
        "ingredients": ["白菜"],  # Even lying visible ingredients cannot hide the source.
        "provenance": recipe_provenance(recipe).model_dump(),
    }
    result: dict[str, Any] = {
        "menu": [item],
        "replacement_suggestions": [],
        "conversation_state": {"constraints": {"no_spicy": False}},
        "diner_suitability": [{"hard_constraints_satisfied": True}],
    }
    if location == "replacement_suggestions":
        safe = dish().model_copy(update={"recipe_id": "safe"})
        result["menu"] = [
            dict(item, recipe_id="safe", provenance=recipe_provenance(safe).model_dump())
        ]
        result["replacement_suggestions"] = [item]
        catalog = {recipe.recipe_id: recipe, safe.recipe_id: safe}
    else:
        catalog = {recipe.recipe_id: recipe}
    checks = evaluate_turn(
        result,
        {
            "constraints": {"no_spicy": True},
            "catalog_traceability": True,
            "independent_food_rules": [],
        },
        previous_menu_ids=None,
        recipes=catalog,
    )
    oracle = next(c for c in checks if c["check"] == "independent_food_constraints")
    assert not oracle["passed"]
    assert any(f["location"] == location and food in f["matched"] for f in oracle["actual"])


@pytest.mark.parametrize("food", ["小米辣", "油泼辣子"])
def test_private_source_oracle_also_rejects_missing_aliases(food: str) -> None:
    assert check_allergens(food, [], no_spicy=True)["status"] == "FAIL"


def test_private_oracle_rejects_spicy_label_and_unknown_compound() -> None:
    assert check_allergens("白菜", [], True, labels=["辣"])["status"] == "FAIL"
    assert check_allergens("白菜和酱料包", [], True)["failures"] == [
        "unverified_no_spicy_composite"
    ]
    assert check_allergens("白菜和酱料包", [], False)["status"] == "PASS"


def test_oracle_is_independent_of_removed_production_vocabulary() -> None:
    rules = RuleEngine()
    rules.config["spicy_terms"] = []
    rules.config["spicy_labels"] = []
    assert rules.evaluate(dish("油泼辣子"), Constraints(no_spicy=True)).allowed
    assert no_spicy_findings("油 泼 辣 子", ["辣"]) == ["油泼辣子", "辣味标签:辣"]
    assert no_spicy_findings("", []) == []
    assert no_spicy_findings("黑胡椒和甜椒", ["不辣", "黑椒味"]) == []


def test_legacy_config_keeps_positive_flavor_label_screening() -> None:
    rules = RuleEngine()
    rules.config.pop("spicy_labels")
    assert not rules.evaluate(dish(labels=["微辣"]), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize(
    "origin", ["catalog_label", "visible_steps", "visible_badges", "unknown_source"]
)
def test_public_oracle_audits_positive_labels_and_visible_evidence(origin: str) -> None:
    recipe = dish("酱料包" if origin == "unknown_source" else "白菜")
    if origin == "catalog_label":
        recipe.raw_label = "微辣、晚餐"
    item: dict[str, Any] = {
        "recipe_id": recipe.recipe_id,
        "name": recipe.name,
        "ingredients": ["白菜"],
        "steps": "蒸熟。",
        "provenance": recipe_provenance(recipe).model_dump(),
        "card": {"badges": []},
    }
    if origin == "visible_steps":
        item["steps"] += "加入小米辣。"
    if origin == "visible_badges":
        item["card"]["badges"] = ["辣"]
    result = {"menu": [item], "conversation_state": {}, "diner_suitability": []}
    checks = evaluate_turn(
        result,
        {"constraints": {"no_spicy": True}, "catalog_traceability": True},
        previous_menu_ids=None,
        recipes={recipe.recipe_id: recipe},
    )
    oracle = next(c for c in checks if c["check"] == "independent_food_constraints")
    assert not oracle["passed"]
    assert any(c["rule"] == "non-spicy-source-oracle-v2-mustard-color-pepper" for c in oracle["actual"])


class FixedIntentLLM(BaseLLM):
    """Isolate the deterministic HTTP path without paid model calls."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        return Intent(
            people=1, meal_type="晚餐", dish_count=1, no_spicy=True, restrictions_confirmed=True
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


class AppendedRestrictionLLM(FixedIntentLLM):
    """Two annotated turns, not an NLU benchmark."""

    def __init__(self) -> None:
        self.parse_calls = 0

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        self.parse_calls += 1
        if self.parse_calls == 1:
            return Intent(people=1, meal_type="晚餐", dish_count=1, restrictions_confirmed=True)
        return Intent(no_spicy=True)


def test_appended_no_spicy_is_revalidated_and_survives_restart(tmp_path: Path) -> None:
    spicy = dish("油泼辣子")
    safe = dish().model_copy(update={"recipe_id": "safe", "name": "蒸青菜"})
    profile = UserProfile(
        data_scope="synthetic",
        user_id=900001,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    catalog = DataCatalog(
        profiles={profile.user_id: profile},
        recipes={spicy.recipe_id: spicy, safe.recipe_id: safe},
        quality_report={},
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    llm = AppendedRestrictionLLM()
    app = create_app(settings, llm, catalog, SessionStore(settings.database_path))
    with TestClient(app) as client:
        first = client.post(
            "/chat",
            json={"user_id": profile.user_id, "message": "1人晚餐，没有其他忌口，安排1道菜。"},
        ).json()
        assert first["menu"][0]["recipe_id"] == spicy.recipe_id
        sid = first["conversation_state"]["session_id"]
        second = client.post(
            "/chat",
            json={
                "user_id": profile.user_id,
                "session_id": sid,
                "message": "再加一个要求，不吃辣，其他不变。",
            },
        ).json()
        assert second["status"] == "ok"
        assert [item["recipe_id"] for item in second["menu"]] == ["safe"]
    app = create_app(settings, FixedIntentLLM(), catalog, SessionStore(settings.database_path))
    with TestClient(app) as client:
        restarted = client.post(
            "/chat",
            json={"user_id": profile.user_id, "session_id": sid, "message": "沿用刚才的不辣要求。"},
        ).json()
    assert restarted["conversation_state"]["constraints"]["no_spicy"] is True
    assert [item["recipe_id"] for item in restarted["menu"]] == ["safe"]


@pytest.mark.parametrize("with_safe_alternative", [False, True])
def test_http_never_renders_an_unverified_non_spicy_menu(
    tmp_path: Path, with_safe_alternative: bool
) -> None:
    spicy = dish("油泼辣子")
    recipes = {spicy.recipe_id: spicy}
    if with_safe_alternative:
        safe = dish().model_copy(update={"recipe_id": "safe"})
        recipes[safe.recipe_id] = safe
    profile = UserProfile(
        data_scope="synthetic",
        user_id=900001,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    catalog = DataCatalog(profiles={profile.user_id: profile}, recipes=recipes, quality_report={})
    # Defaults plus explicit fixture fields; never load real environment secrets.
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    app = create_app(settings, FixedIntentLLM(), catalog, SessionStore(settings.database_path))
    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={
                "user_id": profile.user_id,
                "message": "1人晚餐，不吃辣，没有其他忌口，安排1道菜。",
            },
        )
    assert response.status_code == 200
    result = response.json()
    if with_safe_alternative:
        assert result["status"] == "ok"
        assert [item["recipe_id"] for item in result["menu"]] == ["safe"]
        assert result["replacement_suggestions"] == []
    else:
        assert result["status"] == "no_feasible_menu"
        assert result["menu"] == []
        assert "菜单已按要求" not in result["reason"]


@pytest.mark.parametrize("with_safe_alternative", [False, True])
def test_streaming_answer_uses_the_same_non_spicy_gate(
    tmp_path: Path, with_safe_alternative: bool
) -> None:
    spicy = dish("小米辣")
    recipes = {spicy.recipe_id: spicy}
    if with_safe_alternative:
        safe = dish().model_copy(update={"recipe_id": "safe"})
        recipes[safe.recipe_id] = safe
    profile = UserProfile(
        data_scope="synthetic",
        user_id=900001,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    catalog = DataCatalog(profiles={profile.user_id: profile}, recipes=recipes, quality_report={})
    app = create_app(settings, FixedIntentLLM(), catalog, SessionStore(settings.database_path))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": str(profile.user_id),
                "messages": [
                    {"role": "user", "content": "1人晚餐，不吃辣，安排1道菜，没有其他忌口。"}
                ],
                "stream": True,
            },
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert data[-1] == "[DONE]"
    chunks = [json.loads(line) for line in data[:-1]]
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert "小米辣" not in content
    if with_safe_alternative:
        assert "白菜" in content
    else:
        assert "无法组成" in content
        assert "菜单已按要求" not in content
