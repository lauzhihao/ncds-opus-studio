"""OAuth 用户 / session 持久化（SQLite）。

支持多 provider（google / apple）。owner_id = str(user.id)。
旧表仅有 google_sub 时自动迁移。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuthUserRecord:
    id: int
    provider: str
    provider_sub: str
    email: str
    name: str | None
    picture_url: str | None
    created_at: str
    updated_at: str
    last_login_at: str

    @property
    def google_sub(self) -> str | None:
        """兼容旧代码读 google_sub。"""
        return self.provider_sub if self.provider == "google" else None


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
                  provider TEXT NOT NULL,
                  provider_sub TEXT NOT NULL,
                  email TEXT NOT NULL,
                  name TEXT,
                  picture_url TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_login_at TEXT NOT NULL,
                  UNIQUE(provider, provider_sub)
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

                -- OAuth CSRF state（Apple form_post 跨站 POST 可能不带 cookie，必须服务端存）
                CREATE TABLE IF NOT EXISTS oauth_states (
                  state TEXT PRIMARY KEY,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )
            self._migrate_from_google_only(conn)

    def _migrate_from_google_only(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(auth_users)").fetchall()}
        if "google_sub" in cols and "provider" not in cols:
            # SQLite RENAME 会把 auth_sessions 外键改写成 auth_users_legacy，
            # 必须 FK 关闭后连 sessions 一起重建，否则 create_session 会炸。
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(
                """
                ALTER TABLE auth_users RENAME TO auth_users_legacy;
                CREATE TABLE auth_users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  provider TEXT NOT NULL,
                  provider_sub TEXT NOT NULL,
                  email TEXT NOT NULL,
                  name TEXT,
                  picture_url TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_login_at TEXT NOT NULL,
                  UNIQUE(provider, provider_sub)
                );
                INSERT INTO auth_users (
                  id, provider, provider_sub, email, name, picture_url,
                  created_at, updated_at, last_login_at
                )
                SELECT id, 'google', google_sub, email, name, picture_url,
                       created_at, updated_at, last_login_at
                FROM auth_users_legacy;

                CREATE TABLE auth_sessions_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                  session_hash TEXT NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL
                );
                INSERT INTO auth_sessions_new (
                  id, user_id, session_hash, expires_at, created_at, last_seen_at
                )
                SELECT id, user_id, session_hash, expires_at, created_at, last_seen_at
                FROM auth_sessions;
                DROP TABLE auth_sessions;
                ALTER TABLE auth_sessions_new RENAME TO auth_sessions;
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_hash_expires
                  ON auth_sessions(session_hash, expires_at);

                DROP TABLE auth_users_legacy;
                """
            )
            conn.execute("PRAGMA foreign_keys=ON")
            return
        # 半迁移修复：sessions 仍引用 auth_users_legacy
        self._repair_sessions_fk(conn)

    def _repair_sessions_fk(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='auth_sessions'"
        ).fetchone()
        sql = (row[0] if row else "") or ""
        if "auth_users_legacy" not in sql:
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            CREATE TABLE auth_sessions_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
              session_hash TEXT NOT NULL UNIQUE,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );
            INSERT INTO auth_sessions_new (
              id, user_id, session_hash, expires_at, created_at, last_seen_at
            )
            SELECT id, user_id, session_hash, expires_at, created_at, last_seen_at
            FROM auth_sessions;
            DROP TABLE auth_sessions;
            ALTER TABLE auth_sessions_new RENAME TO auth_sessions;
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_hash_expires
              ON auth_sessions(session_hash, expires_at);
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")

    def upsert_auth_user(
        self,
        *,
        provider: str,
        provider_sub: str,
        email: str,
        name: str | None,
        picture_url: str | None,
    ) -> AuthUserRecord:
        now = now_utc_text()
        provider = provider.strip().lower()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_users (
                  provider, provider_sub, email, name, picture_url,
                  created_at, updated_at, last_login_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_sub) DO UPDATE SET
                  email = excluded.email,
                  name = COALESCE(excluded.name, auth_users.name),
                  picture_url = COALESCE(excluded.picture_url, auth_users.picture_url),
                  updated_at = excluded.updated_at,
                  last_login_at = excluded.last_login_at
                """,
                (provider, provider_sub, email, name, picture_url, now, now, now),
            )
            row = conn.execute(
                """
                SELECT id, provider, provider_sub, email, name, picture_url,
                       created_at, updated_at, last_login_at
                FROM auth_users
                WHERE provider = ? AND provider_sub = ?
                """,
                (provider, provider_sub),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Auth user could not be read back: {provider}:{provider_sub}")
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
                  u.id, u.provider, u.provider_sub, u.email, u.name, u.picture_url,
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

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM auth_users").fetchone()
        return int(row["c"] if row is not None else 0)

    def save_oauth_state(self, state: str, *, expires_at: str) -> None:
        now = now_utc_text()
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT OR REPLACE INTO oauth_states (state, expires_at, created_at)
                VALUES (?, ?, ?)
                """,
                (state, expires_at, now),
            )

    def consume_oauth_state(self, state: str) -> bool:
        """一次性消费 state；存在且未过期返回 True。"""
        if not state:
            return False
        now = now_utc_text()
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT state FROM oauth_states WHERE state = ? AND expires_at > ?",
                (state, now),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            return True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _row_to_auth_user(row: sqlite3.Row) -> AuthUserRecord:
    keys = row.keys()
    if "provider" in keys:
        return AuthUserRecord(
            id=row["id"],
            provider=row["provider"],
            provider_sub=row["provider_sub"],
            email=row["email"],
            name=row["name"],
            picture_url=row["picture_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )
    # 兼容未迁移行
    return AuthUserRecord(
        id=row["id"],
        provider="google",
        provider_sub=row["google_sub"],
        email=row["email"],
        name=row["name"],
        picture_url=row["picture_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )
