"""Synthetic configuration counterexamples; no purchases, Docker, HTTP or DNS."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cloud_preflight", ROOT / "deployment/cloud_preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@pytest.fixture
def values(tmp_path):
    rows = [{"id": i, "年龄": 30, "性别": "未指定"} for i in range(50, 0, -1)]
    source = tmp_path / "synthetic-original-shaped-profiles.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8-sig")
    return {
        "BACKEND_IMAGE": "dietagent-delivery:synthetic-frozen",
        "GATEWAY_IMAGE": "dietagent-gateway:synthetic-frozen",
        "DEEPSEEK_API_KEY": "SENSITIVE-synthetic-model-key",
        "PUBLIC_API_TOKEN": "a" * 64,
        "LOCAL_PROFILE_HOST_PATH": str(source),
        "CLOUD_DOMAIN": "diet.synthetic-host.cn",
    }


def test_valid_configuration_is_only_checked_offline_and_keeps_profile_bytes(values):
    source = Path(values["LOCAL_PROFILE_HOST_PATH"])
    before = source.read_bytes()
    result = preflight.check_environment(values, https=True)
    assert result == {"status": "configuration_checked_offline", "profiles": 50, "https_requested": True,
                      "network_requests": 0, "model_requests": 0, "deployment_started": False}
    assert source.read_bytes() == before


def test_public_ip_mode_accepts_no_domain_but_does_not_satisfy_https(values):
    values["CLOUD_DOMAIN"] = ""
    source = Path(values["LOCAL_PROFILE_HOST_PATH"])
    before = source.read_bytes()
    result = preflight.check_environment(values, https=False)
    assert result["https_requested"] is False
    assert result["profiles"] == 50
    assert result["network_requests"] == result["model_requests"] == 0
    with pytest.raises(preflight.PreflightError, match="domain"):
        preflight.check_environment(values, https=True)
    assert source.read_bytes() == before


@pytest.mark.parametrize("domain", ["", "example.com", "diet.example", "127.0.0.1", "localhost", "a.cn\nrespond sensitive", "bad domain.cn"])
def test_https_rejects_reserved_addresses_and_configuration_injection(values, domain):
    values["CLOUD_DOMAIN"] = domain
    with pytest.raises(preflight.PreflightError):
        preflight.check_environment(values, https=True)


@pytest.mark.parametrize("key,value", [
    ("PUBLIC_API_TOKEN", '"; return 200; #'),
    ("PUBLIC_API_TOKEN", ""),
    ("CLOUD_RUNTIME_VOLUME", "dietagent_demo_runtime"),
    ("CLOUD_LOCAL_PORT", "8080"),
    ("CLOUD_LOCAL_PORT", "1"),
    ("BACKEND_IMAGE", "dietagent-delivery:REPLACE_WITH_TAG"),
    ("DEEPSEEK_BASE_URL", "http://unsafe.invalid"),
    ("DEEPSEEK_BASE_URL", "https://secret:password@model.invalid"),
    ("DEEPSEEK_BASE_URL", "https://[bad"),
])
def test_secrets_ports_and_volumes_cannot_weaken_the_delivery_boundary(values, key, value):
    values[key] = value
    with pytest.raises(preflight.PreflightError) as error:
        preflight.check_environment(values, https=False)
    assert "SENSITIVE" not in str(error.value) and "password" not in str(error.value)


@pytest.mark.parametrize("change", ["missing", "duplicate", "string", "bool"])
def test_official_ids_never_remap_or_silently_fallback(values, change):
    source = Path(values["LOCAL_PROFILE_HOST_PATH"])
    rows = json.loads(source.read_text(encoding="utf-8-sig"))
    if change == "missing":
        rows.pop()
    else:
        rows[0]["id"] = {"duplicate": 1, "string": "50", "bool": True}[change]
    source.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(preflight.PreflightError):
        preflight.check_environment(values, https=False)


def test_cli_never_echoes_secret_values_on_success_or_failure(tmp_path, values, capsys):
    env = tmp_path / ".env.cloud"
    env.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    assert preflight.main(["--env-file", str(env), "--https"]) == 0
    assert "SENSITIVE" not in repr(capsys.readouterr())
    env.write_text("PUBLIC_API_TOKEN=SENSITIVE-invalid\n", encoding="utf-8")
    assert preflight.main(["--env-file", str(env)]) == 1
    assert "SENSITIVE" not in repr(capsys.readouterr())


def test_submission_and_model_keys_are_independent(values):
    values["DEEPSEEK_API_KEY"] = values["PUBLIC_API_TOKEN"]
    with pytest.raises(preflight.PreflightError, match="separate"):
        preflight.check_environment(values, https=False)


@pytest.mark.parametrize("text", ["KEY=first\nKEY=second", "BAD KEY=SENSITIVE", "KEY='SENSITIVE", "KEY=${SENSITIVE}"])
def test_ambiguous_env_files_are_rejected_without_values(tmp_path, text):
    env = tmp_path / ".env.cloud"
    env.write_text(text, encoding="utf-8")
    with pytest.raises(preflight.PreflightError) as error:
        preflight.read_environment(env)
    assert "SENSITIVE" not in str(error.value)


def test_cloud_compose_does_not_publish_backend_or_reuse_demo_volume():
    compose = yaml.safe_load((ROOT / "deployment/compose.cloud.yml").read_text(encoding="utf-8"))
    backend = compose["services"]["backend"]
    assert "ports" not in backend
    assert backend["environment"]["LOCAL_PROFILE_PATH"] == "/local-input/profiles.json"
    source = next(volume for volume in backend["volumes"] if isinstance(volume, dict))
    assert source["read_only"] is True and source["bind"]["create_host_path"] is False
    assert compose["services"]["gateway"]["ports"] == ["127.0.0.1:${CLOUD_LOCAL_PORT:-18080}:8080"]
    assert "dietagent_demo_runtime" not in str(compose["volumes"])
    assert compose["services"]["backend"]["environment"]["ALLOW_RECIPE_GENERATION"] == "false"
