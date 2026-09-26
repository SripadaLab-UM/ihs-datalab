"""Records of every query: the Data accessed panel and the audit log.

Two stores, on purpose:
- the `queries` table keeps the full record (SQL text, bind values) for the
  user's own Data accessed panel. It is local conversation data;
- `logs/audit.jsonl` is append-only and metadata only: a fingerprint of the
  SQL, the tables touched, sizes and timings, and never SQL text or values.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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


class AccessLog:
    def __init__(self, db: sqlite3.Connection, audit_file: Path) -> None:
        self._db = db
        self._audit_file = audit_file
        self._lock = threading.Lock()
        audit_file.parent.mkdir(parents=True, exist_ok=True)

    def started(
        self, *, query_id: str, session_id: str, sql: str, binds: dict[str, Any], tables: list[str]
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO queries (id, session_id, started_at, status, sql_text, binds_json, "
                "tables_json) VALUES (?, ?, ?, 'running', ?, ?, ?)",
                (query_id, session_id, _now(), sql, _json(binds), _json(tables)),
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

    def rejected(self, *, query_id: str, session_id: str, sql: str, reason: str) -> None:
        """A query the SQL check refused. It never reached the database."""
        self.started(query_id=query_id, session_id=session_id, sql=sql, binds={}, tables=[])
        self.finished(query_id, status="rejected", message=reason)

    def record_export(
        self, *, session_id: str, destination: str, files: int, contains_study_data: bool
    ) -> None:
        """An export the person made: where to, and how many files. No contents."""
        entry = {
            "ts": _now(),
            "event": "export",
            "session_id": session_id,
            "destination": destination,
            "files": files,
            "contains_study_data": contains_study_data,
        }
        with self._lock, self._audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def for_session(self, session_id: str) -> list[QueryRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM queries WHERE session_id = ? ORDER BY started_at", (session_id,)
            ).fetchall()
        return [_record(row) for row in rows]

    def _get(self, query_id: str) -> QueryRecord | None:
        row = self._db.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()
        return _record(row) if row else None

    def _append_audit(self, record: QueryRecord) -> None:
        entry = {
            "ts": record.finished_at,
            "query_id": record.id,
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
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
