"""Real local API workflows with a scripted adapter, not real-model quality."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.models import Intent, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.sessions import SessionStore
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent, openai_payload, parse_sse
from tests.test_component_slots import catalog, dish


def workflow_catalog() -> DataCatalog:
    source = catalog()[591]
    others = [
        dish("蒸西兰花", "西兰花200克；水100毫升", "西兰花蒸熟后装盘即可食用。"),
        dish("蒸鸡肉", "鸡肉200克；水100毫升", "鸡肉蒸熟后装盘即可食用。"),
        dish("番茄豆腐汤", "西红柿100克；豆腐100克；水300毫升", "西红柿豆腐煮熟后食用。"),
    ]
    profile = UserProfile(
        data_scope="synthetic",
        user_id=3,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    return DataCatalog(
        profiles={3: profile, 4: profile.model_copy(update={"user_id": 4})},
        recipes={r.recipe_id: r for r in [source, *others]},
        quality_report={},
    )


def post(client: Any, trace: list[dict[str, Any]], message: str, **fields: Any) -> dict[str, Any]:
    payload = dict(user_id=3, message=message, **fields)
    response = client.post("/chat", json=payload)
    result = response.json()
    trace.append(dict(request=payload, http_status=response.status_code, response=result))
    assert response.status_code == 200
    return result


def ids(result: dict[str, Any]) -> list[str]:
    return [r["recipe_id"] for r in result["menu"]]


def soups(result: dict[str, Any], data: DataCatalog) -> int:
    return sum(data.recipes[key].categories == ["soup"] for key in ids(result))


def exercise_initial_wire(tmp_path: Path, count: int, stream: bool) -> list[dict[str, Any]]:
    data = workflow_catalog()
    llm = ScriptedLLM(
        [
            complete_intent(
                dish_count=3, soup_count=count, no_spicy=True, preferred_ingredients=["南瓜"]
            )
        ]
    )
    trace: list[dict[str, Any]] = []
    message = f"1人晚餐，没有其他忌口，不要辣，3道菜其中{count}道汤，喜欢南瓜"
    with client_for(tmp_path, data, llm) as client:
        result = post(client, trace, message, request_id="source-soup-wire")
        sid = result["conversation_state"]["session_id"]
        wire = client.post(
            "/v1/chat/completions",
            json=openai_payload(
                stream=stream,
                session_id=sid,
                request_id="source-soup-wire",
                messages=[{"role": "user", "content": message}],
            ),
        )
        assert wire.status_code == 200 and llm.parse_calls == 1
        content = (
            "".join(c["choices"][0]["delta"].get("content", "") for c in parse_sse(wire.text))
            if stream
            else wire.json()["choices"][0]["message"]["content"]
        )
        trace.append(
            dict(
                wire_format="SSE" if stream else "nonstream",
                http_status=wire.status_code,
                literal_content=content,
                replay_parse_calls=llm.parse_calls,
            )
        )
    if count == 0:
        assert result["status"] == "no_feasible_menu" and not result["menu"]
        assert not result["conversation_state"]["menu_valid"]
        assert "其他菜 2 道" in result["reason"]
        assert all(r.name not in content for r in data.recipes.values())
    else:
        assert result["status"] == "ok" and len(result["menu"]) == 3
        assert soups(result, data) == count
        assert "南瓜海鲜盅" in content
        assert all(r["name"] in content for r in result["menu"])
    body, marker, payload = content.rpartition("\n\n【菜谱JSON】\n```json\n")
    assert marker and content.count(marker) == 1 and payload.endswith("\n```")
    assert json.loads(payload[:-4]) == [
        {"recipe_id": item["recipe_id"], "name": item["name"]} for item in result["menu"]
    ]
    if result["status"] == "ok":
        assert body.endswith(result["reason"])
    else:
        assert body == result["reason"] + "\n本次未返回菜单。"
    return trace


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("stream", [False, True])
def test_source_soup_count_and_failure_replay_through_both_wire_formats(tmp_path, count, stream):
    exercise_initial_wire(tmp_path, count, stream)


def exercise_increment_and_restart(tmp_path: Path) -> list[dict[str, Any]]:
    data = workflow_catalog()
    llm = ScriptedLLM(
        [
            complete_intent(dish_count=3, soup_count=1, no_spicy=True),
            Intent(soup_count=2),
            Intent(soup_count=0),
        ]
    )
    trace: list[dict[str, Any]] = []
    with client_for(tmp_path, data, llm) as client:
        first = post(client, trace, "1人晚餐，没有其他忌口，不辣，3道菜其中1汤")
        assert first["status"] == "ok" and soups(first, data) == 1
        sid = first["conversation_state"]["session_id"]
        second = post(client, trace, "仍总共3道菜，改成其中2道汤", session_id=sid)
        assert second["status"] == "ok" and soups(second, data) == 2
        failed = post(
            client, trace, "3道菜都不要汤", session_id=sid, request_id="zero-soup-failure"
        )
        assert failed["status"] == "no_feasible_menu" and not failed["menu"]
        assert failed["conversation_state"]["constraints"]["soup_count"] == 0
        assert failed["conversation_state"]["constraints"]["no_spicy"]
        replay = post(
            client, trace, "3道菜都不要汤", session_id=sid, request_id="zero-soup-failure"
        )
        assert replay == failed and llm.parse_calls == 3
    state = SessionStore(tmp_path / "state.db").get(sid, 3)
    assert state.constraints.soup_count == 0 and not state.menu_valid
    restarted = ScriptedLLM([Intent(action="explain")])
    with client_for(tmp_path, data, restarted) as client:
        result = post(client, trace, "解释当前菜单", session_id=sid)
        assert result["status"] == "clarification_required" and not result["menu"]
        assert "当前没有满足已知约束的完整菜单" in result["reason"]
        wrong_user = client.post(
            "/chat", json=dict(user_id=4, message="解释当前菜单", session_id=sid)
        )
        trace.append(
            dict(
                request=dict(user_id=4, message="解释当前菜单", session_id=sid),
                http_status=wrong_user.status_code,
                response=wrong_user.json(),
            )
        )
        assert wrong_user.status_code == 409 and restarted.parse_calls == 1
    return trace


def test_soup_count_changes_failure_replay_restart_and_user_isolation(tmp_path):
    exercise_increment_and_restart(tmp_path)


def exercise_replace_soup(tmp_path: Path) -> list[dict[str, Any]]:
    data = workflow_catalog()
    source = catalog()[591]
    llm = ScriptedLLM(
        [
            complete_intent(
                dish_count=3, soup_count=1, no_spicy=True, preferred_ingredients=["南瓜"]
            ),
            Intent(action="replace", replace_name="汤"),
        ]
    )
    trace: list[dict[str, Any]] = []
    with client_for(tmp_path, data, llm) as client:
        first = post(client, trace, "1人晚餐，没有其他忌口，不辣，3道菜其中1汤，喜欢南瓜")
        assert first["status"] == "ok" and source.recipe_id in ids(first)
        sid = first["conversation_state"]["session_id"]
        second = post(client, trace, "只换汤，其他两道别动", session_id=sid)
    assert second["status"] == "ok" and soups(second, data) == 1
    slot = ids(first).index(source.recipe_id)
    assert ids(second)[slot] != source.recipe_id
    assert all(a == b for i, (a, b) in enumerate(zip(ids(first), ids(second))) if i != slot)
    assert "其他菜保持不变" in second["reason"]
    return trace


def test_replace_by_soup_name_changes_only_the_source_soup_slot(tmp_path):
    exercise_replace_soup(tmp_path)


def exercise_stale_restart(tmp_path: Path, action: str) -> list[dict[str, Any]]:
    data = workflow_catalog()
    source = catalog()[591]
    trace: list[dict[str, Any]] = []
    with client_for(
        tmp_path,
        data,
        ScriptedLLM(
            [
                complete_intent(
                    dish_count=3,
                    soup_count=1,
                    no_spicy=True,
                    preferred_ingredients=["南瓜"],
                )
            ]
        ),
    ) as client:
        first = post(client, trace, "1人晚餐，没有其他忌口，不辣，3道菜其中1汤，喜欢南瓜")
        assert first["status"] == "ok" and source.recipe_id in ids(first)
        sid = first["conversation_state"]["session_id"]
    # Simulate an old external cache retaining the wrong non-soup role.
    data.recipes[source.recipe_id] = source.model_copy(update={"categories": ["vegetable"]})
    # A valid extra replacement prevents the pool-shortage check from masking
    # the distinct outside-slot authorization guard under examination.
    extra = dish("蒸白菜", "白菜200克；水100毫升", "白菜蒸熟装盘即可食用。")
    data.recipes[extra.recipe_id] = extra
    nonsoup_slot = next(i + 1 for i, key in enumerate(ids(first)) if key != source.recipe_id)
    intent = Intent(action=action, replace_slot=nonsoup_slot if action == "replace" else None)
    with client_for(tmp_path, data, ScriptedLLM([intent])) as client:
        second = post(
            client,
            trace,
            "解释当前菜单" if action == "explain" else f"只换第{nonsoup_slot}道，其他别动",
            session_id=sid,
        )
    expected_status = "clarification_required" if action == "explain" else "no_feasible_menu"
    assert second["status"] == expected_status and not second["menu"]
    assert not second["conversation_state"]["menu_valid"]
    assert second["conversation_state"]["constraints"]["soup_count"] == 1
    assert (
        "当前没有满足" in second["reason"]
        if action == "explain"
        else "不能只换指定菜" in second["reason"]
    )
    return trace


@pytest.mark.parametrize("action", ["explain", "replace"])
def test_restart_with_stale_role_cannot_explain_or_silently_expand_local_edit(tmp_path, action):
    exercise_stale_restart(tmp_path, action)


def exercise_allergy(tmp_path: Path) -> list[dict[str, Any]]:
    data = workflow_catalog()
    trace: list[dict[str, Any]] = []
    llm = ScriptedLLM(
        [
            complete_intent(
                dish_count=3,
                soup_count=1,
                no_spicy=True,
                allergies=["虾"],
                preferred_ingredients=["南瓜"],
            )
        ]
    )
    with client_for(tmp_path, data, llm) as client:
        result = post(client, trace, "1人晚餐，虾过敏，没有其他忌口，不辣，3道菜其中1汤，喜欢南瓜")
    assert result["status"] == "ok" and soups(result, data) == 1
    assert catalog()[591].recipe_id not in ids(result)
    assert result["conversation_state"]["constraints"]["allergies"] == ["虾"]
    assert result["conversation_state"]["constraints"]["no_spicy"]
    assert all(
        "虾" not in ingredient for item in result["menu"] for ingredient in item["ingredients"]
    )
    return trace


def test_source_soup_preference_cannot_override_allergy_or_nonspicy_gate(tmp_path):
    exercise_allergy(tmp_path)
