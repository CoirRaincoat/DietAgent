import csv
import hashlib
from pathlib import Path

from app.infrastructure import data
from app.infrastructure.synthetic import load_synthetic_catalog, synthetic_profiles


def test_demo_profiles_are_explicitly_synthetic_fresh_and_have_no_original_details():
    profiles = synthetic_profiles()
    assert set(profiles) == {900001, 900002, 900003}
    assert all(profile.data_scope == "synthetic" for profile in profiles.values())
    assert all(profile.raw == {} and profile.measurements == {} for profile in profiles.values())
    assert profiles[900001].allergies == profiles[900001].health_goals == []
    assert profiles[900002].allergies == ["海鲜", "花生"]
    assert profiles[900002].health_goals == ["控糖"]
    assert profiles[900003].health_goals == ["降压", "护心"]
    profiles[900002].allergies.append("人工测试变更")
    assert synthetic_profiles()[900002].allergies == ["海鲜", "花生"]


def test_synthetic_loader_reads_only_recipe_csv_never_original_health_or_dialogues(monkeypatch):
    original_open = Path.open
    recipe_path = (data.PROJECT_ROOT / data.RECIPE_PATH).resolve()
    forbidden = {(data.PROJECT_ROOT / relative).resolve()
                 for relative in (data.PROFILE_PATH, data.DIALOGUE_PATH)}
    opened = []

    def guarded_open(path, *args, **kwargs):
        resolved = path.resolve()
        assert resolved not in forbidden, "Synthetic loader read original personal data"
        assert resolved == recipe_path, "Synthetic loader read an unexpected file"
        opened.append(resolved)
        return original_open(path, *args, **kwargs)

    def forbidden_catalog(*args, **kwargs):
        raise AssertionError("Synthetic loader must not call original load_catalog")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(data, "load_catalog", forbidden_catalog)
    catalog = load_synthetic_catalog()
    assert opened == [recipe_path]
    assert len(catalog.recipes) == 2000
    assert len(catalog.profiles) == 3
    assert catalog.dialogues == []
    assert catalog.quality_report["data_scope"] == "synthetic"
    assert catalog.quality_report["original_profiles_loaded"] is False
    assert catalog.quality_report["original_dialogues_loaded"] is False
    assert set(catalog.quality_report["sources"]) == {"recipes"}
    assert catalog.quality_report["sources"]["recipes"]["sha256"] == (
        "b2177dc6cdcae24fc5671c8dada44295228f4301e3ce620ed11d51b1abfe4371"
    )


def test_synthetic_loader_works_without_any_original_json_files(tmp_path):
    recipe_path = tmp_path / data.RECIPE_PATH
    recipe_path.parent.mkdir(parents=True)
    with recipe_path.open("w", encoding="gb18030", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=data.RECIPE_FIELDS)
        writer.writeheader()
        writer.writerow({"名称": "清蒸南瓜", "食材清单": "南瓜200克；清水500毫升",
                         "烹饪步骤": "将南瓜蒸熟。", "label": "晚餐、清淡"})
    catalog = load_synthetic_catalog(tmp_path)
    assert len(catalog.recipes) == 1
    recipe = next(iter(catalog.recipes.values()))
    assert recipe.name == "清蒸南瓜"
    assert recipe.source_row == 2
    assert recipe.raw_ingredients == "南瓜200克；清水500毫升"
    assert catalog.quality_report["sources"]["recipes"]["sha256"] == (
        hashlib.sha256(recipe_path.read_bytes()).hexdigest()
    )
    assert not (tmp_path / data.PROFILE_PATH).exists()
    assert not (tmp_path / data.DIALOGUE_PATH).exists()
