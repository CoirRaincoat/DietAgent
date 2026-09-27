"""Shared, version-neutral writers for evaluation report bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def write_bundle(
    output_dir: Path,
    report: dict[str, Any],
    markdown: str,
    *,
    records: Iterable[dict[str, Any]],
    records_name: str,
) -> dict[str, Path]:
    """Write JSON, Markdown and JSONL artifacts under one run directory."""
    if Path(records_name).name != records_name or not records_name.endswith(".jsonl"):
        raise ValueError("records_name must be a JSONL filename")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output_dir / "report.json",
        "markdown": output_dir / "report.md",
        "records": output_dir / records_name,
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths["markdown"].write_text(markdown, encoding="utf-8")
    with paths["records"].open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return paths
