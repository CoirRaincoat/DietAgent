"""Start local demo images; use --build explicitly to rebuild them."""

import argparse
import os
import subprocess
from pathlib import Path

from app.infrastructure.settings import PROJECT_ROOT, Settings

PROJECT_NAME = "dietagent-demo"
VOLUME = "dietagent_demo_runtime"
IMAGES = ("dietagent:demo", "dietagent-frontend:demo")


class DemoStartError(Exception):
    """A fixed, non-sensitive startup failure."""


def docker_prefix(context: str | None) -> list[str]:
    return ["docker", "--context", context] if context else ["docker"]


def compose_prefix(project_root: Path, context: str | None) -> list[str]:
    return docker_prefix(context) + [
        "compose", "--project-name", PROJECT_NAME,
        "--project-directory", str(project_root),
        "--env-file", str(project_root / "configs" / "compose.env"),
        "--file", str(project_root / "docker-compose.yml"),
    ]


def run_command(command: list[str], environment: dict[str, str], timeout: int):
    try:
        return subprocess.run(
            command, env=environment, capture_output=True, text=True,
            shell=False, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise DemoStartError("Docker operation exceeded its time limit") from None
    except OSError:
        raise DemoStartError("Docker CLI could not be started") from None


def checked_command(
    command: list[str], environment: dict[str, str], timeout: int, stage: str,
) -> None:
    """Classify failures without echoing subprocess output or configuration."""
    try:
        result = run_command(command, environment, timeout)
    except DemoStartError as error:
        raise DemoStartError(f"{stage}: {error}") from None
    if result.returncode == 0:
        return
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if any(marker in output for marker in (
        "failed to fetch anonymous token", "failed to resolve source metadata",
        "auth.docker.io", "registry-1.docker.io", "i/o timeout",
        "tls handshake timeout", "proxyconnect", "context deadline exceeded",
        "unexpected eof", "no such host",
    )):
        detail = (
            "image registry or network access failed. Check Docker Desktop networking "
            "or the shell HTTP_PROXY/HTTPS_PROXY settings; cached images can be "
            "started without --build"
        )
    elif any(marker in output for marker in (
        "port is already allocated", "address already in use", "ports are not available",
    )):
        detail = "the requested local port is already in use; select another --port"
    elif any(marker in output for marker in ("unhealthy", "dependency failed to start")):
        detail = "a service did not become healthy; inspect its local Docker logs"
    elif any(marker in output for marker in (
        "cannot connect to the docker daemon", "is the docker daemon running",
        "error during connect", "dockerdesktoplinuxengine",
    )):
        detail = "Docker Engine is unavailable; start Docker Desktop and check --context"
    else:
        detail = "check the local Docker state; raw output was withheld to protect secrets"
    raise DemoStartError(f"{stage} failed (exit {result.returncode}): {detail}")


def start_demo(
    settings: Settings, *, port: int = 8080, context: str | None = None,
    build: bool = False, project_root: Path = PROJECT_ROOT,
) -> None:
    if not 1 <= port <= 65535:
        raise DemoStartError("Port must be between 1 and 65535")
    project_root = project_root.resolve()
    empty_env = project_root / "configs" / "compose.env"
    try:
        lines = empty_env.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise DemoStartError("The empty Compose environment file is missing") from None
    if any(line.strip() and not line.lstrip().startswith("#") for line in lines):
        raise DemoStartError("configs/compose.env must contain only comments or blank lines")
    if not (project_root / "docker-compose.yml").is_file():
        raise DemoStartError("docker-compose.yml is missing")

    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")
    }
    environment.update({
        "DEEPSEEK_API_KEY": settings.deepseek_api_key.get_secret_value(),
        "DEEPSEEK_BASE_URL": settings.deepseek_base_url,
        "DEEPSEEK_MODEL": settings.deepseek_model,
        "LLM_TIMEOUT_SECONDS": str(settings.llm_timeout_seconds),
        "SESSION_DB": "/app/runtime/sessions.sqlite3",
        "DEMO_PORT": str(port),
        "COMPOSE_DISABLE_ENV_FILE": "1",
    })
    docker = docker_prefix(context)
    compose = compose_prefix(project_root, context)
    print("Checking Docker Engine (up to 30 seconds).", flush=True)
    checked_command(
        docker + ["version", "--format", "{{.Server.Version}}"], environment, 30,
        "Docker Engine check",
    )
    if build:
        print("Building demo images (up to 900 seconds; registry access may be required).", flush=True)
        checked_command(
            compose + ["build", "backend", "frontend"], environment, 900, "Image build",
        )
        print("Image build completed.", flush=True)
    else:
        print("Checking existing local demo images; automatic build and pull are disabled.", flush=True)
        inspected_images = run_command(
            docker + ["image", "inspect", "--format", "{{.Id}}", *IMAGES], environment, 30,
        )
        if inspected_images.returncode != 0:
            raise DemoStartError(
                "Local demo images are unavailable. Run python -m evaluation.demo_start "
                "--build (include the same --context/--port options if used) for the first "
                "build, then retry without --build"
            )
    print("Checking the shared runtime volume.", flush=True)
    inspected = run_command(
        docker + ["volume", "inspect", "--format", "{{.Name}}", VOLUME], environment, 30,
    )
    if inspected.returncode != 0:
        created = run_command(docker + ["volume", "create", VOLUME], environment, 30)
        if created.returncode != 0:
            raise DemoStartError("Docker could not prepare the shared runtime volume")

    print("Starting the local demo images and waiting for health checks (up to 180 seconds).", flush=True)
    command = compose + [
        "up", "--detach", "--wait", "--wait-timeout", "120",
        "--no-build", "--pull", "never",
    ]
    checked_command(command, environment, 180, "Compose startup")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--context", default=None)
    parser.add_argument(
        "--build", action=argparse.BooleanOptionalAction, default=False,
        help="Explicitly build images before starting (default: reuse local images without pulling)",
    )
    args = parser.parse_args(argv)
    try:
        try:
            settings = Settings()
        except ValueError:
            raise DemoStartError("Invalid application configuration") from None
        start_demo(settings, port=args.port, context=args.context, build=args.build)
    except DemoStartError as error:
        print(str(error))
        return 1
    print(f"Demo: http://localhost:{args.port}")
    print(f"Showcase: http://localhost:{args.port}/demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
