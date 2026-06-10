import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from backend.auth.models import UserRecord
from backend.auth.password import verify_password_hash


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row: tuple) -> UserRecord:
    # columns: id, username, password_hash, modules, is_admin, is_active, created_at, updated_at
    _, username, password_hash, modules_json, is_admin, is_active, created_at, updated_at = row
    return UserRecord(
        username=username,
        password_hash=password_hash,
        modules=json.loads(modules_json),
        is_admin=bool(is_admin),
        is_active=bool(is_active),
        created_at=created_at,
        updated_at=updated_at,
    )


class UserRepository(Protocol):
    def create_user(
        self,
        username: str,
        password_hash: str,
        modules: list[str],
        is_admin: bool,
        is_active: bool,
    ) -> UserRecord: ...

    def get_user(self, username: str) -> UserRecord | None: ...

    def list_users(self) -> list[UserRecord]: ...

    def update_user(
        self,
        username: str,
        *,
        password_hash: str | None = None,
        modules: list[str] | None = None,
        is_admin: bool | None = None,
        is_active: bool | None = None,
    ) -> UserRecord: ...

    def delete_user(self, username: str) -> bool: ...

    def verify_password(self, username: str, plaintext: str) -> bool: ...

    def count_active_admins(self) -> int: ...


class SQLiteUserRepository:
    """SQLite-backed UserRepository. Thread-safe via a per-instance lock."""

    def __init__(self, db_path: str = "data/users.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    username     TEXT    NOT NULL UNIQUE,
                    password_hash TEXT   NOT NULL,
                    modules      TEXT    NOT NULL DEFAULT '[]',
                    is_admin     INTEGER NOT NULL DEFAULT 0,
                    is_active    INTEGER NOT NULL DEFAULT 1,
                    created_at   TEXT    NOT NULL,
                    updated_at   TEXT    NOT NULL
                )
            """)
            self._conn.commit()

    def create_user(
        self,
        username: str,
        password_hash: str,
        modules: list[str],
        is_admin: bool,
        is_active: bool,
    ) -> UserRecord:
        now = _now_iso()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO users
                        (username, password_hash, modules, is_admin, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (username, password_hash, json.dumps(modules), int(is_admin), int(is_active), now, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Username already exists: {username!r}")
        return UserRecord(
            username=username,
            password_hash=password_hash,
            modules=modules,
            is_admin=is_admin,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )

    def get_user(self, username: str) -> UserRecord | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, username, password_hash, modules, is_admin, is_active, created_at, updated_at"
                " FROM users WHERE username = ?",
                (username,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row is not None else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, username, password_hash, modules, is_admin, is_active, created_at, updated_at"
                " FROM users ORDER BY username"
            )
            rows = cur.fetchall()
        return [_row_to_record(row) for row in rows]

    def update_user(
        self,
        username: str,
        *,
        password_hash: str | None = None,
        modules: list[str] | None = None,
        is_admin: bool | None = None,
        is_active: bool | None = None,
    ) -> UserRecord:
        fields: list[str] = []
        values: list[object] = []
        if password_hash is not None:
            fields.append("password_hash = ?")
            values.append(password_hash)
        if modules is not None:
            fields.append("modules = ?")
            values.append(json.dumps(modules))
        if is_admin is not None:
            fields.append("is_admin = ?")
            values.append(int(is_admin))
        if is_active is not None:
            fields.append("is_active = ?")
            values.append(int(is_active))
        if not fields:
            raise ValueError("update_user called with no fields to update")
        now = _now_iso()
        fields.append("updated_at = ?")
        values.append(now)
        values.append(username)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE username = ?",
                values,
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise KeyError(username)
        record = self.get_user(username)
        assert record is not None  # rowcount > 0 guarantees it exists
        return record

    def delete_user(self, username: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM users WHERE username = ?", (username,))
            self._conn.commit()
        return cur.rowcount > 0

    def verify_password(self, username: str, plaintext: str) -> bool:
        """Pure hash check — does not enforce is_active (that is an A3 concern)."""
        user = self.get_user(username)
        if user is None:
            return False
        return verify_password_hash(plaintext, user.password_hash)

    def count_active_admins(self) -> int:
        """COUNT rows where is_admin=1 AND is_active=1. Used for last-admin lockout protection."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1"
            )
            row = cur.fetchone()
        return row[0] if row else 0
