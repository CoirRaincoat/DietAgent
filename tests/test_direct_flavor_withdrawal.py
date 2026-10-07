"""Author-defined source withdrawal tests, not sensory/clinical certification."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_preference_continuation import samples
from test_scene_retraction import state

from app.agent.diners import aggregate_constraints
from app.agent.flavor_withdrawal import apply_flavor_withdrawals, flavor_withdrawals
from app.agent.request_authority import empty_continuation_request
from app.agent.service import MealAgent
from app.api.main import create_app
from app.domain.matching_tags import (
    FLAVOR_REQUEST_TERMS,
    flavor_conflicts,
    negative_flavor_request_clauses,
    supported_flavor_exclusions,
    supported_flavor_preferences,
)
from app.domain.models import DinerUpdate, Intent, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings


@pytest.mark.parametrize("flavor", sorted(FLAVOR_REQUEST_TERMS))
def test_each_existing_positive_reference_can_be_withdrawn(flavor: str) -> None:
    current = state(["喜欢" + flavor])
    intent, issue = apply_flavor_withdrawals(
        current, Intent(preferences=[flavor]), "取消本餐" + flavor + "偏好"
    )
    assert issue is None and intent.preferences == []
    assert current.meal_constraints.preferences == []
    assert current.constraints.no_spicy and current.constraints.allergies == ["虾", "花生"]


@pytest.mark.parametrize(
    "command",
    [
        "取消清淡偏好",
        "撤销本餐清淡口味",
        "去掉本餐清淡要求",
        "取消我的清淡偏好",
        "妈妈的取消清淡要求",
    ],
)
def test_direct_bounded_commands(command: str) -> None:
    assert len(flavor_withdrawals(command)) == 1


@pytest.mark.parametrize(
    "message",
    [
        "不要清淡",
        "去掉酸",
        "是否取消清淡偏好？",
        "如果取消清淡偏好",
        "解释取消清淡偏好",
        "他说‘取消清淡偏好’",
        "取消全部要求",
        "取消过敏",
        "取消健康目标",
        "取消晚餐要求",
    ],
)
def test_questions_exclusions_safety_and_other_axes_not_authority(message: str) -> None:
    assert flavor_withdrawals(message) == ()


@pytest.mark.parametrize("preference", ["不要辣", "不辣", "不喜欢香辣", "不要微辣", "不要酸辣"])
def test_negative_spicy_component_never_withdrawn(preference: str) -> None:
    current = state([preference])
    before = current.model_dump()
    _, issue = apply_flavor_withdrawals(current, Intent(), "取消本餐" + preference + "要求")
    assert issue and current.model_dump() == before


def test_mixed_source_preserves_unaffected_spans_and_extractor_command_echo() -> None:
    current = state(["喜欢清淡，护心，喜欢蒜香；不要辣"])
    intent, issue = apply_flavor_withdrawals(
        current, Intent(preferences=["清淡", "取消清淡偏好", "蒜香"]), "取消清淡偏好"
    )
    assert issue is None and intent.preferences == ["蒜香"]
    assert current.meal_constraints.preferences == ["护心，喜欢蒜香；不要辣"]
    assert supported_flavor_preferences(current.meal_constraints.preferences) == ("蒜香",)
    assert supported_flavor_exclusions(current.meal_constraints.preferences) == ("辣",)


@pytest.mark.parametrize(
    "entry,command",
    [
        ("清淡护心", "取消清淡偏好"),
        ("喜欢酸甜和蒜香", "取消酸甜偏好"),
        ("喜欢酸甜不辣", "取消酸甜要求"),
    ],
)
def test_no_partial_deletion_of_mixed_or_compound_source(entry: str, command: str) -> None:
    current = state([entry])
    before = current.model_dump()
    _, issue = apply_flavor_withdrawals(current, Intent(), command)
    assert issue and current.model_dump() == before


def test_personal_withdrawal_does_not_clear_another_person() -> None:
    current = state([], ["清淡"])
    current.diners[1].preferences = ["清淡"]
    current.constraints = aggregate_constraints(current.meal_constraints, current.diners)
    intent, issue = apply_flavor_withdrawals(
        current,
        Intent(
            preferences=["清淡"],
            diner_updates=[
                DinerUpdate(diner="我", preferences=["清淡"]),
                DinerUpdate(diner="妈妈", preferences=["清淡"]),
            ],
        ),
        "取消我的清淡偏好",
    )
    assert issue is None and current.diners[0].preferences == []
    assert current.diners[1].preferences == ["清淡"]
    assert current.constraints.preferences == ["清淡"]
    assert intent.preferences == [] and intent.diner_updates[0].preferences == []
    assert intent.diner_updates[1].preferences == ["清淡"]


@pytest.mark.parametrize(
    "message", ["取消清淡偏好", "取消爷爷的清淡要求", "取消本餐清淡偏好，取消爷爷的清淡偏好"]
)
def test_owner_ambiguity_is_atomic(message: str) -> None:
    current = state([], ["清淡"])
    before = current.model_dump()
    _, issue = apply_flavor_withdrawals(current, Intent(), message)
    assert issue and current.model_dump() == before


def test_polarity_and_compound_conflict_must_use_bound_existing_options() -> None:
    current = state(["喜欢酸甜", "不要酸"])
    before = current.model_dump()
    _, issue = apply_flavor_withdrawals(current, Intent(), "取消本餐酸甜偏好")
    assert (
        issue
        and current.model_dump() == before
        and flavor_conflicts(current.constraints.preferences)
    )


def test_negative_soft_preference_can_return_to_neutral_without_opposite_positive() -> None:
    current = state(["不要酸", "蒜香"])
    intent, issue = apply_flavor_withdrawals(
        current, Intent(preferences=["不要酸"]), "取消本餐不要酸要求"
    )
    assert (
        issue is None
        and current.meal_constraints.preferences == ["蒜香"]
        and intent.preferences == []
    )
    assert supported_flavor_exclusions(current.constraints.preferences) == ()


def test_a_real_negative_after_withdrawal_clause_is_still_grounded() -> None:
    assert negative_flavor_request_clauses("取消清淡偏好，不要酸") == ("不要酸",)
    assert negative_flavor_request_clauses("去掉清淡偏好，不要甜") == ("不要甜",)
    assert negative_flavor_request_clauses("去掉酸") == ("去掉酸",)
    assert negative_flavor_request_clauses("取消不要酸要求") == ()


def test_empty_continue_cannot_restore_source_but_fresh_request_can() -> None:
    current = state(["清淡"])
    apply_flavor_withdrawals(current, Intent(), "取消清淡偏好")
    echoed, _ = apply_flavor_withdrawals(current, Intent(preferences=["清淡"]), "继续")
    assert echoed.preferences == [] and len(current.direct_flavor_retractions) == 1
    fresh, _ = apply_flavor_withdrawals(current, Intent(preferences=["清淡"]), "喜欢清淡")
    assert fresh.preferences == ["清淡"]


class Echo(BaseLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if message == "继续":
            return Intent(
                action="reject",
                preferences=["清淡"],
                allergies=["芝麻"],
                no_spicy=False,
                people=8,
                dish_count=8,
                soup_count=3,
                inventory=["虾"],
                diner_updates=[DinerUpdate(diner="妈妈", attendance=False)],
            )
        return (
            Intent(action="replace", replace_slot=1, preferences=["清淡"])
            if "只换" in message
            else Intent(preferences=["清淡"])
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening"]

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize(
    "command", ["取消清淡偏好", "只换第1道，取消清淡偏好", "取消清淡偏好，不要酸"]
)
def test_actual_http_restart_retry_local_and_readonly(tmp_path: Path, command: str) -> None:
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
    initial = state(["清淡"])
    initial.diners = initial.diners[:1]
    initial.constraints = aggregate_constraints(initial.meal_constraints, initial.diners)
    initial.menu_ids = [samples()[i].recipe_id for i in (0, 3, 4)]
    initial.menu_valid = True
    store.save(initial, None)
    with TestClient(create_app(settings, Echo(), catalog, store)) as client:
        payload = {
            "user_id": 3,
            "session_id": initial.session_id,
            "message": command,
            "request_id": "cancel-flavor",
        }
        first = client.post("/chat", json=payload).json()
        assert first["status"] == "ok", first
        assert first == client.post("/chat", json=payload).json()
        assert "已撤销本餐口味偏好：清淡" in first["reason"]
        assert "清淡" not in first["conversation_state"]["constraints"]["preferences"]
        assert [m["recipe_id"] for m in first["menu"]][1:] == initial.menu_ids[1:]
        if "不要酸" in command:
            assert supported_flavor_exclusions(
                first["conversation_state"]["constraints"]["preferences"]
            ) == ("酸",)
    store = SessionStore(settings.database_path)
    with TestClient(create_app(settings, Echo(), catalog, store)) as client:
        for message in ["继续", "只解释菜单，取消清淡偏好，不改菜单"]:
            result = client.post(
                "/chat", json={"user_id": 3, "session_id": initial.session_id, "message": message}
            ).json()
            assert result["status"] == "ok"
            assert result["conversation_state"]["constraints"]["no_spicy"]
            assert result["conversation_state"]["constraints"]["allergies"] == ["虾"]
            assert "清淡" not in result["conversation_state"]["constraints"]["preferences"]
            assert len(result["conversation_state"]["direct_flavor_retractions"]) == 1
            assert result["conversation_state"]["rejected_recipe_ids"] == []
            assert len(result["conversation_state"]["diners"]) == 1
            assert result["conversation_state"]["constraints"]["people"] == 1
            assert result["conversation_state"]["constraints"]["inventory"] is None
            assert [m["recipe_id"] for m in result["menu"]] == [
                m["recipe_id"] for m in first["menu"]
            ]


def test_source_ambiguity_still_retains_same_turn_allergy(tmp_path: Path) -> None:
    catalog = DataCatalog(recipes={}, profiles={}, quality_report={})
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), Echo())
    current = state([], ["清淡"])
    issue = agent._apply_intent(current, Intent(allergies=["芝麻"]), "取消清淡偏好，我芝麻过敏")
    assert issue and "谁" in issue
    assert "芝麻" in current.constraints.allergies


@pytest.mark.parametrize("message", ["继续", "再试一次", "重试！", "就这样", "继续按刚才的要求"])
def test_finite_empty_continuation_authority(message: str) -> None:
    assert empty_continuation_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "继续，不要辣",
        "继续，我芝麻过敏",
        "他说继续",
        "“继续”",
        "如果继续",
        "继续只换第1道",
        "继续？",
    ],
)
def test_substantive_or_quoted_continuation_not_stripped(message: str) -> None:
    assert not empty_continuation_request(message)


def test_conflicting_migrated_state_gets_real_bound_options(tmp_path: Path) -> None:
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
    initial = state(["清淡", "不要清淡"])
    initial.diners = initial.diners[:1]
    initial.constraints = aggregate_constraints(initial.meal_constraints, initial.diners)
    initial.menu_ids = [samples()[i].recipe_id for i in (0, 3, 4)]
    store.save(initial, None)
    with TestClient(create_app(settings, Echo(), catalog, store)) as client:
        question = client.post(
            "/chat",
            json={"user_id": 3, "session_id": initial.session_id, "message": "取消本餐清淡偏好"},
        ).json()
        assert question["status"] == "clarification_required"
        pending = question["conversation_state"]["pending_flavor_resolution"]
        assert pending and pending["options"]
        selected = next(
            option["display"] for option in pending["options"] if option["removed_clause"] == "清淡"
        )
        result = client.post(
            "/chat", json={"user_id": 3, "session_id": initial.session_id, "message": selected}
        ).json()
        assert result["status"] == "ok", result
        assert result["conversation_state"]["meal_constraints"]["preferences"] == ["不要清淡"]
