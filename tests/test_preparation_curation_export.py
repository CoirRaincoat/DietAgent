"""Source-bound assistant registry export, not human labels or serving approval."""

import csv
import hashlib
import json

import pytest
from pydantic import ValidationError

from app.domain.preparation_enrichment import audit_preparation
from app.infrastructure.data import normalize_recipes
from app.infrastructure.preparation_curations import load_preparation_curations
from pipelines.enrich_recipes import export_review, main


def public_inputs(tmp_path):
    rows = [
        {
            "名称": "公开豆馅面包",
            "食材清单": "面粉；水；酵母；豆沙馅",
            "烹饪步骤": "第1步：面粉、水、酵母揉成面团后发酵。",
            "label": "",
        },
        {"名称": "蒸白菜", "食材清单": "白菜；盐", "烹饪步骤": "白菜蒸熟。", "label": ""},
    ]
    source = tmp_path / "public.csv"
    with source.open("w", encoding="gb18030", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    recipes = normalize_recipes(rows)
    recipe = next(value for value in recipes.values() if value.name == "公开豆馅面包")
    original = audit_preparation(recipe)
    entry = {
        "recipe_id": recipe.recipe_id,
        "source_name": recipe.name,
        "source_fingerprint": original.source_fingerprint,
        "source_content_sha256": original.source_content_sha256,
        "disposition": "supplement_draft",
        "reason": "公开样例只有发酵，豆沙馅未使用；补充内容由助手建议，不是源文恢复。",
        "additions": [
            {
                "placement": "after_source",
                "text": "排气整形，包入已列出的豆沙馅，收口后再次醒发并烘烤。",
                "ingredient_names": ["豆沙馅"],
            }
        ],
        "tag_suggestions": [
            {"tag": "含豆沙的面团制品（缺烘烤步骤）", "dimension": "culinary_form"}
        ],
        "remaining_questions": ["设备参数和成品效果尚未试做"],
        "reviewer_kind": "assistant",
    }
    return source, recipes, entry


def registry_file(tmp_path, entries, **changes):
    path = tmp_path / "assistant_registry.json"
    value = {"policy_version": "assistant-preparation-curation-v1", "records": entries}
    value.update(changes)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_selected_curation_packet_retains_source_and_before_findings(tmp_path):
    source, recipes, entry = public_inputs(tmp_path)
    registry = registry_file(tmp_path, [entry])
    before_bytes = source.read_bytes()
    registry_bytes = registry.read_bytes()
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=2, curation_path=registry)
    assert summary["assistant_curation_records"] == 1
    assert summary["assistant_curation_disposition_counts"] == {"supplement_draft": 1}
    assert summary["assistant_curation_sha256"] == hashlib.sha256(registry_bytes).hexdigest()
    assert summary["preparation_counts_before_curation"] == {
        "review_required": 1,
        "keep_source": 1,
    }
    assert summary["preparation_counts"] == {"draft_supplement": 1, "keep_source": 1}
    assert summary["original_findings_retained"]
    assert summary["human_labels_filled"] == 0
    assert summary["provider_calls"] == 0 and not summary["production_applied"]
    assert source.read_bytes() == before_bytes and registry.read_bytes() == registry_bytes
    records = jsonl(output / "recipes_enriched_review.jsonl")
    selected = next(row for row in records if row["source"]["recipe_id"] == entry["recipe_id"])
    assert selected["source"] == recipes[entry["recipe_id"]].model_dump(mode="json")
    assert selected["preparation_before_curation"]["findings"]
    assert (
        selected["preparation_review"]["findings"]
        == selected["preparation_before_curation"]["findings"]
    )
    assert selected["source"]["steps"] in selected["supplemented_steps_REVIEW_ONLY"]
    curated = jsonl(output / "assistant_curations.jsonl")
    assert len(curated) == 1 and curated[0]["human_review"] == "not_reviewed"
    assert curated[0]["assistant_curation"]["remaining_questions"] == entry["remaining_questions"]
    results = jsonl(output / "case_results.jsonl")
    assert len(results) == 2
    assert all(
        row["human_quality_score"] is None and row["model_quality_score"] is None for row in results
    )
    notes = jsonl(output / "review_notes_blank.jsonl")
    assert all(
        row["preparation_approved"] is None and row["tags_approved"] is None for row in notes
    )
    assert all(not row["reviewer"] and not row["comments"] for row in notes)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert "assistant_curations.jsonl" in manifest
    assert "evidence/assistant_registry.json" in manifest
    assert "evidence/preparation_curations.py" in manifest
    assert (output / "evidence/assistant_registry.json").read_bytes() == registry_bytes
    assert all(
        hashlib.sha256((output / path).read_bytes()).hexdigest() == sha
        for path, sha in manifest.items()
    )
    for report in (output / "report.md", output / "report.html"):
        content = report.read_text(encoding="utf-8")
        assert entry["reason"] in content
        assert entry["remaining_questions"][0] in content
    assert (output / "report.html").read_text(encoding="utf-8").count(
        '<details class="recipe">'
    ) == 2


@pytest.mark.parametrize(
    "field", ["recipe_id", "source_name", "source_fingerprint", "source_content_sha256"]
)
def test_stale_registry_rejects_all_entries_before_output_creation(tmp_path, field):
    source, _, entry = public_inputs(tmp_path)
    stale = dict(entry)
    stale[field] = (
        "f" * 64 if field.endswith("fingerprint") or field.endswith("sha256") else "unrelated"
    )
    registry = registry_file(tmp_path, [stale])
    output = tmp_path / "packet"
    with pytest.raises(ValueError):
        export_review(source, output, expected_count=2, curation_path=registry)
    assert not output.exists()


def test_duplicate_registry_rejects_before_output_creation(tmp_path):
    source, _, entry = public_inputs(tmp_path)
    registry = registry_file(tmp_path, [entry, entry])
    output = tmp_path / "packet"
    with pytest.raises(ValueError, match="Duplicate"):
        export_review(source, output, expected_count=2, curation_path=registry)
    assert not output.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"policy_version": "human-reviewed-v1"},
        {"human_approved": True},
        {"records": [{"recipe_id": "unbound"}]},
    ],
)
def test_registry_schema_fails_closed_before_output(tmp_path, changes):
    source, _, entry = public_inputs(tmp_path)
    registry = registry_file(tmp_path, [entry], **changes)
    output = tmp_path / "packet"
    with pytest.raises(ValidationError):
        export_review(source, output, expected_count=2, curation_path=registry)
    assert not output.exists()


@pytest.mark.parametrize(
    "disposition", ["retain_component", "source_conflict", "needs_confirmation"]
)
def test_unresolved_decisions_not_misreported_as_fixed_or_human_passed(tmp_path, disposition):
    source, _, entry = public_inputs(tmp_path)
    entry.update(disposition=disposition, additions=[])
    registry = registry_file(tmp_path, [entry])
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=2, curation_path=registry)
    assert summary["assistant_curation_disposition_counts"] == {disposition: 1}
    assert summary["preparation_counts"]["review_required"] == 1
    assert not jsonl(output / "preparation_drafts.jsonl")
    assert len(jsonl(output / "manual_queue.jsonl")) == 1
    decision = jsonl(output / "assistant_curations.jsonl")[0]
    assert decision["after_status"] == "review_required"
    assert decision["human_review"] == "not_reviewed"


def test_partial_conflict_draft_is_also_in_manual_queue(tmp_path):
    source, _, entry = public_inputs(tmp_path)
    entry["disposition"] = "source_conflict"
    registry = registry_file(tmp_path, [entry])
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=2, curation_path=registry)
    assert summary["preparation_counts"]["review_required"] == 1
    assert len(jsonl(output / "preparation_drafts.jsonl")) == 1
    assert len(jsonl(output / "manual_queue.jsonl")) == 1


def test_custom_csv_export_without_registry_preserves_finite_baseline(tmp_path):
    source, _, _ = public_inputs(tmp_path)
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=2)
    assert summary["assistant_curation_records"] == 0
    assert summary["assistant_curation_sha256"] is None
    assert summary["preparation_counts"] == summary["preparation_counts_before_curation"]
    assert not jsonl(output / "assistant_curations.jsonl")


def test_cli_explicit_no_curations_does_not_load_real_registry(tmp_path, monkeypatch, capsys):
    source, _, _ = public_inputs(tmp_path)
    output = tmp_path / "cli-packet"
    monkeypatch.setattr(
        "sys.argv",
        [
            "enrich_recipes",
            "--recipes",
            str(source),
            "--output",
            str(output),
            "--expected-count",
            "2",
            "--no-curations",
        ],
    )
    main()
    summary = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert summary["assistant_curation_records"] == 0
    assert "Human review:" in capsys.readouterr().out


def test_loader_checks_every_entry_not_only_selected_name(tmp_path):
    _, recipes, entry = public_inputs(tmp_path)
    missing = dict(entry, recipe_id="absent", source_name="absent")
    registry = registry_file(tmp_path, [entry, missing])
    with pytest.raises(ValueError, match="missing"):
        load_preparation_curations(registry, recipes)


def test_registry_snapshot_change_before_validation_creates_no_packet(tmp_path, monkeypatch):
    source, _, entry = public_inputs(tmp_path)
    registry = registry_file(tmp_path, [entry])
    output = tmp_path / "packet"

    def changed_loader(path, recipes, *, expected_sha256=None):
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return load_preparation_curations(path, recipes, expected_sha256=expected_sha256)

    monkeypatch.setattr("pipelines.enrich_recipes.load_preparation_curations", changed_loader)
    with pytest.raises(ValueError, match="changed before validation"):
        export_review(source, output, expected_count=2, curation_path=registry)
    assert not output.exists()


def test_curation_reason_html_is_escaped_not_executed(tmp_path):
    source, _, entry = public_inputs(tmp_path)
    entry["reason"] = "<script>不执行的人工复核说明</script>"
    registry = registry_file(tmp_path, [entry])
    output = tmp_path / "packet"
    export_review(source, output, expected_count=2, curation_path=registry)
    content = (output / "report.html").read_text(encoding="utf-8")
    assert entry["reason"] not in content
    assert "&lt;script&gt;不执行的人工复核说明&lt;/script&gt;" in content
