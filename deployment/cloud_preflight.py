"""Offline cloud configuration checks. Never prints values or calls Docker/network."""

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


class PreflightError(ValueError):
    """Fixed diagnostics without private input values."""


def read_environment(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise PreflightError("Private environment file is unavailable") from None
    result = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise PreflightError("Invalid environment entry; use KEY=value")
        if key in result:
            raise PreflightError("Duplicate environment key")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise PreflightError("Unclosed environment quote")
            value = value[1:-1]
        if "$" in value or "\x00" in value:
            raise PreflightError("Environment interpolation is not supported")
        result[key] = value
    return result


def check_environment(values: dict[str, str], *, https: bool) -> dict[str, object]:
    required = ("BACKEND_IMAGE", "GATEWAY_IMAGE", "DEEPSEEK_API_KEY", "PUBLIC_API_TOKEN", "LOCAL_PROFILE_HOST_PATH")
    if any(not values.get(key, "").strip() for key in required):
        raise PreflightError("Required cloud configuration is missing")
    for key in ("BACKEND_IMAGE", "GATEWAY_IMAGE"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*:[A-Za-z0-9_.-]+", values[key]) or "REPLACE" in values[key]:
            raise PreflightError("Frozen image tags are required")
    token = values["PUBLIC_API_TOKEN"]
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise PreflightError("API token must be 64 lowercase hexadecimal characters")
    if token == values["DEEPSEEK_API_KEY"]:
        raise PreflightError("Submission token must be separate from the model key")
    try:
        endpoint = urlsplit(values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    except ValueError:
        raise PreflightError("Model base URL is malformed") from None
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise PreflightError("Model base URL must use HTTPS without credentials/query")
    volume = values.get("CLOUD_RUNTIME_VOLUME", "dietagent_cloud_runtime")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", volume) or volume == "dietagent_demo_runtime":
        raise PreflightError("Cloud runtime volume must be isolated from the local demo")
    try:
        port = int(values.get("CLOUD_LOCAL_PORT", "18080"))
        timeout = float(values.get("LLM_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise PreflightError("Invalid port or timeout") from None
    if not 1024 <= port <= 65535 or port == 8080 or not 0 < timeout <= 120:
        raise PreflightError("Cloud port/timeout violates the isolation boundary")
    if https:
        domain = values.get("CLOUD_DOMAIN", "")
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain):
            raise PreflightError("HTTPS requires a real DNS domain")
        if domain.endswith((".example", ".test", ".invalid", ".localhost")) or domain in {"example.com", "example.org", "example.net"}:
            raise PreflightError("Replace the reserved example domain")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise PreflightError("Use a DNS domain for this HTTPS configuration")
    profile_path = Path(values["LOCAL_PROFILE_HOST_PATH"])
    if not profile_path.is_absolute() or not profile_path.is_file():
        raise PreflightError("Official profile path must be an existing absolute file")
    try:
        rows = json.loads(profile_path.read_text(encoding="utf-8-sig"))
        ids = [row["id"] for row in rows]
        valid = (
            isinstance(rows, list) and len(rows) == 50
            and all(type(user_id) is int for user_id in ids)
            and set(ids) == set(range(1, 51))
            and all("年龄" in row and "性别" in row for row in rows)
        )
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise PreflightError("Profile source must contain exactly official IDs 1-50 and required fields")
    return {"status": "configuration_checked_offline", "profiles": 50, "https_requested": https,
            "network_requests": 0, "model_requests": 0, "deployment_started": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--https", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check_environment(read_environment(args.env_file), https=args.https)
    except PreflightError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
