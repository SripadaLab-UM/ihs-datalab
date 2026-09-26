"""The data service: the one path by which anything queries the database.

Agents (through the `ihs-data` tools), SQL Playground, and workflows all call
`DataService.run_query`, so every query gets the same SQL check, guardrails,
and logging.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from datalab.config import QueryLimits
from datalab.data.access_log import AccessLog
from datalab.data.oracle import ExtractResult, QueryCancelled, QueryFailed
from datalab.data.sqlcheck import SqlRejected, check_sql


class Database(Protocol):
    def extract_to_csv(
        self,
        sql: str,
        binds: Mapping[str, Any],
        out_path: Path,
        *,
        max_rows: int,
        max_bytes: int,
        preview_rows: int,
        cancel: threading.Event,
    ) -> ExtractResult: ...


@dataclass(frozen=True)
class QueryOutcome:
    query_id: str
    columns: list[str]
    preview: list[list[str]]
    row_count: int
    bytes_written: int
    elapsed_seconds: float
    result_path: Path
    tables: list[str]
    warnings: list[str]


class DataService:
    def __init__(
        self,
        database: Database,
        access_log: AccessLog,
        limits: QueryLimits,
        allowed_schemas: frozenset[str],
    ) -> None:
        self._database = database
        self._log = access_log
        self._limits = limits
        self._allowed_schemas = allowed_schemas
        self._slots = asyncio.Semaphore(limits.max_concurrent_queries)

    async def run_query(
        self,
        *,
        session_id: str,
        sql: str,
        binds: Mapping[str, Any] | None,
        results_dir: Path,
        preview_rows: int | None = None,
    ) -> QueryOutcome:
        """Check, run, and log one query. Raises SqlRejected or QueryFailed."""
        query_id = _new_query_id()
        binds = dict(binds or {})
        try:
            checked = check_sql(sql, allowed_schemas=self._allowed_schemas)
            _check_binds(checked.binds, binds)
        except SqlRejected as rejection:
            self._log.rejected(
                query_id=query_id, session_id=session_id, sql=sql, reason=str(rejection)
            )
            raise

        tables = [str(t) for t in checked.tables]
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / f"{query_id}.csv"
        self._log.started(
            query_id=query_id, session_id=session_id, sql=checked.sql, binds=binds, tables=tables
        )
        cancel = threading.Event()
        try:
            async with self._slots:
                work = asyncio.to_thread(
                    self._database.extract_to_csv,
                    checked.sql,
                    binds,
                    out_path,
                    max_rows=self._limits.max_rows,
                    max_bytes=self._limits.max_bytes,
                    preview_rows=min(preview_rows or self._limits.preview_rows, 500),
                    cancel=cancel,
                )
                result = await _cancel_on_task_cancel(work, cancel)
        except QueryCancelled as error:
            self._log.finished(query_id, status="cancelled", message=str(error))
            raise
        except QueryFailed as error:
            self._log.finished(query_id, status="failed", message=str(error))
            raise
        except asyncio.CancelledError:
            self._log.finished(query_id, status="cancelled", message="Stopped.")
            raise

        self._log.finished(
            query_id,
            status="succeeded",
            row_count=result.row_count,
            bytes_written=result.bytes_written,
            elapsed_ms=round(result.elapsed_seconds * 1000),
            result_path=out_path,
        )
        return QueryOutcome(
            query_id=query_id,
            columns=result.columns,
            preview=result.preview,
            row_count=result.row_count,
            bytes_written=result.bytes_written,
            elapsed_seconds=result.elapsed_seconds,
            result_path=out_path,
            tables=tables,
            warnings=list(checked.warnings),
        )


async def _cancel_on_task_cancel(work, cancel: threading.Event) -> ExtractResult:
    """If the caller is cancelled (Stop), cancel the database call too, then wait for it."""
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        with contextlib.suppress(QueryFailed):
            await task
        raise


def _check_binds(expected: tuple[str, ...], given: dict[str, Any]) -> None:
    # Oracle bind names are case-insensitive.
    wanted = {name.lower() for name in expected}
    have = {name.lower() for name in given}
    missing = sorted(wanted - have)
    extra = sorted(have - wanted)
    if missing:
        raise SqlRejected(f"Missing values for bind variables: {', '.join(missing)}.")
    if extra:
        raise SqlRejected(f"Values were given for binds the query doesn't use: {', '.join(extra)}.")


def _new_query_id() -> str:
    return f"q_{datetime.now(UTC):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
