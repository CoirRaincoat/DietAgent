"""SQLite derived metadata cache: stable recipe IDs, read-only selection."""

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from app.schemas.recipe_meta import RecipeMeta


def write_metadata(path: Path, records: Iterable[RecipeMeta]) -> int:
    """Create a new derived cache. Existing files are never rebuilt or overwritten."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    count = 0
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE recipe_metadata (recipe_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            for record in records:
                connection.execute(
                    "INSERT INTO recipe_metadata VALUES (?, ?)",
                    (record.recipe_id, record.model_dump_json()),
                )
                count += 1
    except Exception:
        # Only this newly and exclusively created file belongs to this operation.
        path.unlink()
        raise
    return count


class SQLiteRecipeMetadata:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def select(self, *, required_labels: Sequence[str]) -> list[RecipeMeta]:
        uri = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("SELECT payload FROM recipe_metadata ORDER BY recipe_id")
            records = [RecipeMeta.model_validate_json(row[0]) for row in rows]
        required = set(required_labels)
        return [row for row in records if row.eligible and required.issubset(row.labels)]
