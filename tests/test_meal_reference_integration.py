"""Scripted HTTP/SSE reference disclosure, not real-provider quality judging."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.models import Intent, SessionState, UserProfile
from tests.test_flavor_matching_integration import FlavorLLM, make_app
from tests.test_meal_references import source_catalog


class MealReferenceLLM(FlavorLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="早餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
        )


def test_http_then_restart_discloses_reference_and_keeps_accepted_menu(tmp_path: Path) -> None:
    old, suggested = source_catalog()[1657], source_catalog()[1110]
    with TestClient(make_app(tmp_path, (old, suggested), MealReferenceLLM())) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "message": "一人早餐一道主食，无忌口。"}
        )
        assert response.status_code == 200
        first = response.json()
    assert first["status"] == "ok"
    assert [r["recipe_id"] for r in first["menu"]] == [suggested.recipe_id]
    assert "助手餐次参考" in first["reason"] and "尚未核验" in first["reason"]
    sid = first["conversation_state"]["session_id"]
    with TestClient(make_app(tmp_path, (old, suggested), MealReferenceLLM())) as client:
        retry = client.post(
            "/chat", json={"user_id": 900001, "message": "继续", "session_id": sid}
        ).json()
    assert retry["status"] == "ok"
    assert [r["recipe_id"] for r in retry["menu"]] == [suggested.recipe_id]
    assert "助手餐次参考" in retry["reason"]


def test_sse_minimal_explanation_cannot_hide_assistant_origin_or_unverified_state(
    tmp_path: Path,
) -> None:
    old, suggested = source_catalog()[1657], source_catalog()[1110]
    with TestClient(make_app(tmp_path, (old, suggested), MealReferenceLLM())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [{"role": "user", "content": "一人早餐一道主食，无忌口。"}],
            },
        )
    assert response.status_code == 200
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])
    assert "南瓜二米粥" in text and "助手餐次参考" in text and "尚未核验" in text
    assert "不是独立审核" in text
