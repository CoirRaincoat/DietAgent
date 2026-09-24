"""Build disposable normalized data and index: python -m pipelines.normalize."""

import argparse
import json
from pathlib import Path

from app.infrastructure.data import PROJECT_ROOT, build_lexical_index, load_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts")
    args = parser.parse_args()
    output = args.output.resolve()
    # Never let a mistaken output option overwrite original competition data.
    if output == PROJECT_ROOT / "dataset" or (PROJECT_ROOT / "dataset") in output.parents:
        parser.error("output must not be inside the original dataset directory")
    catalog = load_catalog()
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "recipes.normalized.json": [recipe.model_dump() for recipe in catalog.recipes.values()],
        "profiles.normalized.json": [profile.model_dump() for profile in catalog.profiles.values()],
        "recipes.lexical_index.json": build_lexical_index(catalog.recipes.values()),
        "data_quality.json": catalog.quality_report,
    }
    for name, document in documents.items():
        (output / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Normalized {len(catalog.recipes)} recipes and {len(catalog.profiles)} profiles into {output}")


if __name__ == "__main__":
    main()
