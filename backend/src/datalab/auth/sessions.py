"""Browser sign-ins on the lab's server (table `web_sessions`).

A sign-in is a random value in a cookie; only its SHA-256 is stored. It
lasts at most `[server] session_hours`, and ends after
`session_idle_minutes` without a request, at sign-out, or when the person
is disabled.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from datalab.auth.users import User, Users

COOKIE = "datalab_session"
# last_seen_at is written at most this often per sign-in (it's on every request).
_TOUCH_EVERY = timedelta(minutes=1)


class WebSessions:
    """The server's browser sign-ins. Has what ApiProtection asks of a
    sign-in (web.py): `cookie_name`, `user_for`."""

    cookie_name = COOKIE

    def __init__(
        self,
        db: sqlite3.Connection,
        users: Users,
        *,
        hours: float,
        idle_minutes: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._users = users
        self._lifetime = timedelta(hours=hours)
        self._idle = timedelta(minutes=idle_minutes)
        self._clock = clock
        self._lock = threading.Lock()

    def start(self, user: User) -> str:
        """A new sign-in for `user`: the cookie's value."""
        value = secrets.token_urlsafe(32)
        now = self._now()
        with self._lock:
            self._db.execute(
                "INSERT INTO web_sessions (id_hash, user_id, created_at, last_seen_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_hash(value), user.id, _iso(now), _iso(now), _iso(now + self._lifetime)),
            )
        return value

    def user_for(self, cookie: str | None) -> User | None:
        """Who a cookie signs in, if it's a live sign-in of someone who may
        use DataLab now."""
        if not cookie:
            return None
        now = self._now()
        with self._lock:
            row = self._db.execute(
                "SELECT user_id, last_seen_at, expires_at FROM web_sessions "
                "WHERE id_hash = ? AND ended = 0",
                (_hash(cookie),),
            ).fetchone()
            if row is None:
                return None
            last_seen = datetime.fromisoformat(row["last_seen_at"])
            if now >= datetime.fromisoformat(row["expires_at"]) or now - last_seen >= self._idle:
                return None
            if now - last_seen >= _TOUCH_EVERY:
                self._db.execute(
                    "UPDATE web_sessions SET last_seen_at = ? WHERE id_hash = ?",
                    (_iso(now), _hash(cookie)),
                )
        return self._users.get(row["user_id"])

    def valid(self, cookie: str | None) -> bool:
        return self.user_for(cookie) is not None

    def end(self, cookie: str | None = None) -> None:
        """Sign this browser out."""
        if cookie:
            with self._lock:
                self._db.execute(
                    "UPDATE web_sessions SET ended = 1 WHERE id_hash = ?", (_hash(cookie),)
                )

    def end_all(self, user_id: int) -> None:
        """Sign a person out everywhere (access lost, disabled)."""
        with self._lock:
            self._db.execute("UPDATE web_sessions SET ended = 1 WHERE user_id = ?", (user_id,))

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")
