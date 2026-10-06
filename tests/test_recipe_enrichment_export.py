"""Non-destructive full-input export and honest review provenance contracts."""

import csv
import hashlib
import json

import pytest

from pipelines.enrich_recipes import export_review


def source_file(tmp_path):
    source = tmp_path / "recipes.csv"
    with source.open("w", encoding="gb18030", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["名称", "食材清单", "烹饪步骤", "label"])
        writer.writeheader()
        writer.writerows(
            [
                {"名称": "蒸白菜", "食材清单": "白菜；盐", "烹饪步骤": "白菜蒸熟。", "label": ""},
                {
                    "名称": "普通面包",
                    "食材清单": "面粉；酵母；水",
                    "烹饪步骤": "面粉、酵母、水揉成面团。",
                    "label": "降压",
                },
                {
                    "名称": "待核查面包",
                    "食材清单": "面粉；酵母；水",
                    "烹饪步骤": "第1步：1",
                    "label": "",
                },
                {
                    "名称": "<script>不执行</script>",
                    "食材清单": "白菜；盐",
                    "烹饪步骤": "白菜蒸熟。",
                    "label": "",
                },
            ]
        )
    return source


def test_export_all_inputs_blank_notes_and_manifest_without_source_mutation(tmp_path):
    source = source_file(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=4)
    assert summary["recipe_count"] == 4
    assert summary["preparation_counts"] == {
        "keep_source": 2,
        "draft_supplement": 1,
        "review_required": 1,
    }
    assert source.read_bytes() == original
    assert summary["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert summary["human_labels_filled"] == 0 and summary["provider_calls"] == 0
    assert not summary["production_applied"]
    records = [
        json.loads(line)
        for line in (output / "recipes_enriched_review.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 4
    for record in records:
        assert record["source"]["steps"] in record["supplemented_steps_REVIEW_ONLY"]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert all(
        hashlib.sha256((output / path).read_bytes()).hexdigest() == sha
        for path, sha in manifest.items()
    )
    page = (output / "report.html").read_text(encoding="utf-8")
    assert page.count('<details class="recipe">') == 4
    assert "<script>不执行</script>" not in page
    assert "&lt;script&gt;不执行&lt;/script&gt;" in page
    notes = [
        json.loads(line)
        for line in (output / "review_notes_blank.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert notes and all(
        note["preparation_approved"] is None and note["tags_approved"] is None for note in notes
    )


def test_expected_size_failure_does_not_create_or_overwrite_packet(tmp_path):
    source = source_file(tmp_path)
    output = tmp_path / "packet"
    with pytest.raises(ValueError, match="Expected 2000"):
        export_review(source, output)
    assert not output.exists()
    export_review(source, output, expected_count=4)
    report = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_review(source, output, expected_count=4)
    assert (output / "report.json").read_bytes() == report


def test_actual_test_failure_is_exported_not_converted_to_success(tmp_path):
    source = source_file(tmp_path)
    junit = tmp_path / "suite.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase name="counterexample"><failure message="failed">original failure</failure></testcase><testcase name="good"/></testsuite></testsuites>',
        encoding="utf-8",
    )
    output = tmp_path / "packet"
    summary = export_review(source, output, expected_count=4, test_results=[junit])
    assert summary["test_counts"] == {"failed": 1, "passed": 1}
    results = json.loads((output / "test_results.json").read_text(encoding="utf-8"))
    assert results[0]["status"] == "failed" and "original failure" in results[0]["failure"]
