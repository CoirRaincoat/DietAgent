"""Developer regressions for named edit scope, not independent menu quality."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.context_retry_scope import preserve_context_retry_scope, resolve_replacement_target
from app.api.main import create_app
from app.domain.models import (
    Constraints,
    ContextReplacementScope,
    Intent,
    Recipe,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from tests.test_flavor_matching_integration import FlavorLLM, dish
from tests.test_scene_retraction import state as owned_state


class NamedLLM(FlavorLLM):
    def __init__(self, intent: Intent):
        self.intent = intent

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "只换" in message:
            return self.intent.model_copy(deep=True)
        if message == "解释这份菜单":
            return Intent(action="explain")
        return Intent()


def make_app(tmp_path: Path, recipes: Sequence[Recipe], llm: NamedLLM) -> Any:
    profile = UserProfile(user_id=900001, data_scope="synthetic", age=30, sex="未指定")
    settings = Settings.model_construct(session_db=tmp_path / "flavor.db")
    return create_app(
        settings,
        llm,
        DataCatalog({900001: profile}, {r.recipe_id: r for r in recipes}, {}),
        SessionStore(settings.database_path),
    )


def seeded_app(tmp_path: Path, case: str) -> tuple[Any, list[Recipe], dict[str, Any], NamedLLM]:
    if case == "soup":
        recipes = [
            dish("白菜汤", "白菜200克；水500克；辣椒3克；盐1克"),
            dish("菠菜汤", "菠菜200克；水500克；盐1克"),
            dish("炒青菜", "青菜200克；盐1克"),
            dish("冬瓜汤", "冬瓜200克；水500克；盐1克"),
        ]
        intent = Intent(action="replace", replace_name="汤", no_spicy=True)
        constraints = Constraints(people=1, dish_count=3, soup_count=2)
        chosen = recipes[:3]
    else:
        recipes = [
            dish("便当炒白菜", "白菜200克；花生油5克；盐1克"),
            dish("炒菠菜", "菠菜200克；盐1克"),
            dish("炒青菜", "青菜200克；盐1克"),
            dish("炒西兰花", "西兰花200克；盐1克"),
        ]
        intent = Intent(
            action="replace", replace_name="炒菠菜", allergies=["花生"] if case == "allergy" else []
        )
        constraints = Constraints(people=1, dish_count=2)
        chosen = recipes[:2]
    llm = NamedLLM(intent)
    app = make_app(tmp_path, tuple(recipes), llm)
    seed = owned_state([])
    seed.user_id = 900001
    seed.diners = seed.diners[:1]
    seed.diners[0].allergies = []
    seed.diners[0].no_spicy = False
    seed.constraints = constraints
    seed.meal_constraints = constraints.model_copy(deep=True)
    seed.menu_ids = [r.recipe_id for r in chosen]
    seed.menu_valid = True
    SessionStore(tmp_path / "flavor.db").save(seed, None)
    return app, recipes, {"user_id": 900001, "session_id": seed.session_id}, llm


@pytest.mark.parametrize("case", ["context", "allergy", "soup"])
def test_failed_named_edit_cannot_turn_continue_into_whole_menu_authority(
    tmp_path: Path, case: str
) -> None:
    app, recipes, request, llm = seeded_app(tmp_path, case)
    message = {
        "context": "只换炒菠菜，这餐不要便当，其他不变。",
        "allergy": "只换炒菠菜，我花生过敏，其他不变。",
        "soup": "只换汤，不辣，其他不变。",
    }[case]
    with TestClient(app) as client:
        failed = client.post("/chat", json={**request, "message": message}).json()
        assert failed["status"] != "ok" and not failed["menu"]
        continued = client.post("/chat", json={**request, "message": "继续"}).json()
        assert continued["status"] != "ok" and not continued["menu"]
    with TestClient(make_app(tmp_path, tuple(recipes), llm)) as client:
        restarted = client.post("/chat", json={**request, "message": "继续"}).json()
        assert restarted["status"] != "ok" and not restarted["menu"]
        confirmed = client.post("/chat", json={**request, "message": "允许整餐调整。"}).json()
        if case == "soup":
            assert confirmed["status"] == "clarification_required" and not confirmed["menu"]
            confirmed = client.post(
                "/chat", json={**request, "message": "重新规划本餐菜单。"}
            ).json()
        assert confirmed["status"] == "ok" and confirmed["menu"]
        if case != "soup":
            assert recipes[1].recipe_id not in [r["recipe_id"] for r in confirmed["menu"]]
            assert not confirmed["conversation_state"]["rejected_recipe_ids"]


@pytest.mark.parametrize("target", [None, "不存在的菜"])
def test_unspecified_or_absent_target_remains_local_without_other_restrictions(
    tmp_path: Path, target: str | None
) -> None:
    app, recipes, request, llm = seeded_app(tmp_path, "context")
    llm.intent = Intent(action="replace", replace_name=target)
    with TestClient(app) as client:
        failed = client.post("/chat", json={**request, "message": "只换一道菜，其他不变。"}).json()
        assert failed["status"] == "clarification_required" and not failed["menu"]
        assert failed["conversation_state"]["pending_context_replacement"] is not None
        explained = client.post("/chat", json={**request, "message": "解释这份菜单"}).json()
        assert explained["status"] == "ok"
        assert explained["conversation_state"]["menu_ids"] == [r.recipe_id for r in recipes[:2]]
        assert explained["conversation_state"]["pending_context_replacement"] is not None
    with TestClient(make_app(tmp_path, tuple(recipes), llm)) as client:
        continued = client.post("/chat", json={**request, "message": "继续"}).json()
    assert continued["status"] == "clarification_required" and not continued["menu"]


def test_names_bind_to_this_menu_not_whole_catalog_and_conflicting_index_is_rejected() -> None:
    cabbage = dish("炒白菜", "白菜200克；盐1克")
    spinach = dish("炒菠菜", "菠菜200克；盐1克")
    target, issue = resolve_replacement_target(
        Intent(action="replace", replace_name="炒菠菜"), [cabbage, spinach]
    )
    assert issue is None and target.replace_slot == 2
    target, issue = resolve_replacement_target(
        Intent(action="replace", replace_name="炒菠菜", replace_slot=1), [cabbage, spinach]
    )
    assert issue and target.replace_slot == 1
    _, issue = resolve_replacement_target(
        Intent(action="replace", replace_name="炒西兰花"), [cabbage, spinach]
    )
    assert issue


def test_explicit_slot_can_disambiguate_multiple_soups_but_cannot_point_to_other_role() -> None:
    soup = dish("白菜汤", "白菜200克；水500克；盐1克")
    other_soup = dish("菠菜汤", "菠菜200克；水500克；盐1克")
    greens = dish("炒青菜", "青菜200克；盐1克")
    menu = [greens, soup, other_soup]
    _, issue = resolve_replacement_target(Intent(action="replace", replace_name="汤"), menu)
    assert issue
    intent, issue = resolve_replacement_target(
        Intent(action="replace", replace_name="汤", replace_slot=2), menu
    )
    assert issue is None and intent.replace_slot == 2
    _, issue = resolve_replacement_target(
        Intent(action="replace", replace_name="汤", replace_slot=1), menu
    )
    assert issue


def test_changed_menu_binding_requires_confirmation_not_reused_name_or_slot() -> None:
    state = owned_state([])
    state.menu_ids = ["new-first", "old-second"]
    state.pending_context_replacement = ContextReplacementScope(
        replace_slot=2, replace_name="炒菠菜", menu_ids=["old-first", "old-second"]
    )
    intent = preserve_context_retry_scope(state, Intent(), "继续")
    assert intent.action == "clarify" and not intent.replace_slot
    assert state.pending_context_replacement is not None


def test_legacy_pending_numbered_scope_remains_loadable() -> None:
    pending = ContextReplacementScope.model_validate({"replace_slot": 2, "menu_ids": ["a", "b"]})
    assert pending.replace_slot == 2 and pending.replace_name is None


def test_continue_after_completed_readonly_menu_does_not_invoke_replanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.planner import MenuPlanner

    app, recipes, request, llm = seeded_app(tmp_path, "context")

    def unexpected_plan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A completed menu's empty continuation must not replan")

    monkeypatch.setattr(MenuPlanner, "plan", unexpected_plan)
    with TestClient(app) as client:
        explained = client.post("/chat", json={**request, "message": "解释这份菜单"}).json()
        assert explained["status"] == "ok"
        continued = client.post("/chat", json={**request, "message": "继续"}).json()
        assert continued["status"] == "ok"
        assert [r["recipe_id"] for r in continued["menu"]] == [r.recipe_id for r in recipes[:2]]
    with TestClient(make_app(tmp_path, tuple(recipes), llm)) as client:
        resumed = client.post("/chat", json={**request, "message": "继续"}).json()
        assert resumed["status"] == "ok"
        assert [r["recipe_id"] for r in resumed["menu"]] == [r.recipe_id for r in recipes[:2]]


@pytest.mark.asyncio
async def test_post_clarification_plan_rebinds_whole_menu_replacement_obligation(
    tmp_path: Path,
) -> None:
    from app.agent.service import MealAgent

    _app, recipes, request, llm = seeded_app(tmp_path, "context")
    store = SessionStore(tmp_path / "flavor.db")
    state = store.get(request["session_id"], 900001)
    assert state is not None
    state.pending_context_replacement = ContextReplacementScope(
        replace_slot=2,
        replace_name=recipes[1].name,
        menu_ids=list(state.menu_ids),
        whole_menu_authorized=True,
    )
    profile = UserProfile(user_id=900001, data_scope="synthetic", age=30, sex="未指定")
    agent = MealAgent(
        DataCatalog({900001: profile}, {r.recipe_id: r for r in recipes}, {}), store, llm
    )
    resumed = await agent._plan(state, Intent(), list(state.menu_ids), [])
    assert resumed.status == "ok"
    assert recipes[1].recipe_id not in [r.recipe_id for r in resumed.menu]
    assert state.rejected_recipe_ids == []
    assert state.pending_context_replacement is None
