"""Synthetic read-only API checks and static gateway-template checks, not live auth."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.infrastructure.settings import Settings


def isolated_client(tmp_path, monkeypatch):
    def unexpected_work(*args, **kwargs):
        raise AssertionError("Guide must not load profiles, sessions, providers or business tools")

    for name in ("load_runtime_catalog", "SessionStore", "DeepSeekLLM", "MealAgent"):
        monkeypatch.setattr(main, name, unexpected_work)
    config = Settings(
        _env_file=None, deepseek_api_key="", local_profile_path=None,
        session_db=tmp_path / "never-created.db",
    )
    # No lifespan: the guide is usable without any initialized catalog or agent.
    return TestClient(main.create_app(config))


def test_guide_is_fixed_head_safe_and_independent_of_runtime(tmp_path, monkeypatch):
    client = isolated_client(tmp_path, monkeypatch)
    get = client.get("/api-guide")
    with_inputs = client.get("/api-guide?user=synthetic-private-sentinel", headers={
        "Authorization": "Bearer synthetic-private-sentinel", "X-User-ID": "999999",
        "X-Session-ID": "synthetic-private-sentinel",
    })
    head = client.head("/api-guide")
    assert get.status_code == with_inputs.status_code == head.status_code == 200
    assert get.content == with_inputs.content and head.content == b""
    assert head.headers["content-length"] == get.headers["content-length"]
    assert get.headers["content-type"].startswith("application/json")
    guide = get.json()
    assert set(guide["examples"]["first"]) == {"model", "user", "messages"}
    assert guide["examples"]["first"]["user"].startswith("<")
    assert guide["optional"]["stream"]["default"] is False
    assert "synthetic-private-sentinel" not in get.text
    assert not any(value in get.text for value in ("900001", "D:\\", "/runtime", ".env"))
    assert "x-session-id" not in get.headers and "x-request-id" not in get.headers
    assert not list(tmp_path.iterdir())
    assert not hasattr(client.app.state, "agent") and not hasattr(client.app.state, "capacity")


def test_guide_scopes_error_format_without_assuming_four_fields_for_method_errors(
    tmp_path, monkeypatch,
):
    client = isolated_client(tmp_path, monkeypatch)
    guide = client.get("/api-guide").json()
    method_error = client.post("/api-guide")
    # An actual router response is a counterexample to a blanket four-field claim.
    assert method_error.status_code == 405
    assert method_error.headers["content-type"].startswith("application/json")
    assert method_error.json() == {"detail": "Method Not Allowed"}
    guidance = guide["responses"]["errors"]
    assert "兼容入口已处理的应用错误及网关401" in guidance
    assert "其他错误不保证该格式" in guidance
    assert "HTTP状态码" in guidance and "Content-Type" in guidance
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_guide_only_exposes_exact_get_head_path(tmp_path, monkeypatch, method):
    client = isolated_client(tmp_path, monkeypatch)
    assert client.request(method, "/api-guide").status_code == 405
    assert client.get("/api-guide/private").status_code == 404
    assert not list(tmp_path.iterdir()) and not hasattr(client.app.state, "agent")


def cloud_template():
    return (Path(__file__).resolve().parents[1] / "deployment/nginx.cloud.conf.template").read_text(
        encoding="utf-8",
    )


def test_gateway_static_401_has_four_fields_and_preserves_auth_rate_limits():
    template = cloud_template()
    body = re.search(r"return 401 '([^']+)';", template)
    assert body is not None
    error = json.loads(body.group(1))["error"]
    assert set(error) == {"message", "type", "param", "code"}
    assert error["type"] == "authentication_error"
    assert error["param"] == "Authorization" and error["code"] == "unauthorized"
    assert "API token required" in error["message"] and "/api-guide" in error["message"]
    assert '"Bearer ${PUBLIC_API_TOKEN}" 1;' in template and "default 0;" in template
    assert "if ($api_authorized = 0)" in template
    assert "rate=10r/s;" in template and "burst=20 nodelay;" in template
    assert "limit_req_status 429;" in template and "proxy_intercept_errors" not in template


def test_gateway_static_guide_has_exact_path_and_get_head_guard():
    template = cloud_template()
    before, marker, rest = template.partition("location = /api-guide {")
    assert marker and "api-guide" not in before
    guide_block, marker, chat = rest.partition("location ~ ^/(chat|v1/chat/completions|api/chat)$")
    assert marker and "if ($request_method !~ ^(GET|HEAD)$)" in guide_block
    assert "return 405" in guide_block and 'add_header Allow "GET, HEAD" always;' in guide_block
    assert "proxy_pass http://backend:8000;" in guide_block
    assert "$api_authorized" not in guide_block and "if ($api_authorized = 0)" in chat
    assert "location / {\n        return 404;" in template
