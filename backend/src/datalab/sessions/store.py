"""Conversations and their event logs, in SQLite.

Every event gets a sequence number. The browser follows a conversation with
"give me everything after N", so it can close, reconnect, or switch tabs
without losing anything, and without interrupting a running turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from datalab.sessions.tokens import SessionKind


@dataclass(frozen=True)
class Conversation:
    id: str
    kind: SessionKind
    mode: str
    title: str
    model: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Event:
    seq: int
    created_at: str
    type: str
    data: dict[str, Any]


class ConversationStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._waiters: dict[str, asyncio.Future[None]] = {}

    # Conversations -----------------------------------------------------------

    def create(self, *, kind: SessionKind, mode: str, title: str, model: str) -> Conversation:
        now = _now()
        conversation = Conversation(
            id=f"c_{secrets.token_hex(8)}",
            kind=kind,
            mode=mode,
            title=title,
            model=model,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation.id,
                    kind,
                    mode,
                    title,
                    model,
                    now,
                    now,
                ),
            )
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return _conversation(row) if row else None

    def list(self) -> list[Conversation]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [_conversation(row) for row in rows]

    def rename(self, conversation_id: str, title: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), conversation_id),
            )

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    # Events ------------------------------------------------------------------

    def append(self, conversation_id: str, type: str, data: dict[str, Any]) -> Event:
        now = _now()
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            seq = row[0]
            self._db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (conversation_id, seq, now, type, json.dumps(data, default=str)),
            )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
        self._notify(conversation_id)
        return Event(seq, now, type, data)

    def events_after(self, conversation_id: str, seq: int, limit: int = 1000) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, created_at, type, data_json FROM events "
                "WHERE conversation_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (conversation_id, seq, limit),
            ).fetchall()
        return [Event(r[0], r[1], r[2], json.loads(r[3])) for r in rows]

    def count(self, conversation_id: str, type: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE conversation_id = ? AND type = ?",
                (conversation_id, type),
            ).fetchone()
        return row[0]

    def last(self, conversation_id: str, type: str) -> Event | None:
        with self._lock:
            row = self._db.execute(
                "SELECT seq, created_at, type, data_json FROM events "
                "WHERE conversation_id = ? AND type = ? ORDER BY seq DESC LIMIT 1",
                (conversation_id, type),
            ).fetchone()
        return Event(row[0], row[1], row[2], json.loads(row[3])) if row else None

    def events_of_types_after(
        self, conversation_id: str, seq: int, types: tuple[str, ...]
    ) -> list[Event]:
        marks = ", ".join("?" for _ in types)
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, created_at, type, data_json FROM events "
                f"WHERE conversation_id = ? AND seq > ? AND type IN ({marks}) ORDER BY seq",
                (conversation_id, seq, *types),
            ).fetchall()
        return [Event(r[0], r[1], r[2], json.loads(r[3])) for r in rows]

    async def wait_for_events(self, conversation_id: str, after: int, timeout: float) -> None:
        """Return once this conversation has events after `after`, or after `timeout`.

        Events are appended on the event loop, so registering the waiter and
        then re-checking the log can't miss one.
        """
        waiter = self._waiters.setdefault(
            conversation_id, asyncio.get_running_loop().create_future()
        )
        if self.events_after(conversation_id, after, limit=1):
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiter), timeout)

    def _notify(self, conversation_id: str) -> None:
        waiter = self._waiters.pop(conversation_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(None)


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        kind=row["kind"],
        mode=row["mode"],
        title=row["title"],
        model=row["model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
