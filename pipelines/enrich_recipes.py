"""Audit the whole recipe CSV and export labelled, non-destructive review drafts.

Usage: python -m pipelines.enrich_recipes --test-results runtime/suite.xml
No API/model calls, original user profiles, source edits or serving activation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.preparation_enrichment import (
    ENRICHMENT_VERSION,
    audit_preparation,
    supplemented_steps,
    tag_enrichment,
)
from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, normalize_recipes
from app.infrastructure.preparation_curations import (
    CURATION_VERSION,
    load_preparation_curations,
)

_LABELS = {
    "keep_source": "未触发有限规则（原文保留，非完整性认证）",
    "draft_supplement": "有助手补全草稿，尚未人工审核/试做",
    "review_required": "原问题/未决事项待核查（可能含部分草稿）",
}


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8"
    )


def _test_results(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        root = ET.parse(path).getroot()
        for item in root.iter("testcase"):
            status = (
                "failed"
                if item.find("failure") is not None
                else (
                    "error"
                    if item.find("error") is not None
                    else "skipped" if item.find("skipped") is not None else "passed"
                )
            )
            rows.append(
                {
                    "suite_file": path.name,
                    "class": item.get("classname"),
                    "case": item.get("name"),
                    "status": status,
                    "time_seconds_NOT_model_latency": item.get("time"),
                    "failure": "".join(item.itertext()) if status != "passed" else None,
                }
            )
    return rows


def export_review(
    source_path: Path,
    output_dir: Path,
    *,
    test_results: list[Path] | None = None,
    expected_count: int | None = 2000,
    curation_path: Path | None = None,
) -> dict[str, Any]:
    """Create a new packet only, with every input/result and a SHA manifest."""
    source_bytes = source_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    with StringIO(source_bytes.decode("gb18030"), newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    if expected_count is not None and len(recipes) != expected_count:
        raise ValueError(f"Expected {expected_count} recipes, got {len(recipes)}")
    # Every registry entry is source-bound before any output is created. Custom
    # CSV callers opt in explicitly; selecting a stale registry never falls back.
    curation_bytes = curation_path.read_bytes() if curation_path else None
    curation_sha = (
        hashlib.sha256(curation_bytes).hexdigest() if curation_bytes is not None else None
    )
    curations = (
        load_preparation_curations(curation_path, recipes, expected_sha256=curation_sha)
        if curation_path
        else {}
    )
    # No overwriting an earlier review, including empty directories.
    output_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    selected_curations: list[dict[str, Any]] = []
    for recipe in recipes.values():
        original = recipe.model_dump(mode="json")
        before = audit_preparation(recipe)
        curation = curations.get(recipe.recipe_id)
        review = audit_preparation(recipe, curation=curation)
        view = supplemented_steps(recipe, review, curation=curation)
        tags = tag_enrichment(recipe, review, curation=curation)
        record: dict[str, Any] = {
            "source": original,
            "preparation_before_curation": before.to_dict(),
            "preparation_review": review.to_dict(),
            "supplemented_steps_REVIEW_ONLY": view,
            "tag_review": tags,
        }
        records.append(record)
        if curation is not None:
            selected_curations.append(
                {
                    "recipe_id": recipe.recipe_id,
                    "name": recipe.name,
                    "assistant_curation": curation.model_dump(mode="json"),
                    "before_status": before.status,
                    "after_status": review.status,
                    "original_findings": before.findings,
                    "findings_after_curation": review.findings,
                    "source_unchanged": True,
                    "human_review": "not_reviewed",
                    "production_applied": False,
                }
            )
        source_unchanged = original == recipe.model_dump(mode="json")
        dependencies = all(
            set(addition.ingredient_names) <= {i.name for i in recipe.ingredients}
            for addition in review.additions
        )
        preserved = recipe.steps in view
        if not (source_unchanged and dependencies and preserved):
            raise RuntimeError("Source preservation/declaration check failed")
        results.append(
            {
                "recipe_id": recipe.recipe_id,
                "status": review.status,
                "findings": review.findings,
                "before_curation_status": before.status,
                "before_curation_findings": before.findings,
                "assistant_curation_selected": curation is not None,
                "curation_disposition": curation.disposition if curation else None,
                "source_unchanged": source_unchanged,
                "original_steps_preserved": preserved,
                "addition_dependencies_declared": dependencies,
                "versioned_sidecar_bound": True,
                "production_applied": False,
                "human_quality_score": None,
                "model_quality_score": None,
                "nutrition_or_safety_certified": False,
            }
        )
        if review.findings or tags["tags_to_add_to_review_view"] or curation is not None:
            notes.append(
                {
                    "recipe_id": recipe.recipe_id,
                    "name": recipe.name,
                    "preparation_approved": None,
                    "tags_approved": None,
                    "comments": "",
                    "reviewer": "",
                }
            )
    counts = Counter(record["preparation_review"]["status"] for record in records)
    tests = _test_results(test_results or [])
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    revision = git.stdout.strip() if git.returncode == 0 else "unavailable"
    summary = {
        "policy_version": ENRICHMENT_VERSION,
        "recipe_count": len(records),
        "preparation_counts": dict(counts),
        "preparation_counts_before_curation": dict(
            Counter(record["preparation_before_curation"]["status"] for record in records)
        ),
        "raw_finite_findings_records": sum(
            bool(record["preparation_before_curation"]["findings"]) for record in records
        ),
        "assistant_curation_version": CURATION_VERSION if curation_path else None,
        "assistant_curation_records": len(selected_curations),
        "assistant_curation_disposition_counts": dict(
            Counter(row["assistant_curation"]["disposition"] for row in selected_curations)
        ),
        "source_conflict_records": sum(
            row["assistant_curation"]["disposition"] == "source_conflict"
            for row in selected_curations
        ),
        "retained_component_records": sum(
            row["assistant_curation"]["disposition"] == "retain_component"
            for row in selected_curations
        ),
        "unresolved_records_with_partial_drafts": sum(
            record["preparation_review"]["status"] == "review_required"
            and bool(record["preparation_review"]["additions"])
            for record in records
        ),
        "assistant_curation_sha256": curation_sha,
        "assistant_curation_registry_unchanged": (
            curation_path.read_bytes() == curation_bytes if curation_path else None
        ),
        "original_findings_retained": all(
            record["preparation_before_curation"]["findings"]
            == record["preparation_review"]["findings"]
            for record in records
        ),
        "source_missing_labels": sum(
            not record["source"]["raw_label"].strip() for record in records
        ),
        "tag_suggestion_records": sum(
            bool(record["tag_review"]["tags_to_add_to_review_view"]) for record in records
        ),
        "source_sha256": source_sha,
        "code_revision": revision,
        "test_counts": dict(Counter(row["status"] for row in tests)),
        "test_counts_per_run": {
            path.name: dict(
                Counter(row["status"] for row in tests if row["suite_file"] == path.name)
            )
            for path in (test_results or [])
        },
        "test_counts_include_repeated_tests_across_runs": len(test_results or []) > 1,
        "human_labels_filled": 0,
        "provider_calls": 0,
        "original_profiles_loaded": False,
        "original_dialogues_loaded": False,
        "production_applied": False,
        "source_unchanged": source_path.read_bytes() == source_bytes,
        "boundary": "有限整库扫描与逐条助手补全/标签草稿；保留原始问题、配方冲突和组件资格。数量变化不是完整性、质量或安全认证。未人工审核或上线。",
    }
    _json(output_dir / "report.json", summary)
    _jsonl(output_dir / "recipes_enriched_review.jsonl", records)
    _jsonl(output_dir / "case_results.jsonl", results)
    _jsonl(output_dir / "assistant_curations.jsonl", selected_curations)
    _jsonl(
        output_dir / "preparation_drafts.jsonl",
        [record for record in records if record["preparation_review"]["additions"]],
    )
    _jsonl(
        output_dir / "manual_queue.jsonl",
        [
            record
            for record in records
            if record["preparation_review"]["status"] == "review_required"
        ],
    )
    _jsonl(output_dir / "review_notes_blank.jsonl", notes)
    _json(output_dir / "test_results.json", tests)
    evidence = output_dir / "evidence"
    evidence.mkdir()
    for path in [
        PROJECT_ROOT / "app/domain/preparation_enrichment.py",
        PROJECT_ROOT / "app/infrastructure/preparation_curations.py",
        PROJECT_ROOT / "pipelines/enrich_recipes.py",
        PROJECT_ROOT / "tests/test_preparation_enrichment.py",
        PROJECT_ROOT / "tests/test_preparation_curation.py",
        PROJECT_ROOT / "tests/test_recipe_curation_registry.py",
        PROJECT_ROOT / "tests/test_preparation_curation_export.py",
        PROJECT_ROOT / "tests/test_recipe_enrichment_export.py",
        *(test_results or []),
    ]:
        shutil.copy2(path, evidence / path.name)
    if curation_path is not None and curation_bytes is not None:
        # This is the exact snapshot validated above, not a later filesystem
        # copy that could disagree with the registry SHA in the report.
        (evidence / curation_path.name).write_bytes(curation_bytes)
    rows = [
        "# 整库菜谱：有限扫描与逐条补全/标签清洗复核",
        "",
        "原版不改写；所有补全为助手草稿，人工审核为空。没有新模型评分、临床或时延结论。",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 优先查看",
        "",
        "- preparation_drafts.jsonl：做法补全稿。",
        "- assistant_curations.jsonl：逐条助手决定、原问题和未决事项；不代表人工通过。",
        "- manual_queue.jsonl：不能可靠自动补全的条目。",
        "- recipes_enriched_review.jsonl：全部原始记录、补全、标签建议。",
        "- case_results.jsonl：每条运行检查；不是回复质量分数。",
        "- review_notes_blank.jsonl：人工意见，未代填。",
        "",
        "## 已生成做法草稿",
        "",
    ]
    for record in records:
        if record["preparation_review"]["additions"]:
            rows += [
                "### " + record["source"]["name"],
                "",
                "原料：" + record["source"]["raw_ingredients"],
                "",
                "原始步骤：",
                "",
                record["source"]["steps"],
                "",
                "补充内容：",
                "",
            ]
            rows += [
                addition["placement"] + "：" + addition["text"] + "\n"
                for addition in record["preparation_review"]["additions"]
            ]
    rows += ["", "## 逐条助手核查与未决事项", ""]
    record_index = {record["source"]["recipe_id"]: record for record in records}
    for row in selected_curations:
        curation_data = row["assistant_curation"]
        selected_record = record_index[row["recipe_id"]]
        rows += [
            "### " + row["name"],
            "",
            "源记录 ID：" + row["recipe_id"],
            "",
            "原料（不改）：" + selected_record["source"]["raw_ingredients"],
            "",
            "原始步骤（不改）：",
            "",
            selected_record["source"]["steps"],
            "",
            "决定：" + curation_data["disposition"] + "（助手建议，不是人工确认）",
            "",
            "依据：" + curation_data["reason"],
            "",
            "原始有限核查问题：" + json.dumps(row["original_findings"], ensure_ascii=False),
            "",
            "剩余问题：" + json.dumps(curation_data["remaining_questions"], ensure_ascii=False),
            "",
            "描述性标签建议：" + json.dumps(curation_data["tag_suggestions"], ensure_ascii=False),
            "",
        ]
        if selected_record["preparation_review"]["additions"]:
            rows += [
                "补全版（仅供复核，原问题与未决事项仍保留）：",
                "",
                selected_record["supplemented_steps_REVIEW_ONLY"],
                "",
            ]
    (output_dir / "report.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    sections = []
    ordered = sorted(
        records,
        key=lambda r: (
            {"draft_supplement": 0, "review_required": 1, "keep_source": 2}[
                r["preparation_review"]["status"]
            ],
            r["source"]["source_row"],
        ),
    )
    for record in ordered:
        source, review_data, tags = (
            record["source"],
            record["preparation_review"],
            record["tag_review"],
        )

        def esc(value: object) -> str:
            return html.escape(str(value))

        sections.append(
            '<details class="recipe"><summary>'
            + esc(source["name"])
            + " · "
            + esc(_LABELS[review_data["status"]])
            + "</summary><p>"
            + esc(source["recipe_id"])
            + " · CSV行 "
            + str(source["source_row"])
            + "</p><h3>原料（不改）</h3><pre>"
            + esc(source["raw_ingredients"])
            + "</pre><h3>原始做法</h3><pre>"
            + esc(source["steps"])
            + "</pre><h3>做法核查</h3><pre>"
            + esc(json.dumps(review_data, ensure_ascii=False, indent=2))
            + "</pre>"
            + (
                "<h3>逐条助手核查：依据、决定与未决事项</h3><pre>"
                + esc(json.dumps(review_data["assistant_curation"], ensure_ascii=False, indent=2))
                + "</pre>"
                if review_data.get("assistant_curation") is not None
                else ""
            )
            + (
                "<h3>补全版（仅复核）</h3><pre>"
                + esc(record["supplemented_steps_REVIEW_ONLY"])
                + "</pre>"
                if review_data["additions"]
                else ""
            )
            + "<h3>原标签与补齐/清洗建议</h3><pre>"
            + esc(json.dumps(tags, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>菜谱整库复核</title>'
    document += "<style>body{max-width:1100px;margin:30px auto;padding:0 20px;font-family:system-ui;line-height:1.6}"
    document += "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f6;padding:14px}"
    document += "details{border:1px solid #ccc;border-radius:6px;margin:12px 0;padding:12px}"
    document += "summary{cursor:pointer;font-weight:bold}input{padding:8px;width:90%}</style>"
    document += (
        "<h1>整库做法补全与标签清洗：逐条复核</h1><p>先展示草稿、再展示待核查，最后原版保留。"
    )
    document += "“未触发规则”不是完整性认证。所有草稿尚未人工审核/试做，未自动上线。</p><pre>"
    document += html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + "</pre>"
    document += '<p><a href="report.md">Markdown</a> · <a href="recipes_enriched_review.jsonl">整库输入/结果</a>'
    document += ' · <a href="preparation_drafts.jsonl">做法草稿</a> · <a href="manual_queue.jsonl">待核查</a>'
    document += ' · <a href="assistant_curations.jsonl">逐条助手核查</a>'
    document += ' · <a href="test_results.json">测试结果</a></p><label>按菜名/ID搜索：<input id="query"></label>'
    document += "<main>" + "\n".join(sections) + '</main><script>document.getElementById("query")'
    document += '.addEventListener("input",function(){const q=this.value.trim().toLowerCase();'
    document += 'document.querySelectorAll("details.recipe").forEach(d=>{'
    document += "d.hidden=!d.textContent.toLowerCase().includes(q)})});</script></html>"
    (output_dir / "report.html").write_text(document, encoding="utf-8")
    manifest = {
        path.relative_to(output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    _json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes", type=Path, default=PROJECT_ROOT / RECIPE_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-count", type=int, default=2000)
    parser.add_argument("--test-results", type=Path, nargs="*", default=[])
    curated = parser.add_mutually_exclusive_group()
    curated.add_argument(
        "--curations",
        type=Path,
        default=PROJECT_ROOT / "configs/recipe_preparation_curations.json",
        help="Exact-source assistant registry; all entries must bind to selected CSV.",
    )
    curated.add_argument(
        "--no-curations", action="store_true", help="Export the unchanged finite-rule baseline."
    )
    args = parser.parse_args()
    output = args.output or (
        PROJECT_ROOT
        / "runtime/recipe_enrichment"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    summary = export_review(
        args.recipes,
        output,
        test_results=args.test_results,
        expected_count=args.expected_count,
        curation_path=None if args.no_curations else args.curations,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Human review: " + str(output / "report.html"))


if __name__ == "__main__":
    main()
