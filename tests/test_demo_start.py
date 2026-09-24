import subprocess
from unittest.mock import Mock

import pytest
import yaml

from app.infrastructure.settings import PROJECT_ROOT, Settings
from evaluation import demo_start


@pytest.fixture
def config():
    return Settings(
        _env_file=None, deepseek_api_key="fake-compose-sensitive-key",
        deepseek_model="fake-model-setting", session_db="host-only.db",
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "compose.env").write_text("# Deliberately empty\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return tmp_path


def completed(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def test_reuses_volume_and_passes_secret_only_in_environment(config, project, monkeypatch):
    runner = Mock(side_effect=[completed()] * 4)
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    monkeypatch.setenv("COMPOSE_ENV_FILES", "untrusted.env")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    demo_start.start_demo(config, context="desktop-linux", project_root=project)
    assert runner.call_count == 4
    engine, images, first, second = runner.call_args_list
    assert engine.args[0][-3:] == ["version", "--format", "{{.Server.Version}}"]
    assert images.args[0][-2:] == list(demo_start.IMAGES)
    assert first.args[0][-5:] == [
        "volume", "inspect", "--format", "{{.Name}}", "dietagent_demo_runtime",
    ]
    command = second.args[0]
    assert command[:4] == ["docker", "--context", "desktop-linux", "compose"]
    assert command[command.index("--env-file") + 1] == str(project / "configs" / "compose.env")
    assert command[-3:] == ["--no-build", "--pull", "never"]
    assert "build" not in command and "--build" not in command
    assert "--wait" in command
    assert "fake-compose-sensitive-key" not in repr(command)
    assert "fake-model-setting" not in repr(command)
    assert second.kwargs["shell"] is False
    environment = second.kwargs["env"]
    assert environment["DEEPSEEK_API_KEY"] == "fake-compose-sensitive-key"
    assert environment["SESSION_DB"] == "/app/runtime/sessions.sqlite3"
    assert environment["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert "COMPOSE_ENV_FILES" not in environment
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_creates_missing_volume_without_removing_containers(config, project, monkeypatch):
    runner = Mock(side_effect=[completed(), completed(), completed(1), completed(), completed()])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    demo_start.start_demo(config, port=8091, build=False, project_root=project)
    commands = [call.args[0] for call in runner.call_args_list]
    assert commands[3] == ["docker", "volume", "create", "dietagent_demo_runtime"]
    assert commands[4][-3:] == ["--no-build", "--pull", "never"]
    assert runner.call_args.kwargs["env"]["DEMO_PORT"] == "8091"
    assert all(not set(command).intersection({"rm", "down", "stop", "prune"}) for command in commands)


def test_volume_failure_stops_before_compose(config, project, monkeypatch):
    runner = Mock(side_effect=[completed(), completed(), completed(1), completed(1)])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError, match="runtime volume"):
        demo_start.start_demo(config, project_root=project)
    assert runner.call_count == 4


def test_compose_failure_never_prints_captured_secret(config, project, monkeypatch, capsys):
    monkeypatch.setattr(demo_start, "Settings", lambda: config)
    actual_start = demo_start.start_demo
    monkeypatch.setattr(
        demo_start, "start_demo",
        lambda settings, **kwargs: actual_start(settings, project_root=project, **kwargs),
    )
    runner = Mock(side_effect=[
        completed(), completed(), completed(),
        completed(1, stderr="fake-compose-sensitive-key fake-model-setting"),
    ])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    assert demo_start.main(["--no-build", "--port", "8091"]) == 1
    output = capsys.readouterr()
    assert "fake-compose-sensitive-key" not in output.out + output.err
    assert "fake-model-setting" not in output.out + output.err
    assert "Compose startup failed" in output.out


def test_explicit_build_is_separate_from_startup(config, project, monkeypatch):
    runner = Mock(side_effect=[completed()] * 4)
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    demo_start.start_demo(config, project_root=project, build=True)
    commands = [call.args[0] for call in runner.call_args_list]
    assert commands[1][-3:] == ["build", "backend", "frontend"]
    assert runner.call_args_list[1].kwargs["timeout"] == 900
    assert commands[3][-3:] == ["--no-build", "--pull", "never"]
    assert runner.call_args_list[3].kwargs["timeout"] == 180
    assert all("--build" not in command for command in commands)


def test_missing_local_images_requires_explicit_build(config, project, monkeypatch):
    runner = Mock(side_effect=[completed(), completed(1, stderr="No such image")])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError, match="demo_start --build"):
        demo_start.start_demo(config, project_root=project)
    assert runner.call_count == 2


def test_build_network_failure_is_sanitized_and_does_not_start(config, project, monkeypatch):
    runner = Mock(side_effect=[completed(), completed(1, stderr=(
        "failed to fetch anonymous token: auth.docker.io: i/o timeout "
        "fake-compose-sensitive-key fake-model-setting"
    ))])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError) as error:
        demo_start.start_demo(config, project_root=project, build=True)
    assert "Image build failed" in str(error.value)
    assert "network access failed" in str(error.value)
    assert "HTTP_PROXY/HTTPS_PROXY" in str(error.value)
    assert "fake-compose-sensitive-key" not in str(error.value)
    assert "fake-model-setting" not in str(error.value)
    assert runner.call_count == 2


@pytest.mark.parametrize(("stderr", "message"), [
    ("Bind: address already in use fake-compose-sensitive-key", "local port"),
    ("container is unhealthy fake-compose-sensitive-key", "did not become healthy"),
])
def test_startup_failure_has_safe_category(config, project, monkeypatch, stderr, message):
    runner = Mock(side_effect=[completed(), completed(), completed(), completed(1, stderr=stderr)])
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError) as error:
        demo_start.start_demo(config, project_root=project)
    assert "Compose startup failed" in str(error.value)
    assert message in str(error.value)
    assert "fake-compose-sensitive-key" not in str(error.value)


def test_engine_failure_stops_before_build_or_start(config, project, monkeypatch):
    runner = Mock(return_value=completed(1, stderr="Cannot connect to the Docker daemon"))
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError, match="Docker Engine is unavailable"):
        demo_start.start_demo(config, project_root=project, build=True)
    assert runner.call_count == 1


@pytest.mark.parametrize(("argv", "build"), [([], False), (["--no-build"], False), (["--build"], True)])
def test_cli_defaults_to_cached_images_and_retains_flags(config, monkeypatch, argv, build):
    monkeypatch.setattr(demo_start, "Settings", lambda: config)
    start = Mock()
    monkeypatch.setattr(demo_start, "start_demo", start)
    assert demo_start.main(argv) == 0
    start.assert_called_once_with(config, port=8080, context=None, build=build)


def test_timeout_is_sanitized(config, project, monkeypatch):
    runner = Mock(side_effect=subprocess.TimeoutExpired(
        ["docker"], 30, output="fake-compose-sensitive-key",
    ))
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError) as error:
        demo_start.start_demo(config, project_root=project)
    assert "fake-compose-sensitive-key" not in str(error.value)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_does_not_run_docker(config, project, monkeypatch, port):
    runner = Mock()
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError, match="Port"):
        demo_start.start_demo(config, port=port, project_root=project)
    runner.assert_not_called()


def test_nonempty_compose_env_is_rejected_without_echo(config, project, monkeypatch):
    (project / "configs" / "compose.env").write_text(
        "DEEPSEEK_API_KEY=fake-compose-sensitive-key\n", encoding="utf-8",
    )
    runner = Mock()
    monkeypatch.setattr(demo_start.subprocess, "run", runner)
    with pytest.raises(demo_start.DemoStartError) as error:
        demo_start.start_demo(config, project_root=project)
    assert "fake-compose-sensitive-key" not in str(error.value)
    runner.assert_not_called()


def test_compose_keeps_api_internal_and_uses_existing_external_volume():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "ports" not in compose["services"]["backend"]
    assert compose["services"]["frontend"]["ports"] == ["127.0.0.1:${DEMO_PORT:-8080}:8080"]
    volume = compose["volumes"]["dietagent_runtime"]
    assert volume["external"] is True and volume["name"] == "dietagent_demo_runtime"
    assert compose["services"]["backend"]["command"][-2:] == ["--workers", "1"]
