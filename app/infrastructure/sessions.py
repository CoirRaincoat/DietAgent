"""SQLite snapshots with optimistic versions and idempotent completed requests.

Connections use short transactions, never held during model calls. This MVP runs
one API worker; the agent also serializes each session with an asyncio lock.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.models import ChatResult, RecommendedMeal, SessionState


@dataclass(frozen=True)
class RecommendationHistory:
    revision: int
    recommendations: list[RecommendedMeal]


class SessionConflict(Exception):
    pass


def _state_snapshot(state: SessionState) -> str:
    """Persist server-only proposals omitted from the public state serializer."""
    snapshot = state.model_dump(mode="json")
    snapshot["generated_recipes"] = {key: recipe.model_dump(mode="json") for key, recipe in state.generated_recipes.items()}
    return json.dumps(snapshot, ensure_ascii=False)


def _upgrade_menu_structure(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Preserve stored counts when legacy snapshots lack their provenance flag."""
    if "menu_structure_explicit" not in snapshot:
        constraints = snapshot.get("meal_constraints") or snapshot.get("constraints") or {}
        if "dish_count" in constraints or "soup_count" in constraints:
            snapshot["menu_structure_explicit"] = True
    return snapshot


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    revision INTEGER NOT NULL, snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, revision INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS recommended_meals (
                    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL, session_id TEXT NOT NULL,
                    meal_sequence INTEGER NOT NULL, snapshot TEXT NOT NULL,
                    UNIQUE(session_id, meal_sequence)
                );
                CREATE INDEX IF NOT EXISTS recommended_meals_user_order
                    ON recommended_meals(user_id, ordinal DESC);
                CREATE TABLE IF NOT EXISTS recommendation_versions (
                    user_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL
                );
                """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, session_id: str, user_id: int) -> SessionState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, snapshot FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        if row[0] != user_id:
            raise SessionConflict("会话与用户不匹配。")
        return SessionState.model_validate(_upgrade_menu_structure(json.loads(row[1])))

    def save(self, state: SessionState, expected_revision: int | None) -> None:
        with self._connect() as connection:
            if expected_revision is None:
                try:
                    connection.execute(
                        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                        (state.session_id, state.user_id, state.revision, _state_snapshot(state)),
                    )
                except sqlite3.IntegrityError as error:
                    raise SessionConflict("会话已存在，请重试。") from error
            else:
                cursor = connection.execute(
                    "UPDATE sessions SET revision = ?, snapshot = ? "
                    "WHERE id = ? AND user_id = ? AND revision = ?",
                    (
                        state.revision,
                        _state_snapshot(state),
                        state.session_id,
                        state.user_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SessionConflict("会话版本冲突，请重试。")

    def replay(
        self, session_id: str, request_id: str, request_hash: str, revision: int
    ) -> ChatResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_hash, revision, result FROM requests "
                "WHERE session_id = ? AND request_id = ?",
                (session_id, request_id),
            ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise SessionConflict("同一 request_id 不能用于不同请求。")
        if row[1] != revision:
            raise SessionConflict("该请求的结果已经过期，请使用新的 request_id。")
        result = json.loads(row[2])
        result["conversation_state"] = _upgrade_menu_structure(result["conversation_state"])
        return ChatResult.model_validate(result)

    def complete(
        self,
        result: ChatResult,
        expected_revision: int,
        request_id: str | None,
        request_hash: str,
        *,
        recommendation: RecommendedMeal | None = None,
        expected_history_revision: int | None = None,
    ) -> None:
        state = result.conversation_state
        if recommendation is not None and (
            result.status != "ok"
            or not state.menu_valid
            or not recommendation.recipe_ids
            or recommendation.recipe_ids != state.menu_ids
            or recommendation.recipe_ids != [r.recipe_id for r in result.menu]
            or recommendation.recipe_names != [r.name for r in result.menu]
            or recommendation.meal_type != state.constraints.meal_type
        ):
            raise ValueError("推荐历史只能记录本次完整验证并返回的菜单。")
        with self._connect() as connection:
            # Short write transaction, after generation. Check the user's
            # history and publish result/receipt atomically, even across agents.
            connection.execute("BEGIN IMMEDIATE")
            if recommendation is not None and expected_history_revision is not None:
                row = connection.execute(
                    "SELECT revision FROM recommendation_versions WHERE user_id = ?",
                    (state.user_id,),
                ).fetchone()
                revision = row[0] if row else 0
                if revision != expected_history_revision:
                    raise SessionConflict("用户推荐历史已变化，请重试；未提交旧历史上的轮换结果。")
            cursor = connection.execute(
                "UPDATE sessions SET snapshot = ? WHERE id = ? AND user_id = ? AND revision = ?",
                (_state_snapshot(state), state.session_id, state.user_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise SessionConflict("生成结果所依据的状态已变化。")
            if request_id:
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                    (
                        state.session_id,
                        request_id,
                        request_hash,
                        state.revision,
                        result.model_dump_json(),
                    ),
                )
            if recommendation is not None:
                cursor = connection.execute(
                    "INSERT INTO recommended_meals(user_id, session_id, meal_sequence, snapshot) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(session_id, meal_sequence) DO UPDATE SET "
                    "snapshot = excluded.snapshot WHERE recommended_meals.user_id = excluded.user_id "
                    "AND recommended_meals.snapshot != excluded.snapshot",
                    (
                        state.user_id,
                        state.session_id,
                        state.meal_sequence,
                        recommendation.model_dump_json(),
                    ),
                )
                if cursor.rowcount:
                    connection.execute(
                        "INSERT INTO recommendation_versions VALUES (?, 1) "
                        "ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1",
                        (state.user_id,),
                    )

    def recommendation_history(
        self,
        user_id: int,
        *,
        exclude: tuple[str, int] | None = None,
    ) -> RecommendationHistory:
        """Read one user's last eight completed meals and token in one snapshot.

        Updates within a meal keep its chronology and do not create a new meal.
        Only the user's own current meal may be excluded. No source bodies,
        profile facts, consumption assumptions or historical backfill are read.
        """
        with self._connect() as connection:
            connection.execute("BEGIN")
            version = connection.execute(
                "SELECT revision FROM recommendation_versions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            excluded_id, excluded_sequence = exclude or ("", -1)
            rows = connection.execute(
                "SELECT snapshot FROM recommended_meals WHERE user_id = ? "
                "AND NOT (session_id = ? AND meal_sequence = ?) ORDER BY ordinal DESC LIMIT 8",
                (user_id, excluded_id, excluded_sequence),
            ).fetchall()
        return RecommendationHistory(
            revision=version[0] if version else 0,
            recommendations=[RecommendedMeal.model_validate_json(row[0]) for row in reversed(rows)],
        )
