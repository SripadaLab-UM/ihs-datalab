"""People's GitHub sign-ins on the lab's server, encrypted in DataLab's
database (table `github_tokens`) with the server's token key.

The same interface as the keychain's TokenStore (repos/github.py), so each
person's GitHubAuth refreshes and uses their tokens just as a computer's does.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken

from datalab.repos.github import Tokens


class DbTokenStore:
    def __init__(
        self, db: sqlite3.Connection, user_id: int, key: Fernet, lock: threading.Lock
    ) -> None:
        self._db = db
        self._user_id = user_id
        self._key = key
        self._lock = lock

    def load(self) -> Tokens | None:
        with self._lock:
            row = self._db.execute(
                "SELECT ciphertext FROM github_tokens WHERE user_id = ?", (self._user_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return Tokens.from_json(self._key.decrypt(bytes(row["ciphertext"])).decode())
        except InvalidToken:
            return None  # another key: as if signed out

    def save(self, tokens: Tokens) -> None:
        sealed = self._key.encrypt(tokens.to_json().encode())
        with self._lock:
            self._db.execute(
                "INSERT INTO github_tokens (user_id, ciphertext, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET ciphertext = excluded.ciphertext, "
                "updated_at = excluded.updated_at",
                (self._user_id, sealed, datetime.now(UTC).isoformat(timespec="seconds")),
            )

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM github_tokens WHERE user_id = ?", (self._user_id,))


def token_key(secret: str) -> Fernet:
    """The server's token key (credentials.SERVER_TOKEN_KEY), a Fernet key."""
    try:
        return Fernet(secret.encode())
    except ValueError:
        raise ValueError(
            "The server's token key isn't a Fernet key (32 url-safe base64 bytes)."
        ) from None
