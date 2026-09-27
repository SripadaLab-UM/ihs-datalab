"""Records of every query: the Data accessed panel and the audit log.

Two stores, on purpose:
- the `queries` table keeps the full record (SQL text, bind values) for the
  user's own Data accessed panel. It is local conversation data;
- `logs/audit.jsonl` is append-only and metadata only: a fingerprint of the
  SQL, the tables touched, sizes and timings, and never SQL text or values.

Each query has an origin, what it was run for, and an owner (`session_id`):
a conversation (its id), the SQL Playground (a `pg_…` id), or a workflow run
(a `run_…` id). A panel lists one owner's queries with `for_origin`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

Origin = Literal["conversation", "playground", "run"]
ORIGINS: frozenset[str] = frozenset(get_args(Origin))
# The owner ids of queries that aren't a conversation's start with these.
# (Later, origin could simply be read from the owner id's prefix instead of
# being passed separately; for now both are given, and must agree.)
_OWNER_PREFIXES = {"playground": "pg_", "run": "run_"}


def check_owner(origin: str, owner_id: str) -> None:
    """Refuse an owner id that doesn't match its origin, so one kind of
    owner's queries can never be listed as another's."""
    if origin not in ORIGINS:
        raise ValueError(f"Unknown query origin: {origin!r}")
    prefix = _OWNER_PREFIXES.get(origin)
    if prefix is not None and not owner_id.startswith(prefix):
        raise ValueError(f"A {origin} query's owner id must start with {prefix!r}")
    if prefix is None and owner_id.startswith(tuple(_OWNER_PREFIXES.values())):
        raise ValueError("A conversation's query can't have a playground or run id")


@dataclass(frozen=True)
class QueryRecord:
    id: str
    session_id: str
    started_at: str
    finished_at: str | None
    status: str
    sql_text: str
    binds: dict[str, Any]
    tables: list[str]
    row_count: int | None
    bytes_written: int | None
    elapsed_ms: int | None
    result_path: str | None
    message: str | None
    origin: Origin = "conversation"


class AccessLog:
    def __init__(self, db: sqlite3.Connection, audit_file: Path) -> None:
        self._db = db
        self._audit_file = audit_file
        self._lock = threading.Lock()
        audit_file.parent.mkdir(parents=True, exist_ok=True)

    def started(
        self,
        *,
        query_id: str,
        session_id: str,
        sql: str,
        binds: dict[str, Any],
        tables: list[str],
        origin: Origin = "conversation",
    ) -> None:
        check_owner(origin, session_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO queries (id, session_id, origin, started_at, status, sql_text, "
                "binds_json, tables_json) VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
                (query_id, session_id, origin, _now(), sql, _json(binds), _json(tables)),
            )

    def finished(
        self,
        query_id: str,
        *,
        status: str,
        row_count: int | None = None,
        bytes_written: int | None = None,
        elapsed_ms: int | None = None,
        result_path: Path | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE queries SET finished_at = ?, status = ?, row_count = ?, bytes_written = ?, "
                "elapsed_ms = ?, result_path = ?, message = ? WHERE id = ?",
                (
                    _now(),
                    status,
                    row_count,
                    bytes_written,
                    elapsed_ms,
                    str(result_path) if result_path else None,
                    message,
                    query_id,
                ),
            )
            record = self._get(query_id)
            if record:
                self._append_audit(record)

    def rejected(
        self,
        *,
        query_id: str,
        session_id: str,
        sql: str,
        reason: str,
        origin: Origin = "conversation",
    ) -> None:
        """A query the SQL check refused. It never reached the database."""
        self.started(
            query_id=query_id, session_id=session_id, sql=sql, binds={}, tables=[], origin=origin
        )
        self.finished(query_id, status="rejected", message=reason)

    def end_cut_off_queries(self) -> int:
        """Mark queries a previous run of DataLab left running as cancelled.

        Called at startup, when no query can be running (one DataLab per
        data folder). The log has no "interrupted" status, so they're
        cancelled, and the message says why. Returns how many there were.
        """
        with self._lock:
            ids = [
                row["id"]
                for row in self._db.execute("SELECT id FROM queries WHERE status = 'running'")
            ]
        for query_id in ids:
            self.finished(
                query_id,
                status="cancelled",
                message="DataLab stopped before this query finished. Nothing was kept.",
            )
        return len(ids)

    def record_export(
        self,
        *,
        session_id: str,
        destination: str,
        files: int,
        contains_study_data: bool,
        query_id: str | None = None,
    ) -> None:
        """An export the person made: where to, and how many files. No contents.
        `query_id` names the query whose result it was, for a Playground export."""
        entry = {
            "ts": _now(),
            "event": "export",
            "session_id": session_id,
            "destination": destination,
            "files": files,
            "contains_study_data": contains_study_data,
            **({"query_id": query_id} if query_id else {}),
        }
        with self._lock, self._audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def for_session(self, session_id: str) -> list[QueryRecord]:
        """A conversation's queries (its Data accessed panel)."""
        return self.for_origin("conversation", session_id)

    def for_origin(self, origin: Origin, owner_id: str) -> list[QueryRecord]:
        """One owner's queries, oldest first: a conversation's, a playground's, or a run's."""
        check_owner(origin, owner_id)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM queries WHERE origin = ? AND session_id = ? ORDER BY started_at",
                (origin, owner_id),
            ).fetchall()
        return [_record(row) for row in rows]

    def _get(self, query_id: str) -> QueryRecord | None:
        row = self._db.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()
        return _record(row) if row else None

    def _append_audit(self, record: QueryRecord) -> None:
        entry = {
            "ts": record.finished_at,
            "query_id": record.id,
            "origin": record.origin,
            "session_id": record.session_id,
            "status": record.status,
            "sql_sha256": hashlib.sha256(record.sql_text.encode()).hexdigest(),
            "bind_names": sorted(record.binds),
            "tables": record.tables,
            "row_count": record.row_count,
            "bytes": record.bytes_written,
            "elapsed_ms": record.elapsed_ms,
        }
        with self._audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def _record(row: sqlite3.Row) -> QueryRecord:
    return QueryRecord(
        id=row["id"],
        session_id=row["session_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        sql_text=row["sql_text"],
        binds=json.loads(row["binds_json"]),
        tables=json.loads(row["tables_json"]),
        row_count=row["row_count"],
        bytes_written=row["bytes_written"],
        elapsed_ms=row["elapsed_ms"],
        result_path=row["result_path"],
        message=row["message"],
        origin=row["origin"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
