"""Authored multi-turn withdrawal regressions, not independent quality gold."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_preference_continuation import samples

from app.agent.diners import aggregate_constraints
from app.agent.scene_retraction import apply_scene_withdrawals, scene_withdrawals
from app.api.main import create_app
from app.domain.dining_scenes import scene_requests
from app.domain.models import Constraints, Diner, DinerUpdate, Intent, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings


def state(meal: list[str], personal: list[str] | None = None) -> SessionState:
    constraints = Constraints(preferences=meal, allergies=["虾"], no_spicy=True)
    diners = [
        Diner(
            diner_id="owner",
            display_name="用户",
            aliases=["我", "本人"],
            profile_owner=True,
            preferences=personal or [],
        ),
        Diner(
            diner_id="mum",
            display_name="妈妈",
            preferences=["便当", "清淡"],
            allergies=["花生"],
            no_spicy=True,
        ),
    ]
    return SessionState(
        session_id="a" * 32,
        user_id=3,
        meal_constraints=constraints,
        constraints=aggregate_constraints(constraints, diners),
        diners=diners,
        confirmed_fields=["people", "meal_type", "restrictions"],
    )


@pytest.mark.parametrize(
    "command",
    [
        "取消便当要求",
        "撤销本餐便当偏好",
        "这餐去掉便当场景",
        "取消我的便当要求",
        "妈妈的取消便当偏好",
    ],
)
def test_finite_commands(command: str) -> None:
    assert len(scene_withdrawals(command)) == 1


@pytest.mark.parametrize(
    "message",
    [
        "不要便当",
        "是否取消便当要求？",
        "如果取消便当要求",
        "解释取消便当要求",
        "他说‘取消便当要求’",
        "取消全部要求",
        "取消不辣",
        "取消虾过敏",
        "去掉健康目标",
        "取消妈妈的我便当偏好",
    ],
)
def test_mentions_safety_and_negative_requests_are_not_withdrawal(message: str) -> None:
    assert not scene_withdrawals(message)


def test_removes_only_meal_source_clause_and_extractor_echo() -> None:
    current = state(["便当，清淡，不吃辣", "蒸菜"])
    intent, issue = apply_scene_withdrawals(
        current, Intent(preferences=["便当", "清淡"]), "取消便当要求"
    )
    assert issue is None and intent.preferences == ["清淡"]
    assert current.meal_constraints is not None
    assert current.meal_constraints.preferences == ["清淡，不吃辣", "蒸菜"]
    assert current.diners[1].preferences == ["便当", "清淡"]
    assert current.constraints.allergies == ["虾", "花生"] and current.constraints.no_spicy
    assert current.last_scene_retractions[0]["status"] == "removed"


def test_negative_request_remains_distinct_from_canceling_positive() -> None:
    current = state(["便当", "不要便当"])
    _, issue = apply_scene_withdrawals(current, Intent(), "取消本餐便当要求")
    assert issue is None and current.meal_constraints is not None
    assert current.meal_constraints.preferences == ["不要便当"]
    assert scene_requests(current.meal_constraints.preferences).negative == ("便当",)


def test_explicit_negative_withdrawal_does_not_clear_positive() -> None:
    current = state(["便当", "不要便当"])
    _, issue = apply_scene_withdrawals(current, Intent(), "取消本餐不要便当要求")
    assert issue is None and current.meal_constraints is not None
    assert current.meal_constraints.preferences == ["便当"]


def test_unqualified_request_does_not_guess_profile_owner() -> None:
    current = state([], ["便当"])
    before = current.model_dump()
    _, issue = apply_scene_withdrawals(current, Intent(), "取消便当要求")
    assert issue and "谁" in issue and current.model_dump() == before


def test_personal_source_removed_without_aggregated_readdition() -> None:
    current = state([], ["便当，蒜香"])
    update = DinerUpdate(diner="我", preferences=["便当", "清淡"])
    intent, issue = apply_scene_withdrawals(
        current, Intent(preferences=["便当"], diner_updates=[update]), "取消我的便当要求"
    )
    assert issue is None and current.diners[0].preferences == ["蒜香"]
    assert intent.preferences == [] and intent.diner_updates[0].preferences == ["清淡"]
    assert current.diners[1].preferences == ["便当", "清淡"]
    assert "便当" in current.constraints.preferences  # Other diner's real requirement.
    assert update.preferences == ["便当", "清淡"]  # Don't mutate parsed input.


@pytest.mark.parametrize(
    "message",
    ["取消爷爷的便当要求", "取消本餐便当要求，取消爷爷的便当要求", "取消本餐便当要求，便当"],
)
def test_unresolved_or_contradictory_commands_are_atomic(message: str) -> None:
    current = state(["便当"])
    before = current.model_dump()
    _, issue = apply_scene_withdrawals(current, Intent(), message)
    assert issue and current.model_dump() == before


@pytest.mark.parametrize("action", ["explain", "clarify"])
def test_non_amendment_actions_cannot_retract(action: str) -> None:
    current = state(["便当"])
    before = current.model_dump()
    apply_scene_withdrawals(current, Intent(action=action), "取消便当要求")
    assert current.model_dump() == before


def test_mixed_unknown_source_does_not_drop_safety_or_other_text() -> None:
    current = state(["便当且花生过敏", "不要辣便当", "便当，家常，蒜香"])
    apply_scene_withdrawals(current, Intent(), "取消便当要求")
    assert current.meal_constraints is not None
    assert current.meal_constraints.preferences == ["便当且花生过敏", "不要辣便当", "家常，蒜香"]


class EchoLLM(BaseLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "只换第" in message:
            return Intent(action="replace", replace_slot=1, preferences=["便当"])
        if "解释" in message:
            return Intent(preferences=["便当"], no_spicy=False)
        return Intent(preferences=["便当"] if "取消" in message or message == "继续" else [])

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening"]  # Withdrawal evidence must not be omitted by model.

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("command", ["取消便当要求", "只换第1道，取消便当要求"])
def test_http_withdrawal_survives_retry_restart_and_readonly(
    tmp_path: Path, restart: bool, command: str
) -> None:
    catalog = DataCatalog(
        recipes={r.recipe_id: r for r in samples()},
        profiles={
            3: UserProfile(
                data_scope="synthetic",
                user_id=3,
                age=30,
                sex="女",
                height_cm=165,
                weight_kg=55,
                bmi=20.2,
            )
        },
        quality_report={},
    )
    settings = Settings.model_construct(
        deepseek_api_key=SecretStr(""), session_db=tmp_path / "state.db"
    )
    store = SessionStore(settings.database_path)
    initial = state(["便当"])
    initial.diners = initial.diners[:1]
    initial.constraints = aggregate_constraints(initial.meal_constraints, initial.diners)
    initial.menu_ids = [samples()[i].recipe_id for i in (0, 3, 4)]
    initial.menu_valid = True
    store.save(initial, None)
    with TestClient(create_app(settings, EchoLLM(), catalog, store)) as client:
        payload = {
            "user_id": 3,
            "session_id": "a" * 32,
            "message": command,
            "request_id": "withdraw",
        }
        first = client.post("/chat", json=payload).json()
        repeated = client.post("/chat", json=payload).json()
        assert first["status"] == "ok", first
        assert repeated == first
        assert "已撤销本餐" in first["reason"]
        assert first["conversation_state"]["constraints"]["preferences"] == []
        assert len(first["conversation_state"]["scene_retractions"]) == 1
        assert [m["recipe_id"] for m in first["menu"]][1:] == initial.menu_ids[1:]
        if command.startswith("只换"):
            assert first["menu"][0]["recipe_id"] != initial.menu_ids[0]
    if restart:
        store = SessionStore(settings.database_path)
    with TestClient(create_app(settings, EchoLLM(), catalog, store)) as client:
        explained = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": "a" * 32,
                "message": "只解释菜单，取消便当要求，不改菜单",
            },
        ).json()
        continued = client.post(
            "/chat", json={"user_id": 3, "session_id": "a" * 32, "message": "继续"}
        ).json()
        for result in (explained, continued):
            assert result["status"] == "ok"
            assert result["conversation_state"]["constraints"]["preferences"] == []
            assert result["conversation_state"]["constraints"]["allergies"] == ["虾"]
            assert result["conversation_state"]["constraints"]["no_spicy"]
            assert [m["recipe_id"] for m in result["menu"]] == [
                m["recipe_id"] for m in first["menu"]
            ]


def test_personal_continuation_echo_cannot_restore_withdrawn_source() -> None:
    current = state([], ["便当"])
    apply_scene_withdrawals(current, Intent(), "取消我的便当要求")
    intent, issue = apply_scene_withdrawals(
        current,
        Intent(
            preferences=["便当"],
            diner_updates=[
                DinerUpdate(diner="我", preferences=["便当"]),
                DinerUpdate(diner="妈妈", preferences=["便当"]),
            ],
        ),
        "继续",
    )
    assert issue is None and intent.preferences == []
    assert intent.diner_updates[0].preferences == []
    assert intent.diner_updates[1].preferences == ["便当"]


def test_later_explicit_positive_scene_is_not_permanently_banned() -> None:
    current = state(["便当"])
    apply_scene_withdrawals(current, Intent(), "取消便当要求")
    intent, issue = apply_scene_withdrawals(current, Intent(preferences=["便当"]), "这餐要便当")
    assert issue is None and intent.preferences == ["便当"]


def test_ambiguous_withdrawal_does_not_drop_new_allergy_fact(tmp_path: Path) -> None:
    from app.agent.service import MealAgent

    catalog = DataCatalog(
        recipes={r.recipe_id: r for r in samples()}, profiles={}, quality_report={}
    )
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), EchoLLM())
    current = state([], ["便当"])
    issue = agent._apply_intent(
        current, Intent(allergies=["芝麻"], no_spicy=True), "取消便当要求，我芝麻过敏"
    )
    assert issue and "谁" in issue
    assert "芝麻" in current.constraints.allergies and current.constraints.no_spicy
    assert current.diners[0].preferences == ["便当"]
