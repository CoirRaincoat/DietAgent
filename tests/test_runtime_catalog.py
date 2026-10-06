"""Public authored ID fixtures; no private source copied into tests."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import Intent
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.infrastructure.settings import Settings
from tests.test_meal_structure import StructureLLM


def source_rows() -> list[dict]:
    return [
        dict(
            id=i,
            年龄=30,
            性别="未指定",
            过敏食材=["花生"] if i == 2 else [],
            健康需求=[],
            口味偏好="清淡",
        )
        for i in range(50, 0, -1)
    ]


def write_source(tmp_path: Path, rows: object) -> Path:
    path = tmp_path / "local-fixture.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8-sig")
    return path


def test_no_config_retains_three_explicit_demo_users() -> None:
    assert set(load_runtime_catalog().profiles) == {900001, 900002, 900003}


def test_local_path_can_be_configured_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_source(tmp_path, source_rows())
    monkeypatch.setenv("LOCAL_PROFILE_PATH", str(path))
    config = Settings(_env_file=None)
    assert config.local_profile_path == path
    assert set(load_runtime_catalog(config.local_profile_path).profiles) == set(
        range(1, 51)
    )


def test_local_ids_are_exact_even_with_reversed_source_order(tmp_path: Path) -> None:
    path = write_source(tmp_path, source_rows())
    original = path.read_bytes()
    catalog = load_runtime_catalog(path)
    assert set(catalog.profiles) == set(range(1, 51))
    assert all(
        key == p.user_id and p.data_scope == "original"
        for key, p in catalog.profiles.items()
    )
    assert catalog.profiles[2].allergies == ["花生"]
    assert catalog.profiles[49].allergies == []
    assert catalog.profiles[1].bmi is None
    assert catalog.dialogues == []
    assert catalog.quality_report["original_dialogues_loaded"] is False
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "string", "bool", "wrong", "schema"]
)
def test_invalid_source_is_not_silently_mapped_or_loaded_as_demo(
    tmp_path: Path, change: str
) -> None:
    rows = source_rows()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[0]["id"] = 1
    elif change == "string":
        rows[0]["id"] = "50"
    elif change == "bool":
        rows[0]["id"] = True
    elif change == "wrong":
        rows[0]["id"] = 900001
    else:
        rows[0].pop("年龄")
    with pytest.raises(ValueError):
        load_runtime_catalog(write_source(tmp_path, rows))


def test_unreadable_source_does_not_fallback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no demo fallback"):
        load_runtime_catalog(tmp_path / "absent.json")


def test_native_configured_startup_uses_local_ids_and_session_ownership(
    tmp_path: Path,
) -> None:
    path = write_source(tmp_path, source_rows())
    llm = StructureLLM(
        [Intent(action="clarify", clarification="本地测试不规划") for _ in range(2)]
    )
    config = Settings(
        _env_file=None, local_profile_path=path, session_db=tmp_path / "state.sqlite3"
    )
    with TestClient(create_app(settings=config, llm=llm)) as client:
        health = client.get("/health").json()
        assert health["profile_count"] == 50 and health["profile_data_scope"] == [
            "original"
        ]
        assert client.get("/demo/profiles").json()["profiles"] == []
        first = client.post("/chat", json=dict(user_id=1, message="确认身份"))
        assert first.status_code == 200
        sid = first.json()["conversation_state"]["session_id"]
        assert first.json()["conversation_state"]["user_id"] == 1
        assert (
            client.post(
                "/chat", json=dict(user_id=2, session_id=sid, message="继续")
            ).status_code
            == 409
        )
        assert (
            client.post("/chat", json=dict(user_id=900001, message="继续")).status_code
            == 404
        )


def test_real_adapter_accepts_local_original_ids_without_sending_profile(
    tmp_path: Path,
) -> None:
    attempted = []

    def user_only(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert "profile" not in payload and "diners" not in payload
        assert "清淡" not in str(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "plan",
                                    "people": 1,
                                    "meal_type": "晚餐",
                                    "dish_count": 3,
                                    "restrictions_confirmed": True,
                                }
                            )
                        },
                    }
                ]
            },
        )

    path = write_source(tmp_path, source_rows())
    llm = DeepSeekLLM(
        api_key="local-not-live",
        client=httpx.AsyncClient(transport=httpx.MockTransport(user_only)),
    )
    config = Settings(
        _env_file=None, local_profile_path=path, session_db=tmp_path / "private.sqlite3"
    )
    with TestClient(create_app(settings=config, llm=llm)) as client:
        request = dict(user_id=1, message="1人晚餐，三菜，无其他忌口")
        reply = client.post("/chat", json=request)
        assert reply.status_code == 200 and reply.json()["status"] == "ok"
        assert reply.json()["explanation_source"] == "verified_template"
        assert reply.json()["conversation_state"]["user_id"] == 1
        compat = client.post(
            "/v1/chat/completions",
            json=dict(
                model="fangtai-meal-agent",
                user="1",
                stream=True,
                messages=[dict(role="user", content=request["message"])],
            ),
        )
        assert compat.status_code == 200 and "[DONE]" in compat.text
    assert len(attempted) == 2  # Only parsing; no explanation calls.
