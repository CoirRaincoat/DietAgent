"""SQLite snapshots with optimistic versions and idempotent completed requests.

Connections use short transactions, never held during model calls. This MVP runs
one API worker; the agent also serializes each session with an asyncio lock.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.domain.models import ChatResult, SessionState


class SessionConflict(Exception):
    pass


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.executescript(
                """
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
                """
            )

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
        return SessionState.model_validate_json(row[1])

    def save(self, state: SessionState, expected_revision: int | None) -> None:
        with self._connect() as connection:
            if expected_revision is None:
                try:
                    connection.execute(
                        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                        (state.session_id, state.user_id, state.revision, state.model_dump_json()),
                    )
                except sqlite3.IntegrityError as error:
                    raise SessionConflict("会话已存在，请重试。") from error
            else:
                cursor = connection.execute(
                    "UPDATE sessions SET revision = ?, snapshot = ? "
                    "WHERE id = ? AND user_id = ? AND revision = ?",
                    (
                        state.revision, state.model_dump_json(), state.session_id,
                        state.user_id, expected_revision,
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
                "WHERE session_id = ? AND request_id = ?", (session_id, request_id),
            ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise SessionConflict("同一 request_id 不能用于不同请求。")
        if row[1] != revision:
            raise SessionConflict("该请求的结果已经过期，请使用新的 request_id。")
        return ChatResult.model_validate_json(row[2])

    def complete(
        self, result: ChatResult, expected_revision: int,
        request_id: str | None, request_hash: str,
    ) -> None:
        state = result.conversation_state
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET snapshot = ? WHERE id = ? AND revision = ?",
                (state.model_dump_json(), state.session_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise SessionConflict("生成结果所依据的状态已变化。")
            if request_id:
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                    (state.session_id, request_id, request_hash, state.revision,
                     result.model_dump_json()),
                )
