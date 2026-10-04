"""Build a new, traceable SQLite cache from the original GB18030 recipe CSV.

Run as `python -m scripts.etl_recipes --output artifacts/recipe_metadata.db`.
No models, embeddings, downloads, vector collections or private inputs are used.
"""

import argparse
import csv
from pathlib import Path

from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, normalize_recipes
from app.infrastructure.recipe_metadata import write_metadata
from app.normalization.normalizer import TermNormalizer
from app.schemas.recipe_meta import RecipeMeta


def build_metadata(source_csv: Path, output: Path) -> int:
    if Path(source_csv).resolve() == Path(output).resolve():
        raise ValueError("The source CSV cannot be the derived output")
    with Path(source_csv).open(encoding="gb18030", newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    normalizer = TermNormalizer()
    return write_metadata(
        output, (RecipeMeta.from_recipe(recipe, normalizer) for recipe in recipes.values()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, default=PROJECT_ROOT / RECIPE_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = build_metadata(args.source_csv, args.output)
    print(f"Created derived metadata for {count} source recipes; safety review: not_reviewed")


if __name__ == "__main__":
    main()
