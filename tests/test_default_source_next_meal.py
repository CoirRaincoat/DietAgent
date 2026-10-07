"""Same authored M02 source workflow: real defaults, mocked intent, no paid NLU."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings
from app.rules.sauce_composition import unresolved_sauce_evidence


def test_default_source_api_changes_explicit_next_meals_but_preserves_every_other_boundary(
    tmp_path,
    monkeypatch,
):
    first_message = "1人晚餐，清淡不辣，3道菜，不要汤。"
    next_message = "安排下一餐，沿用原要求，换些不同的菜。"
    seen = []

    def handler(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert not set(context) & {"profile", "facts", "diners", "existing_constraints"}
        seen.append(context["message"])
        fields = {"action": "plan"}
        if context["message"] == first_message:
            fields.update(
                people=1,
                meal_type="晚餐",
                dish_count=3,
                soup_count=0,
                preferences=["清淡"],
                no_spicy=True,
                restrictions_confirmed=True,
            )
        elif context["message"] == "只换第2道":
            fields = {"action": "replace", "replace_slot": 2}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(fields, ensure_ascii=False)},
                    }
                ]
            },
        )

    provider = DeepSeekLLM(
        "not-live", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(api, "DeepSeekLLM", lambda **kwargs: provider)
    settings = Settings(
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key="",
        session_db=tmp_path / "default-source.db",
    )
    with TestClient(api.create_app(settings=settings)) as client:
        # Do not inject catalog/menu/planner/history or toggle the experiment.
        agent = client.app.state.agent
        assert agent.experiment_cross_meal_rotation
        menus = []
        expected = None
        sid = None
        for i, message in enumerate([first_message, *([next_message] * 3)]):
            request = {
                "user_id": 900001,
                "message": message,
                "request_id": f"default-m02-{i}",
            }
            if sid:
                request["session_id"] = sid
            value = client.post("/chat", json=request).json()
            assert value["status"] == "ok" and len(value["menu"]) == 3
            sid = value["conversation_state"]["session_id"]
            state = agent.store.get(sid, 900001)
            if expected is None:
                expected = state.constraints.model_dump()
            assert state.constraints.model_dump() == expected
            assert state.constraints.no_spicy and state.constraints.soup_count == 0
            assert state.meal_sequence == i
            records = [agent.catalog.recipes[r["recipe_id"]] for r in value["menu"]]
            assert all(
                agent.rules.evaluate(r, state.constraints).allowed for r in records
            )
            assert not any(
                unresolved_sauce_evidence(r, agent.rules._known_foods) for r in records
            )
            menus.append([r.name for r in records])
            assert (
                client.post("/chat", json={**request, "session_id": sid}).json()
                == value
            )
            continued = client.post(
                "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
            ).json()
            assert continued["menu"] == value["menu"]
            assert continued["conversation_state"]["meal_sequence"] == i
        assert menus[0] != menus[1] != menus[2]
        assert not set(menus[0]) & set(menus[1]) and not set(menus[1]) & set(menus[2])
        # Frozen M02 acceptance: each adjacent 3-dish meal changes at least two
        # actual source dishes, including the previously repeated fourth meal.
        assert all(len(set(a) - set(b)) >= 2 for a, b in zip(menus, menus[1:]))
        assert (
            client.post(
                "/chat", json={"user_id": 900003, "session_id": sid, "message": "继续"}
            ).status_code
            == 409
        )
        request = {
            "user_id": 900001,
            "session_id": sid,
            "message": "只换第2道",
            "request_id": "local-m02",
        }
        local = client.post("/chat", json=request).json()
        assert local["status"] == "ok"
        assert all(
            a == b
            for i, (a, b) in enumerate(zip(value["menu"], local["menu"]))
            if i != 1
        )
        assert client.post("/chat", json=request).json() == local
        assert (
            len(seen) == 9
        )  # 4 plans, 4 continuations, 1 local edit; no explain HTTP.


@pytest.mark.parametrize("stream", [False, True])
def test_same_frozen_m02_compatible_wire_uses_authorized_final_rotation(
    tmp_path, monkeypatch, stream
):
    first_message = "1人晚餐，清淡不辣，3道菜，不要汤。"
    next_message = "安排下一餐，沿用原要求，换些不同的菜。"

    def handler(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert not set(context) & {"profile", "facts", "diners", "existing_constraints"}
        fields = {"action": "plan"}
        if context["message"] == first_message:
            fields.update(
                people=1,
                meal_type="晚餐",
                dish_count=3,
                soup_count=0,
                preferences=["清淡"],
                no_spicy=True,
                restrictions_confirmed=True,
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(fields, ensure_ascii=False),
                        },
                    }
                ]
            },
        )

    provider = DeepSeekLLM(
        "not-live", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(api, "DeepSeekLLM", lambda **kwargs: provider)
    settings = Settings(
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key="",
        session_db=tmp_path / "compatible-source.db",
    )
    with TestClient(api.create_app(settings=settings)) as client:
        agent = client.app.state.agent
        sid = None
        menus = []
        for i, message in enumerate([first_message, *([next_message] * 3)]):
            body = {
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": stream,
                "messages": [{"role": "user", "content": message}],
                "request_id": f"compatible-m02-{i}",
            }
            if sid:
                body["session_id"] = sid
            response = client.post("/v1/chat/completions", json=body)
            assert response.status_code == 200
            sid = response.headers["X-Session-ID"]
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
            state = agent.store.get(sid, 900001)
            records = [agent.catalog.recipes[key] for key in state.menu_ids]
            names = [r.name for r in records]
            assert len(names) == 3 and state.meal_sequence == i
            assert all(name in text for name in names)
            assert state.constraints.no_spicy and state.constraints.soup_count == 0
            assert all(
                agent.rules.evaluate(r, state.constraints).allowed for r in records
            )
            menus.append(names)
            if i == 3:
                assert "未要求的一般做法分布" in text
                assert "不证明份量、味觉或营养达标" in text
            replayed = client.post(
                "/v1/chat/completions", json={**body, "session_id": sid}
            )
            assert replayed.status_code == 200
            assert agent.store.get(sid, 900001).menu_ids == state.menu_ids
        assert all(len(set(a) - set(b)) >= 2 for a, b in zip(menus, menus[1:]))
