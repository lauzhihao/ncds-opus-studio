"""Google OAuth 用户 / session 持久化（SQLite，独立于 task/job 状态）。

对齐 vps-insight 的 auth_users / auth_sessions 模型，路径默认 state/auth.db。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuthUserRecord:
    id: int
    google_sub: str
    email: str
    name: str | None
    picture_url: str | None
    created_at: str
    updated_at: str
    last_login_at: str


def now_utc_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  google_sub TEXT NOT NULL UNIQUE,
                  email TEXT NOT NULL UNIQUE,
                  name TEXT,
                  picture_url TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                  session_hash TEXT NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_hash_expires
                  ON auth_sessions(session_hash, expires_at);
                """
            )

    def upsert_auth_user(
        self,
        *,
        google_sub: str,
        email: str,
        name: str | None,
        picture_url: str | None,
    ) -> AuthUserRecord:
        now = now_utc_text()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_users (
                  google_sub, email, name, picture_url, created_at, updated_at, last_login_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(google_sub) DO UPDATE SET
                  email = excluded.email,
                  name = excluded.name,
                  picture_url = excluded.picture_url,
                  updated_at = excluded.updated_at,
                  last_login_at = excluded.last_login_at
                """,
                (google_sub, email, name, picture_url, now, now, now),
            )
            row = conn.execute(
                """
                SELECT id, google_sub, email, name, picture_url,
                       created_at, updated_at, last_login_at
                FROM auth_users
                WHERE google_sub = ?
                """,
                (google_sub,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Auth user could not be read back: {email}")
        return _row_to_auth_user(row)

    def create_auth_session(self, *, user_id: int, session_hash: str, expires_at: str) -> None:
        now = now_utc_text()
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO auth_sessions (user_id, session_hash, expires_at, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, session_hash, expires_at, now, now),
            )

    def get_auth_user_by_session(
        self,
        *,
        session_hash: str,
        now: str,
    ) -> AuthUserRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  u.id, u.google_sub, u.email, u.name, u.picture_url,
                  u.created_at, u.updated_at, u.last_login_at
                FROM auth_sessions s
                JOIN auth_users u ON u.id = s.user_id
                WHERE s.session_hash = ? AND s.expires_at > ?
                """,
                (session_hash, now),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE auth_sessions SET last_seen_at = ? WHERE session_hash = ?",
                    (now, session_hash),
                )
        return _row_to_auth_user(row) if row is not None else None

    def delete_auth_session(self, session_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE session_hash = ?", (session_hash,))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _row_to_auth_user(row: sqlite3.Row) -> AuthUserRecord:
    return AuthUserRecord(
        id=row["id"],
        google_sub=row["google_sub"],
        email=row["email"],
        name=row["name"],
        picture_url=row["picture_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )
