"""Archive guards run before startup and never contact a model."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_delivery_source.py"
COMMIT = "a" * 40


def run_checker(archive, out, commit=COMMIT):
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT),
            "--archive",
            str(archive),
            "--commit",
            commit,
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


@pytest.mark.parametrize(
    "member",
    [
        "../escaped.txt",
        ".env",
        "dataset/user_profile/profiles.json",
        "runtime/secrets.txt",
        "app/../../escaped.txt",
    ],
)
def test_preflight_rejects_unsafe_archive_before_extract_or_start(tmp_path, member):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.comment = COMMIT.encode("ascii")
        package.writestr(member, "must not extract")
    out = tmp_path / "evidence"
    result = run_checker(archive, out)
    assert result.returncode != 0
    assert not (out / "uvicorn.log").exists()
    assert not list((out / "source").rglob("*"))
    assert not (out / "escaped.txt").exists()


def test_preflight_rejects_mislabeled_archive_commit(tmp_path):
    archive = tmp_path / "mislabeled.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.comment = ("b" * 40).encode("ascii")
        package.writestr("app/safe.txt", "safe")
    result = run_checker(archive, tmp_path / "evidence")
    assert result.returncode != 0
    assert "Git archive commit mismatch" in result.stderr


def test_preflight_does_not_overwrite_existing_evidence(tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    marker = out / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    result = run_checker(tmp_path / "absent.zip", out)
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve"
