"""Scripted HTTP/SSE count-note menus, not real provider understanding."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.models import Intent, SessionState, UserProfile
from tests.test_component_slots import dish
from tests.test_flavor_matching_integration import FlavorLLM, make_app


class ZeroSoupLLM(FlavorLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            no_spicy=True,
            restrictions_confirmed=True,
        )


def recipes():
    return (
        dish("一人食1菜0汤", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。"),
        dish("辣炒鸡肉", "鸡肉200克；小米椒10克", "鸡肉炒熟装盘。"),
    )


def test_zero_soup_metadata_menu_survives_http_and_restart(tmp_path: Path) -> None:
    pair = recipes()
    with TestClient(make_app(tmp_path, pair, ZeroSoupLLM())) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "message": "一人晚餐一道菜，不要汤不要辣。"}
        )
        assert response.status_code == 200
        first = response.json()
    assert first["status"] == "ok"
    assert [item["recipe_id"] for item in first["menu"]] == [pair[0].recipe_id]
    reason = "".join(first["reason"].split())
    assert "1道蛋白质来源菜" in reason and "1道汤" not in reason
    with TestClient(make_app(tmp_path, pair, ZeroSoupLLM())) as client:
        retry = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "继续",
                "session_id": first["conversation_state"]["session_id"],
            },
        ).json()
    assert retry["status"] == "ok"
    assert [item["recipe_id"] for item in retry["menu"]] == [pair[0].recipe_id]


def test_sse_emits_the_real_unchanged_title_not_a_fabricated_soup(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path, recipes(), ZeroSoupLLM())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [{"role": "user", "content": "一人晚餐一道菜，不要汤不要辣。"}],
            },
        )
    assert response.status_code == 200
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "") for event in events[:-1]
    )
    assert "一人食1菜0汤" in text and "辣炒鸡肉" not in text
    normalized = "".join(text.split())
    assert "1道蛋白质来源菜" in normalized and "1道汤" not in normalized
