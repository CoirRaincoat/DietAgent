"""Public invented profile controls; no original clinical values in tests."""

from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.private_matrix as private_matrix
from app.domain.models import UserProfile
from app.infrastructure.data import normalize_profile


def public_profile() -> dict:
    return {
        "id": 99991,
        "性别": "未指定",
        "年龄": 30,
        "劳动强度": "轻体力",
        "特殊人群": [],
        "孕周期": "",
        "口味偏好": "清淡",
        "过敏食材": ["虾"],
        "健康需求": ["降压"],
        "身高_cm": 170,
        "体重_kg": 65,
        "BMI": 22.49,
    }


FIELDS = {"身高_cm": "height_cm", "体重_kg": "weight_kg", "BMI": "bmi"}
SUBSETS = [tuple(group) for count in range(4) for group in combinations(FIELDS, count)]


@pytest.mark.parametrize("missing", SUBSETS)
def test_each_missing_measurement_is_null_not_default_or_inferred(missing: tuple[str, ...]) -> None:
    raw = public_profile()
    for field in missing:
        raw.pop(field)
    before = dict(raw)
    profile = normalize_profile(raw)
    for source, normalized in FIELDS.items():
        assert getattr(profile, normalized) == (None if source in missing else raw[source])
    assert raw == before and profile.raw == before
    assert profile.user_id == 99991 and profile.allergies == ["虾"]
    assert profile.preferences == ["清淡"] and profile.health_goals == ["降压"]
    assert UserProfile.model_validate(profile.model_dump()).model_dump() == profile.model_dump()


@pytest.mark.parametrize("field", list(FIELDS))
def test_explicit_null_measurement_stays_unknown(field: str) -> None:
    raw = public_profile()
    raw[field] = None
    assert getattr(normalize_profile(raw), FIELDS[field]) is None


def test_present_source_measurements_are_not_recomputed() -> None:
    raw = public_profile()
    raw["BMI"] = 24.25
    profile = normalize_profile(raw)
    assert profile.bmi == 24.25 and profile.height_cm == 170 and profile.weight_kg == 65


@pytest.mark.parametrize("field", list(FIELDS))
def test_invalid_present_measurement_is_not_silently_missing(field: str) -> None:
    raw = public_profile()
    raw[field] = "无法确认的数字"
    with pytest.raises(ValueError):
        normalize_profile(raw)


def test_minimal_profile_contract_preserves_missing_values() -> None:
    profile = UserProfile(user_id=99991, age=30, sex="未指定")
    assert profile.height_cm is profile.weight_kg is profile.bmi is None


def test_archive_without_git_cannot_inherit_parent_head(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(private_matrix, "__file__", str(tmp_path / "evaluation/private_matrix.py"))

    def forbidden(*args, **kwargs):
        raise AssertionError("An unversioned archive must not walk to its parent checkout")

    monkeypatch.setattr(private_matrix.subprocess, "run", forbidden)
    assert private_matrix._git_commit() == "unknown"


def test_real_worktree_git_file_uses_exact_project_root(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").write_text("gitdir: synthetic-test-only", encoding="utf-8")
    monkeypatch.setattr(private_matrix, "__file__", str(tmp_path / "evaluation/private_matrix.py"))

    def identified(*args, **kwargs):
        assert kwargs["cwd"] == tmp_path
        return SimpleNamespace(stdout="a" * 40 + "\n")

    monkeypatch.setattr(private_matrix.subprocess, "run", identified)
    assert private_matrix._git_commit() == "a" * 40
