"""Annotated deterministic HTTP/SSE path, not an NLU or external model score."""

import json
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import Intent, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from tests.test_unqualified_sauce_restrictions import card


class SauceIntentLLM(BaseLLM):
    def __init__(self, restriction: Literal["no_spicy", "allergy"], appended: bool = False):
        self.restriction = restriction
        self.appended = appended
        self.calls = 0

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        self.calls += 1
        if self.appended and self.calls == 1:
            return Intent(people=1, meal_type="晚餐", dish_count=1, restrictions_confirmed=True)
        fields = dict(no_spicy=True) if self.restriction == "no_spicy" else dict(allergies=["芝麻"])
        if self.appended:
            return Intent(**fields)
        return Intent(
            people=1, meal_type="晚餐", dish_count=1, restrictions_confirmed=True, **fields
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


def fixture(
    tmp_path: Path, restriction: Literal["no_spicy", "allergy"], safe: bool, appended: bool = False
):
    unsafe = card("豆腐蒸熟后蘸酱料。").model_copy(update={"name": "未知蘸酱豆腐"})
    recipes = {unsafe.recipe_id: unsafe}
    if safe:
        alternative = card("豆腐蒸熟装盘。").model_copy(
            update={"recipe_id": "safe", "name": "明确蒸豆腐"}
        )
        recipes[alternative.recipe_id] = alternative
    profile = UserProfile(data_scope="synthetic", user_id=900001, age=30, sex="女")
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    catalog = DataCatalog(profiles={profile.user_id: profile}, recipes=recipes, quality_report={})
    app = create_app(
        settings,
        SauceIntentLLM(restriction, appended),
        catalog,
        SessionStore(settings.database_path),
    )
    return app, catalog, settings


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy"])
@pytest.mark.parametrize("safe", [False, True])
def test_http_does_not_render_unverified_generic_sauce(tmp_path, restriction, safe):
    app, _, _ = fixture(tmp_path, restriction, safe)
    with TestClient(app) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐，安排1道菜，沿用已确认限制。"}
        )
    assert response.status_code == 200
    value = response.json()
    assert [r["recipe_id"] for r in value["menu"]] == (["safe"] if safe else [])
    assert value["status"] == ("ok" if safe else "no_feasible_menu")
    assert all(r["recipe_id"] != "authored-sauce" for r in value["replacement_suggestions"])


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy"])
@pytest.mark.parametrize("safe", [False, True])
def test_sse_uses_same_generic_sauce_gate(tmp_path, restriction, safe):
    app, _, _ = fixture(tmp_path, restriction, safe)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "messages": [{"role": "user", "content": "沿用已确认限制，1人晚餐安排1道菜。"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    lines = [s.removeprefix("data: ") for s in response.text.splitlines() if s.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    content = "".join(
        json.loads(line)["choices"][0]["delta"].get("content", "") for line in lines[:-1]
    )
    assert "未知蘸酱豆腐" not in content
    assert ("明确蒸豆腐" in content) if safe else ("无法组成" in content)


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy"])
def test_appended_hard_restriction_rechecks_source_and_survives_restart(tmp_path, restriction):
    app, catalog, settings = fixture(tmp_path, restriction, True, appended=True)
    with TestClient(app) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐，没有其他忌口，安排1道菜。"}
        ).json()
        assert [r["recipe_id"] for r in first["menu"]] == ["authored-sauce"]
        sid = first["conversation_state"]["session_id"]
        second = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "再加一个已确认限制，其他不变。",
            },
        ).json()
        assert second["status"] == "ok"
        assert [r["recipe_id"] for r in second["menu"]] == ["safe"]
    restarted_app = create_app(
        settings, SauceIntentLLM(restriction), catalog, SessionStore(settings.database_path)
    )
    with TestClient(restarted_app) as client:
        value = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "沿用刚才要求。"}
        ).json()
    assert [r["recipe_id"] for r in value["menu"]] == ["safe"]
    constraints = value["conversation_state"]["constraints"]
    assert (
        constraints["no_spicy"] if restriction == "no_spicy" else "芝麻" in constraints["allergies"]
    )
