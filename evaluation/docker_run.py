"""Start the local demo container without placing credentials in CLI arguments."""

import argparse
import os
import re
import subprocess

from app.infrastructure.settings import Settings

IMAGE = "dietagent:demo"
VOLUME = "dietagent_demo_runtime"
ENV_NAMES = (
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "LLM_TIMEOUT_SECONDS", "SESSION_DB",
)


class DockerLaunchError(Exception):
    """A non-sensitive deployment failure."""


def build_command(
    name: str, port: int, context: str | None, ephemeral: bool,
) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise DockerLaunchError("Invalid container name")
    if not 0 <= port <= 65535:
        raise DockerLaunchError("Port must be between 0 and 65535")
    command = ["docker"]
    if context:
        command.extend(["--context", context])
    command.extend([
        "run", "--detach", "--pull", "never", "--name", name,
        "--publish", f"127.0.0.1:{port if port else ''}:8000",
    ])
    if ephemeral:
        command.append("--rm")
    else:
        command.extend([
            "--mount", f"type=volume,source={VOLUME},target=/app/runtime",
        ])
    for variable in ENV_NAMES:
        command.extend(["--env", variable])
    command.append(IMAGE)
    return command


def launch_container(
    settings: Settings, name: str, port: int, context: str | None, ephemeral: bool,
) -> dict[str, str]:
    command = build_command(name, port, context, ephemeral)
    child_env = os.environ.copy()
    child_env.update({
        "DEEPSEEK_API_KEY": settings.deepseek_api_key.get_secret_value(),
        "DEEPSEEK_BASE_URL": settings.deepseek_base_url,
        "DEEPSEEK_MODEL": settings.deepseek_model,
        "LLM_TIMEOUT_SECONDS": str(settings.llm_timeout_seconds),
        "SESSION_DB": "/app/runtime/sessions.sqlite3",
    })
    try:
        result = subprocess.run(
            command, env=child_env, capture_output=True, text=True, shell=False, check=False,
        )
    except OSError:
        raise DockerLaunchError("Docker CLI could not be started") from None
    if result.returncode != 0:
        raise DockerLaunchError(
            f"Docker launch failed (exit {result.returncode}); "
            "check the engine, local image, container name, and port"
        )
    container_id = result.stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise DockerLaunchError("Docker did not return a valid container ID")
    return {"container_id": container_id, "name": name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="dietagent-demo")
    parser.add_argument("--port", type=int, default=8080, help="Local port; 0 selects a temporary port")
    parser.add_argument("--context", default=None)
    parser.add_argument("--ephemeral", action="store_true", help="No volume; remove on container exit")
    args = parser.parse_args(argv)
    try:
        try:
            settings = Settings()
        except ValueError:
            raise DockerLaunchError("Invalid application configuration") from None
        result = launch_container(
            settings, args.name, args.port, args.context, args.ephemeral,
        )
    except DockerLaunchError as error:
        print(str(error))
        return 1
    print("Started container " + result["container_id"])
    print("Container name: " + result["name"])
    if args.port:
        print(f"Local URL: http://localhost:{args.port}")
    else:
        print("A temporary loopback port was assigned; use docker port to view it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
