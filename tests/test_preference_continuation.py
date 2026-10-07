"""Authored accepted-edit regressions; continuation is not new optimization consent."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.models import Constraints, Ingredient, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def dish(name: str, foods: list[str], role: str = "protein") -> Recipe:
    """Public fixture with an authored role; no original record is copied."""
    return Recipe(
        recipe_id=name,
        name=name,
        raw_ingredients="；".join(foods),
        ingredients=[Ingredient(name=food, raw=food) for food in foods],
        steps="食材放入蒸锅蒸熟后装盘。",
        categories=[role],
        methods=["蒸"],
        raw_label="晚餐",
        meal_types=["晚餐"],
        source_row=1,
        fingerprint=name,
    )


def samples() -> list[Recipe]:
    return [
        dish("蒸鸡胸肉", ["鸡胸肉"]),
        dish("蒸猪肉", ["猪肉"]),
        dish("辣鸡胸肉", ["鸡胸肉", "辣椒酱"]),
        dish("蒸白菜", ["白菜"], "vegetable"),
        dish("蒸米饭", ["大米"], "staple"),
    ]


@pytest.mark.parametrize("preferred", ["鸡胸肉", "鸡肉"])
def test_plain_retry_keeps_an_accepted_local_edit_and_discloses_missing_preference(
    preferred: str,
) -> None:
    chicken, pork, _, vegetable, rice = samples()
    pool = [chicken, pork, vegetable, rice]
    constraints = Constraints(preferred_ingredients=[preferred])
    planner = MenuPlanner(RuleEngine())
    edited = planner.plan(pool, constraints, current=[chicken, vegetable, rice], replace_slot=1)
    assert edited.failure is None and edited.recipes == [pork, vegetable, rice]
    continued = planner.plan(
        pool, constraints, current=edited.recipes, recheck_soft_preferences=False
    )
    assert continued.failure is None
    assert continued.recipes == edited.recipes
    assert any("未覆盖食材偏好" in warning for warning in continued.warnings)
    assert continued.changes == []


def test_explicit_recheck_can_repair_the_missing_preference() -> None:
    chicken, pork, _, vegetable, rice = samples()
    pool = [chicken, pork, vegetable, rice]
    result = MenuPlanner(RuleEngine()).plan(
        pool,
        Constraints(preferred_ingredients=["鸡胸肉"]),
        current=[pork, vegetable, rice],
        recheck_soft_preferences=True,
    )
    assert result.failure is None and result.recipes == [chicken, vegetable, rice]
    assert not any("未覆盖食材偏好" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "constraints",
    [
        Constraints(preferred_ingredients=["鸡胸肉"], allergies=["猪肉"]),
        Constraints(preferred_ingredients=["鸡胸肉"], excluded_ingredients=["猪肉"]),
        Constraints(preferred_ingredients=["鸡胸肉"], no_spicy=True),
    ],
)
def test_continuation_permission_never_bypasses_final_hard_screening(
    constraints: Constraints,
) -> None:
    chicken, pork, spicy, vegetable, rice = samples()
    old = spicy if constraints.no_spicy else pork
    result = MenuPlanner(RuleEngine()).plan(
        [chicken, pork, spicy, vegetable, rice],
        constraints,
        current=[old, vegetable, rice],
        recheck_soft_preferences=False,
    )
    assert result.failure is None and old not in result.recipes
    assert all(RuleEngine().evaluate(item, constraints).allowed for item in result.recipes)


def test_no_available_preferred_dish_is_a_warning_not_a_fabricated_menu() -> None:
    chicken, pork, _, vegetable, rice = samples()
    result = MenuPlanner(RuleEngine()).plan(
        [chicken, pork, vegetable, rice],
        Constraints(preferred_ingredients=["鲈鱼"]),
        current=[pork, vegetable, rice],
        recheck_soft_preferences=False,
    )
    assert result.failure is None and result.recipes == [pork, vegetable, rice]
    assert any("鲈鱼" in warning for warning in result.warnings)


class KnownIntents(BaseLLM):
    """No network or real model parsing; explicit test-only task additions."""

    def __init__(self) -> None:
        self.intents = [
            Intent(
                people=1,
                meal_type="晚餐",
                restrictions_confirmed=True,
                no_spicy=True,
                preferred_ingredients=["鸡胸肉"],
            )
        ]

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        return self.intents.pop(0) if self.intents else Intent()

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


def client_for(path: Path, llm: KnownIntents) -> TestClient:
    settings = Settings.model_construct(
        deepseek_api_key=SecretStr(""), session_db=path / "state.db"
    )
    catalog = DataCatalog(
        recipes={item.recipe_id: item for item in samples()},
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
    return TestClient(create_app(settings, llm, catalog, SessionStore(settings.database_path)))


@pytest.mark.parametrize("restart", [False, True])
def test_http_plain_continue_after_replacing_preferred_food_is_stable(
    tmp_path: Path,
    restart: bool,
) -> None:
    llm = KnownIntents()
    with client_for(tmp_path, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "一人晚餐，要鸡胸肉，不辣"}
        ).json()
        session_id = first["conversation_state"]["session_id"]
        slot = next(i for i, item in enumerate(first["menu"], 1) if item["name"] == "蒸鸡胸肉")
        llm.intents.append(Intent(action="replace", replace_slot=slot))
        replaced = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": session_id,
                "message": f"只换第{slot}道，其它不变",
            },
        ).json()
        assert replaced["status"] == "ok"
        assert "蒸猪肉" in [item["name"] for item in replaced["menu"]]
        request = {
            "user_id": 3,
            "session_id": session_id,
            "message": "继续",
            "request_id": "plain-continue",
        }
        if not restart:
            continued = client.post("/chat", json=request).json()
            assert client.post("/chat", json=request).json() == continued
    if restart:
        with client_for(tmp_path, llm) as client:
            continued = client.post("/chat", json=request).json()
            assert client.post("/chat", json=request).json() == continued
    assert continued["status"] == "ok"
    assert continued["menu"] == replaced["menu"]
    assert continued["conversation_state"]["constraints"]["preferred_ingredients"] == ["鸡胸肉"]
    assert any("未覆盖食材偏好" in warning for warning in continued["warnings"])


@pytest.mark.parametrize("stream", [False, True])
def test_plain_openai_continuation_does_not_silently_undo_the_local_edit(
    tmp_path: Path,
    stream: bool,
) -> None:
    llm = KnownIntents()
    with client_for(tmp_path, llm) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "一人晚餐，要鸡胸肉，不辣"}
        ).json()
        session_id = first["conversation_state"]["session_id"]
        slot = next(i for i, item in enumerate(first["menu"], 1) if item["name"] == "蒸鸡胸肉")
        llm.intents.append(Intent(action="replace", replace_slot=slot))
        replaced = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": session_id,
                "message": f"只换第{slot}道",
            },
        ).json()
        response = client.post(
            "/v1/chat/completions",
            headers={"X-Session-ID": session_id},
            json={
                "model": "fangtai-meal-agent",
                "user": "3",
                "stream": stream,
                "messages": [{"role": "user", "content": "继续"}],
            },
        )
        assert response.status_code == 200
    if stream:
        lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        answer = "".join(
            json.loads(line.removeprefix("data: "))["choices"][0]["delta"].get("content", "")
            for line in lines[:-1]
        )
    else:
        answer = response.json()["choices"][0]["message"]["content"]
    assert "蒸猪肉" in answer and "蒸鸡胸肉" not in answer
    restored = SessionStore(tmp_path / "state.db").get(session_id, 3)
    assert restored is not None
    assert restored.menu_ids == [item["recipe_id"] for item in replaced["menu"]]
