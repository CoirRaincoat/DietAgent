"""Explicitly authored demo profiles with a recipe-only catalog loader.

The numeric fields below are invented fixture values, not copied or derived
from any supplied person. This module never loads the original profile JSON
or dialogue JSON. Recipe provenance remains the supplied recipe CSV.
"""

import csv
import hashlib
from io import StringIO
from pathlib import Path

from app.domain.models import UserProfile
from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, DataCatalog, normalize_recipes


def synthetic_profiles() -> dict[int, UserProfile]:
    """Return fresh, visibly synthetic profiles for three reproducible demos."""
    return {
        900001: UserProfile(
            user_id=900001, data_scope="synthetic", age=30, sex="未指定",
            height_cm=170, weight_kg=65, bmi=22.49,
            preferences=[], allergies=[], health_goals=[], special_groups=[],
            measurements={}, raw={},
        ),
        900002: UserProfile(
            user_id=900002, data_scope="synthetic", age=30, sex="未指定",
            height_cm=170, weight_kg=65, bmi=22.49,
            preferences=["清淡"], allergies=["海鲜", "花生"], health_goals=["控糖"],
            special_groups=[], measurements={}, raw={},
        ),
        900003: UserProfile(
            user_id=900003, data_scope="synthetic", age=30, sex="未指定",
            height_cm=170, weight_kg=65, bmi=22.49,
            preferences=["清淡"], allergies=[], health_goals=["降压", "护心"],
            special_groups=[], measurements={}, raw={},
        ),
    }


def load_synthetic_catalog(project_root: Path | None = None) -> DataCatalog:
    """Read exactly one dataset: the recipe CSV. No original profile fallback."""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    recipe_path = root / RECIPE_PATH
    raw_recipes = recipe_path.read_bytes()
    with StringIO(raw_recipes.decode("gb18030"), newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    profiles = synthetic_profiles()
    return DataCatalog(
        profiles=profiles, recipes=recipes, dialogues=[],
        quality_report={
            "schema_version": 1,
            "data_scope": "synthetic",
            "recipe_count": len(recipes),
            "eligible_recipe_count": sum(recipe.eligible for recipe in recipes.values()),
            "recipe_encoding": "gb18030",
            "profile_count": len(profiles),
            "dialogue_count": 0,
            "dialogue_turn_count": 0,
            "profile_provenance": "handwritten_synthetic_fixtures",
            "original_profiles_loaded": False,
            "original_dialogues_loaded": False,
            "sources": {
                "recipes": {
                    "path": RECIPE_PATH.as_posix(),
                    "sha256": hashlib.sha256(raw_recipes).hexdigest(),
                },
            },
            "limitations": [
                "演示画像及其身体指标均为手写虚构资料，不代表任何真实用户。",
                "菜谱来自原始菜谱 CSV；未加载原始健康档案或对话 JSON。",
                "合成健康目标用于检验约束与能力边界，不证明临床适用性或健康效果。",
            ],
        },
    )
