"""The people DataLab knows (table `users`, migration 0016)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from datalab.repos.github import Account

LOCAL_ID = 1


@dataclass(frozen=True)
class User:
    id: int
    login: str
    name: str = ""
    github_id: int | None = None
    admin: bool = False

    @property
    def display_name(self) -> str:
        return self.name or self.login


# A person's computer: one user, who may do everything.
LOCAL = User(LOCAL_ID, "local", admin=True)


class Users:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def signed_in_with_github(self, account: Account, *, admin: bool) -> User:
        """The person behind a GitHub sign-in, added the first time. Their
        login, name and whether they're an administrator are kept up to
        date; a disabled person stays disabled (see `get`)."""
        now = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO users (github_id, login, name, is_admin, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (github_id) DO UPDATE SET login = excluded.login, "
                "name = excluded.name, is_admin = excluded.is_admin, "
                "last_seen_at = excluded.last_seen_at",
                (account.id, account.login, account.name, int(admin), now, now),
            )
            row = self._db.execute(
                "SELECT * FROM users WHERE github_id = ?", (account.id,)
            ).fetchone()
        return _user(row)

    def get(self, user_id: int) -> User | None:
        """Someone who may use DataLab now: None if unknown or disabled."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,)
            ).fetchone()
        return _user(row) if row else None

    def disabled(self, user_id: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["disabled"])


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        login=row["login"],
        name=row["name"],
        github_id=row["github_id"],
        admin=bool(row["is_admin"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
