"""Scripted menu transport and restart, not real-model extraction quality."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.models import Intent, SessionState, UserProfile
from tests.test_component_slots import catalog, dish
from tests.test_flavor_matching_integration import FlavorLLM, make_app


class ComponentLLM(FlavorLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
            query_terms=["香菇猪肉酱"],
        )


def test_http_and_restart_do_not_restore_a_component_from_stale_role_metadata(
    tmp_path: Path,
) -> None:
    bad = catalog()[651].model_copy(update={"eligible": True, "categories": ["protein"]})
    good = dish("蒸鸡肉", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。")
    with TestClient(make_app(tmp_path, (bad, good), ComponentLLM())) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "message": "一人晚餐一道菜，无忌口。"}
        )
        assert response.status_code == 200
        first = response.json()
    assert first["status"] == "ok"
    assert [item["recipe_id"] for item in first["menu"]] == [good.recipe_id]
    with TestClient(make_app(tmp_path, (bad, good), ComponentLLM())) as client:
        retry = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "继续",
                "session_id": first["conversation_state"]["session_id"],
            },
        ).json()
    assert retry["status"] == "ok"
    assert [item["recipe_id"] for item in retry["menu"]] == [good.recipe_id]


def test_sse_menu_does_not_render_a_component_as_a_main_dish(tmp_path: Path) -> None:
    bad = catalog()[651].model_copy(update={"eligible": True, "categories": ["protein"]})
    good = dish("蒸鸡肉", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。")
    with TestClient(make_app(tmp_path, (bad, good), ComponentLLM())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [{"role": "user", "content": "一人晚餐一道菜，无忌口。"}],
            },
        )
    assert response.status_code == 200
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "") for event in events[:-1]
    )
    assert "蒸鸡肉" in text and "香菇猪肉酱" not in text
