"""Synthetic startup checks: diagnostics reach only the backend process."""

import subprocess
from unittest.mock import Mock

import pytest
import yaml

from app.infrastructure.settings import PROJECT_ROOT, Settings
from evaluation import demo_start


@pytest.mark.parametrize("enabled,inherited", [(True, "false"), (False, "true")])
def test_explicit_settings_override_inherited_diagnostic_flag_without_exposing_secret(
    enabled, inherited, tmp_path, monkeypatch, capsys,
):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/compose.env").write_text("# Empty\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("LATENCY_DIAGNOSTICS_ENABLED", inherited)
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    settings = Settings(_env_file=None, latency_diagnostics_enabled=enabled, deepseek_api_key="SENSITIVE-synthetic-key")
    demo_start.start_demo(settings, project_root=tmp_path)
    assert runner.call_count == 4
    for invoked in runner.call_args_list:
        assert invoked.kwargs["env"]["LATENCY_DIAGNOSTICS_ENABLED"] == ("true" if enabled else "false")
        assert "SENSITIVE" not in repr(invoked.args[0])
    captured = capsys.readouterr()
    assert "SENSITIVE" not in captured.out + captured.err


def test_compose_passes_default_off_diagnostics_only_to_backend_and_preserves_deployment_boundary():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    backend, frontend = compose["services"]["backend"], compose["services"]["frontend"]
    assert "LATENCY_DIAGNOSTICS_ENABLED=${LATENCY_DIAGNOSTICS_ENABLED:-false}" in backend["environment"]
    assert "LATENCY_DIAGNOSTICS_ENABLED" not in str(frontend.get("environment", {}))
    assert "ports" not in backend and backend["command"][-2:] == ["--workers", "1"]
    assert frontend["ports"] == ["127.0.0.1:${DEMO_PORT:-8080}:8080"]
    assert compose["volumes"]["dietagent_runtime"] == {"external": True, "name": "dietagent_demo_runtime"}
