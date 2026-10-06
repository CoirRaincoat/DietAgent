"""Adjacent non-spicy/positive descriptors; no live model or clinical labels."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.flavor_resolution import binding
from app.api.main import create_app
from app.domain.matching_tags import (
    explicit_non_spicy_flavor_preference,
    flavor_conflicts,
    negative_flavor_request_clauses,
    supported_flavor_exclusions,
    supported_flavor_preferences,
)
from app.domain.models import (
    Constraints,
    FlavorResolution,
    Intent,
    Recipe,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings


@pytest.mark.parametrize(
    "text", ["不辣清淡", "我不要辣清淡一点", "这餐不吃辣原味", "口味不能吃辣酸甜"]
)
def test_adjacent_non_spicy_does_not_negate_positive_descriptor(text: str) -> None:
    assert supported_flavor_exclusions([text]) == ("辣",)
    assert supported_flavor_preferences([text])
    assert flavor_conflicts(["清淡", text]) == ()
    assert explicit_non_spicy_flavor_preference([text])


@pytest.mark.parametrize("text", ["不辣清淡", "不辣清淡一点", "不辣清淡不要甜"])
def test_mixed_source_clause_is_not_rewritten_or_lost(text: str) -> None:
    assert negative_flavor_request_clauses(text) == (text,)
    assert "清淡" in supported_flavor_preferences([text])
    assert "清淡" not in supported_flavor_exclusions([text])
    if text.endswith("不要甜"):
        assert "甜" in supported_flavor_exclusions([text])


@pytest.mark.parametrize("text", ["不要辣和清淡", "不要辣与清淡", "辣和清淡都不要"])
def test_coordinated_and_trailing_real_negation_keeps_scope(text: str) -> None:
    assert "清淡" in supported_flavor_exclusions([text])
    assert not supported_flavor_preferences([text])
    assert flavor_conflicts(["清淡", text]) == ("清淡",)


@pytest.mark.parametrize(
    "text", ["不太辣清淡", "不那么辣清淡", "不是不辣清淡", "不辣椒酱"]
)
def test_weak_double_negation_and_food_name_do_not_gain_strict_authority(
    text: str,
) -> None:
    assert not explicit_non_spicy_flavor_preference([text])


@pytest.mark.parametrize(
    "text",
    [
        "妈妈不辣清淡",
        "他不要辣清淡",
        "如果不辣清淡",
        "‘不辣清淡’",
        "不辣清淡是什么意思？",
    ],
)
def test_attributed_quoted_and_descriptive_messages_do_not_become_shared_source(
    text: str,
) -> None:
    assert negative_flavor_request_clauses(text) == ()


def dish(name: str, foods: str, label: str = "晚餐、清淡") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": "食材蒸熟装盘。",
                        "label": label,
                    }
                ]
            ).values()
        )
    )


class OmittingLLM(BaseLLM):
    async def parse(
        self, message: str, state: SessionState, profile: UserProfile
    ) -> Intent:
        # Omit both flavor and non-spicy fields. Source grounding must retain
        # the actual mixed clause; this is not a claim about a live provider.
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening"]

    async def aclose(self) -> None:
        pass


def application(tmp_path: Path) -> FastAPI:
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    recipes = [
        dish("蒸白菜甲", "白菜200克"),
        dish("蒸白菜乙", "白菜200克；辣椒5克"),
        dish("蒸白菜丙", "白菜200克；花生10克"),
    ]
    settings = Settings(  # type: ignore[call-arg]  # BaseSettings runtime override.
        _env_file=None,
        deepseek_api_key=SecretStr(""),
        session_db=tmp_path / "inline.db",
    )
    return create_app(
        settings=settings,
        llm=OmittingLLM(),
        catalog=DataCatalog({900001: profile}, {r.recipe_id: r for r in recipes}, {}),
    )


def test_default_service_grounds_mixed_positive_and_hard_non_spicy_when_model_omits_both(
    tmp_path: Path,
) -> None:
    app = application(tmp_path)
    with TestClient(app) as client:
        body = {
            "user_id": 900001,
            "message": "1人晚餐1道菜，不辣清淡，没有其他忌口。",
            "request_id": "mixed",
        }
        first = client.post("/chat", json=body).json()
        assert first["status"] == "ok" and first["menu"]
        constraints = Constraints.model_validate(
            first["conversation_state"]["constraints"]
        )
        assert constraints.no_spicy and supported_flavor_preferences(
            constraints.preferences
        ) == ("清淡",)
        assert "不辣清淡" in constraints.preferences
        assert all("辣椒" not in r["ingredients"] for r in first["menu"])
        assert not first["conversation_state"]["pending_flavor_resolution"]
        sid = first["conversation_state"]["session_id"]
        assert client.post("/chat", json={**body, "session_id": sid}).json() == first
        cross = client.post(
            "/chat", json={**body, "user_id": 900002, "session_id": sid}
        )
        assert cross.status_code in {404, 409}


def seed_old_false_question(
    app: FastAPI, *, genuine: bool = False, local: bool = False
) -> str:
    preferences = ["清淡", "不要清淡"] if genuine else ["清淡", "不辣清淡"]
    c = Constraints(
        people=1,
        meal_type="晚餐",
        dish_count=1,
        soup_count=0,
        preferences=preferences,
        no_spicy=True,
        allergies=["花生"],
    )
    state = SessionState(
        session_id="f" * 32,
        user_id=900001,
        constraints=c,
        meal_constraints=c.model_copy(deep=True),
        menu_structure_explicit=True,
        pending_plan=True,
        confirmed_fields=["people", "meal_type", "restrictions"],
    )
    if local:
        state.menu_ids = [
            next(
                r.recipe_id
                for r in app.state.agent.catalog.recipes.values()
                if r.name == "蒸白菜甲"
            )
        ]
    state.pending_flavor_resolution = FlavorResolution(
        binding_hash="old",
        action="replace" if local else "plan",
        replace_slot=1 if local else None,
        original_menu_ids=list(state.menu_ids),
        conflicts=["清淡"],
        options=[],
        prompt="旧清淡冲突问题",
    )
    state.pending_flavor_resolution.binding_hash = binding(
        state,
        list(app.state.agent.catalog.recipes.values()),
        state.pending_flavor_resolution.action,
        state.pending_flavor_resolution.replace_slot,
    )
    app.state.agent.store.save(state, expected_revision=None)
    return state.session_id


def test_old_false_initial_question_resumes_without_retracting_source_or_safety(
    tmp_path: Path,
) -> None:
    app = application(tmp_path)
    with TestClient(app) as client:
        sid = seed_old_false_question(app)
        body = {
            "user_id": 900001,
            "session_id": sid,
            "message": "继续",
            "request_id": "old-continue",
        }
        response = client.post("/chat", json=body).json()
        assert response["status"] == "ok" and response["menu"]
        c = response["conversation_state"]["constraints"]
        assert c["preferences"] == ["清淡", "不辣清淡"]
        assert c["no_spicy"] and c["allergies"] == ["花生"]
        assert response["conversation_state"]["flavor_retractions"] == []
        assert response["conversation_state"]["pending_flavor_resolution"] is None
        assert client.post("/chat", json=body).json() == response
    with TestClient(application(tmp_path)) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
        ).json()
        assert response["status"] == "ok" and response["menu"]


@pytest.mark.parametrize("mode", ["genuine", "local", "readonly"])
def test_old_question_cleanup_never_loosens_real_conflicts_local_scope_or_readonly(
    tmp_path: Path, mode: str
) -> None:
    app = application(tmp_path)
    with TestClient(app) as client:
        sid = seed_old_false_question(
            app, genuine=mode == "genuine", local=mode == "local"
        )
        before = app.state.agent.store.get(sid, 900001)
        response = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "只解释，不改菜单" if mode == "readonly" else "继续",
            },
        ).json()
    assert before is not None
    after = app.state.agent.store.get(sid, 900001)
    assert after is not None
    assert after.constraints.preferences == before.constraints.preferences
    assert after.constraints.no_spicy and after.constraints.allergies == ["花生"]
    assert after.menu_ids == before.menu_ids
    if mode != "readonly":
        assert response["status"] == "clarification_required"
    assert after.flavor_retractions == []


@pytest.mark.parametrize("stream", [False, True])
def test_actual_default_source_case_uses_original_phrase_on_compatible_wire(
    tmp_path: Path, stream: bool
) -> None:
    message = "2个人吃晚餐，3道菜不要汤，不辣清淡，想兼顾降压，优先鱼，没有其他忌口。"

    def handler(request: httpx.Request) -> httpx.Response:
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert context["message"] == message
        intent: dict[str, Any] = {
            "action": "plan",
            "people": 2,
            "meal_type": "晚餐",
            "dish_count": 3,
            "soup_count": 0,
            "no_spicy": True,
            "preferences": ["清淡"],
            "health_goals": ["降压"],
            "preferred_ingredients": ["鱼"],
            "restrictions_confirmed": True,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(intent, ensure_ascii=False)},
                    }
                ]
            },
        )

    provider = DeepSeekLLM(
        "mock-only", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    settings = Settings(  # type: ignore[call-arg]  # BaseSettings runtime override.
        _env_file=None,
        deepseek_api_key=SecretStr(""),
        local_profile_path=None,
        session_db=tmp_path / "wire.db",
    )
    app = create_app(settings=settings, llm=provider)
    with TestClient(app) as client:
        body = {
            "model": "fangtai-meal-agent",
            "user": "900001",
            "stream": stream,
            "messages": [{"role": "user", "content": message}],
            "request_id": "inline-wire",
        }
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        if stream:
            chunks = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            text = "".join(
                chunk["choices"][0]["delta"].get("content", "") for chunk in chunks
            )
        else:
            text = response.json()["choices"][0]["message"]["content"]
        state = app.state.agent.store.get(response.headers["X-Session-ID"], 900001)
        assert state is not None and state.menu_valid and len(state.menu_ids) == 3
        assert state.constraints.no_spicy and state.constraints.soup_count == 0
        assert state.constraints.preferred_ingredients == [
            "鱼"
        ] and state.constraints.health_goals == ["降压"]
        assert not flavor_conflicts(state.constraints.preferences)
        assert "撤回" not in text
        assert all(
            app.state.agent.catalog.recipes[key].name in text for key in state.menu_ids
        )
        retry = client.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-Session-ID": state.session_id},
        )
        assert retry.status_code == 200
        if stream:
            retry_chunks = [
                json.loads(line[6:])
                for line in retry.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            retry_text = "".join(
                chunk["choices"][0]["delta"].get("content", "")
                for chunk in retry_chunks
            )
        else:
            retry_text = retry.json()["choices"][0]["message"]["content"]
        assert retry_text == text
        repeated = app.state.agent.store.get(state.session_id, 900001)
        assert repeated is not None and repeated.menu_ids == state.menu_ids
