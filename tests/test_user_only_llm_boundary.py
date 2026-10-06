"""Capture actual adapter requests; all sentinels/ID fixtures are public authored."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.diners import profile_diner
from app.agent.service import MealAgent
from app.api import main as api
from app.domain.models import (
    Constraints,
    Diner,
    FlavorResolution,
    FlavorRetractionOption,
    SessionState,
    UserProfile,
)
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings
from tests.test_llm import completion
from tests.test_runtime_catalog import source_rows, write_source


@pytest.fixture
def profile():
    return UserProfile(
        user_id=1,
        age=30,
        sex="未指定",
        allergies=["花生"],
        raw={"note": "private-raw-marker"},
        measurements={"value": "private-measurement-marker"},
    )


@pytest.fixture
def state():
    return SessionState(
        session_id="local-session-marker",
        user_id=1,
        constraints=Constraints(allergies=["花生"], no_spicy=True),
        menu_ids=["recipe-one", "recipe-two", "recipe-three"],
        menu_valid=True,
    )


@pytest.mark.parametrize(
    "preference,expected",
    [
        ("不辣", True),
        ("不要辣", True),
        ("不吃辣", True),
        ("清淡", False),
        ("不太辣", False),
        ("想吃辣", False),
    ],
)
def test_local_profile_non_spicy_is_explicit_only(preference, expected, profile, state):
    local = profile.model_copy(update={"preferences": [preference]})
    assert profile_diner(local).no_spicy is expected
    # Legacy owner record is upgraded locally, not by echoing a model/profile.
    state.constraints.no_spicy = False
    state.diners = [
        Diner(diner_id="owner", display_name="用户", profile_owner=True),
        Diner(diner_id="other", display_name="爸爸"),
    ]
    MealAgent._ensure_diner_state(state, local)
    assert state.diners[0].no_spicy is expected
    assert state.diners[1].no_spicy is False
    assert state.constraints.no_spicy is expected
    state.diners[0].attendance = False
    MealAgent._ensure_diner_state(state, local)
    assert state.constraints.no_spicy is False


async def test_allowlist_excludes_all_source_channels_and_keeps_user_context(
    profile, state
):
    original = profile.model_copy(update={"data_scope": "original"})
    state.constraints.preferences = ["local-constraint-marker"]
    state.constraints.health_goals = ["local-health-marker"]
    state.diners = [
        Diner(
            diner_id="local-diner-marker",
            display_name="local-name-marker",
            aliases=["local-alias-marker"],
            preferences=["local-preference-marker"],
            pending_allergy_terms=["local-allergy-marker"],
        )
    ]
    state.pending_clarification = "local-clarification-marker"
    state.pending_flavor_resolution = FlavorResolution(
        binding_hash="local-binding-marker",
        action="replace",
        replace_slot=2,
        original_menu_ids=["local-original-marker"],
        conflicts=["local-conflict-marker"],
        prompt="local-prompt-marker",
        options=[
            FlavorRetractionOption(
                display="local-option-marker",
                preference_index=0,
                before="local-before-marker",
                after="local-after-marker",
                removed_clause="local-clause-marker",
            )
        ],
    )
    state.history = [
        {"role": "user", "content": "我和爸爸吃晚餐", "extra": "local-extra-marker"},
        {"role": "assistant", "content": "local-recipe-ingredient-marker"},
        {"role": "system", "content": "local-system-marker"},
        {"role": "tool", "content": "local-tool-marker"},
        {"role": "user", "content": "爸爸不吃辣"},
    ]
    before = state.model_dump()
    observed = []

    def handler(request):
        observed.append(json.loads(request.content))
        return httpx.Response(
            200, json=completion({"action": "replace", "replace_slot": 2})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DeepSeekLLM("fake-test-secret", client=client)
        intent = await adapter.parse("只换第2道", state, original)
        assert intent.replace_slot == 2
        assert await adapter.explain({"local-fact-marker": "local-source-marker"}) == [
            "local-fact-marker"
        ]
    assert len(observed) == 1
    wire = json.dumps(observed, ensure_ascii=False)
    assert "local-" not in wire and "private-" not in wire and "recipe-two" not in wire
    context = json.loads(observed[0]["messages"][1]["content"])
    assert set(context) == {
        "message",
        "confirmed_fields",
        "pending_fields",
        "pending_allergy",
        "current_menu",
        "menu_valid",
        "pending_plan",
        "pending_method_tradeoff",
        "recent_history",
        "pending_flavor_resolution",
        "pending_menu_counts",
        "pending_revoke_exclusion",
    }
    assert context["recent_history"] == [
        {"role": "user", "content": "我和爸爸吃晚餐"},
        {"role": "user", "content": "爸爸不吃辣"},
    ]
    assert context["pending_allergy"] is True
    assert context["pending_flavor_resolution"] is True
    assert state.model_dump() == before and original.data_scope == "original"


async def test_default_provider_local_profile_api_continuation_and_edit(
    tmp_path, monkeypatch
):
    rows = source_rows()
    for row in rows:
        if row["id"] == 2:
            row["口味偏好"] = "不辣"
    path = write_source(tmp_path, rows)
    original_bytes = path.read_bytes()
    observed = []
    answers = {
        "2人晚餐，安排4道菜含1道汤，按档案忌口": {
            "action": "plan",
            "people": 2,
            "meal_type": "晚餐",
            "dish_count": 4,
            "soup_count": 1,
            "restrictions_confirmed": True,
        },
        "继续": {"action": "plan"},
        "只换第2道": {"action": "replace", "replace_slot": 2},
        "解释这份菜单": {"action": "explain"},
    }

    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        assert "facts" not in context and "profile" not in context
        assert "diners" not in context and "existing_constraints" not in context
        # Neither requirement appears in these user-authored messages.
        assert "花生" not in json.dumps(context, ensure_ascii=False)
        assert "不辣" not in json.dumps(context, ensure_ascii=False)
        assert all(item["role"] == "user" for item in context["recent_history"])
        observed.append(context)
        return httpx.Response(200, json=completion(answers[context["message"]]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:

        def factory(**kwargs):
            return DeepSeekLLM(**kwargs, client=upstream)

        monkeypatch.setattr(api, "DeepSeekLLM", factory)
        config = Settings(
            _env_file=None,
            local_profile_path=path,
            deepseek_api_key="fake-test-secret",
            session_db=tmp_path / "default.db",
        )
        # No injected LLM/catalog/planner/settings overrides beyond local config.
        with TestClient(api.create_app(settings=config)) as client:
            message = next(iter(answers))
            request = dict(user_id=2, message=message, request_id="privacy-plan")
            first = client.post("/chat", json=request).json()
            assert first["status"] == "ok" and len(first["menu"]) == 4
            sid = first["conversation_state"]["session_id"]
            assert first["conversation_state"]["constraints"]["allergies"] == ["花生"]
            assert first["conversation_state"]["constraints"]["no_spicy"] is True
            assert first["explanation_source"] == "verified_template"
            assert not any("模型解释暂不可用" in item for item in first["warnings"])
            assert (
                client.post("/chat", json={**request, "session_id": sid}).json()
                == first
            )
            assert len(observed) == 1  # Idempotent replay is not another parse.
            again = client.post(
                "/chat", json=dict(user_id=2, session_id=sid, message="继续")
            ).json()
            ids = [r["recipe_id"] for r in first["menu"]]
            assert [r["recipe_id"] for r in again["menu"]] == ids
            changed = client.post(
                "/chat", json=dict(user_id=2, session_id=sid, message="只换第2道")
            ).json()
            assert changed["status"] == "ok"
            new_ids = [r["recipe_id"] for r in changed["menu"]]
            assert all(
                old == new for i, (old, new) in enumerate(zip(ids, new_ids)) if i != 1
            )
            assert changed["conversation_state"]["constraints"]["no_spicy"] is True
            assert changed["conversation_state"]["constraints"]["allergies"] == ["花生"]
            assert (
                client.post(
                    "/chat", json=dict(user_id=1, session_id=sid, message="继续")
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/chat", json=dict(user_id=900001, message="继续")
                ).status_code
                == 404
            )
            compat = client.post(
                "/v1/chat/completions",
                json={
                    "model": "fangtai-meal-agent",
                    "user": "2",
                    "stream": True,
                    "messages": [{"role": "user", "content": message}],
                },
            )
            assert compat.status_code == 200 and "[DONE]" in compat.text
            readonly = client.post(
                "/chat", json=dict(user_id=2, session_id=sid, message="解释这份菜单")
            ).json()
            assert [r["recipe_id"] for r in readonly["menu"]] == new_ids
            assert (
                len(observed) == 5
            )  # Plan, continue, replace, SSE plan, explain-intent.
            assert api.load_runtime_catalog(path).profiles[2].data_scope == "original"
    assert path.read_bytes() == original_bytes
