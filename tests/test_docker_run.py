import subprocess
from unittest.mock import Mock

import pytest

from app.infrastructure.settings import Settings
from evaluation import docker_run


@pytest.fixture
def config():
    return Settings(
        _env_file=None, deepseek_api_key="fake-sensitive-test-key",
        deepseek_model="synthetic-test-model", session_db="host-only.sqlite3",
    )


def test_persistent_launcher_uses_loopback_volume_and_names_only(config, monkeypatch):
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="a" * 64, stderr=""))
    monkeypatch.setattr(docker_run.subprocess, "run", runner)
    result = docker_run.launch_container(config, "test-demo", 8080, "desktop-linux", False)
    command = runner.call_args.args[0]
    assert result["container_id"] == "a" * 64
    assert command[:3] == ["docker", "--context", "desktop-linux"]
    assert "127.0.0.1:8080:8000" in command
    assert "type=volume,source=dietagent_demo_runtime,target=/app/runtime" in command
    assert "fake-sensitive-test-key" not in repr(command)
    assert "synthetic-test-model" not in repr(command)
    assert command[-1] == "dietagent:demo"
    assert runner.call_args.kwargs["shell"] is False
    child_env = runner.call_args.kwargs["env"]
    assert child_env["DEEPSEEK_API_KEY"] == "fake-sensitive-test-key"
    assert child_env["SESSION_DB"] == "/app/runtime/sessions.sqlite3"
    supplied = [command[i + 1] for i, token in enumerate(command) if token == "--env"]
    assert supplied == list(docker_run.ENV_NAMES)


def test_ephemeral_launcher_has_no_volume_and_temporary_loopback_port():
    command = docker_run.build_command("temporary-check", 0, None, True)
    assert "--mount" not in command and "--rm" in command
    assert "127.0.0.1::8000" in command
    assert command[:2] == ["docker", "run"]


@pytest.mark.parametrize("name,port", [("bad name", 8000), ("--oops", 8000), ("ok", -1), ("ok", 65536)])
def test_invalid_arguments_do_not_start_a_subprocess(config, monkeypatch, name, port):
    runner = Mock()
    monkeypatch.setattr(docker_run.subprocess, "run", runner)
    with pytest.raises(docker_run.DockerLaunchError):
        docker_run.launch_container(config, name, port, None, True)
    runner.assert_not_called()


def test_cli_never_prints_configuration_or_captured_docker_errors(config, monkeypatch, capsys):
    monkeypatch.setattr(docker_run, "Settings", lambda: config)
    runner = Mock(return_value=subprocess.CompletedProcess(
        [], 1, stdout="", stderr="fake-sensitive-test-key synthetic-test-model",
    ))
    monkeypatch.setattr(docker_run.subprocess, "run", runner)
    assert docker_run.main(["--ephemeral"]) == 1
    output = capsys.readouterr()
    assert "fake-sensitive-test-key" not in output.out + output.err
    assert "synthetic-test-model" not in output.out + output.err
    assert "Docker launch failed" in output.out
