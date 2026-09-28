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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from datalab.config import QueryLimits
from datalab.data.access_log import AccessLog, Origin, check_owner
from datalab.data.autocatalog import CatalogAutoBuild
from datalab.data.catalog import Catalog
from datalab.data.oracle import ExtractResult, QueryCancelled, QueryFailed
from datalab.data.sqlcheck import SqlRejected, TableRef, check_sql


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
    # Oracle's type for each column, when the database gave them.
    column_types: list[str] = field(default_factory=list)


class DataService:
    def __init__(
        self,
        database: Database,
        access_log: AccessLog,
        limits: QueryLimits,
        allowed_schemas: frozenset[str],
        catalog: Catalog,
        *,
        catalog_build: CatalogAutoBuild | None = None,
    ) -> None:
        self._database = database
        # Every column a query names is checked against the catalog: with an
        # empty one, no query runs. DataLab's own is built on first use.
        self._catalog = catalog
        self._catalog_build = catalog_build
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
        origin: Origin = "conversation",
        allowed_tables: frozenset[str] | None = None,
    ) -> QueryOutcome:
        """Check, run, and log one query. Raises SqlRejected or QueryFailed.

        `session_id` is the query's owner: a conversation id, or a playground
        (`pg_…`) or workflow run (`run_…`) id, matching `origin`.

        `allowed_tables` (`SCHEMA.OBJECT`, upper case), when given, is all the
        query may read: a workflow's declared `reads:`. Anything else is
        refused before the query runs, and logged as rejected.
        """
        # Before anything is logged or run: a wrong owner is a bug in DataLab.
        check_owner(origin, session_id)
        if origin == "run" and allowed_tables is None:
            raise ValueError("A workflow run's query needs the workflow's declared tables.")
        query_id = _new_query_id()
        binds = dict(binds or {})
        build = self._catalog_build
        if (
            build is not None
            and not len(self._catalog)
            and not await asyncio.to_thread(build.ensure)
        ):
            reason = (
                "DataLab has no catalog of the cohorts' tables yet, so it can't check "
                "queries. It builds one as soon as it can connect to the database: "
                f"{build.problem or 'not connected yet'}"
            )
            self._log.rejected(
                query_id=query_id, session_id=session_id, sql=sql, reason=reason, origin=origin
            )
            raise QueryFailed(reason)
        try:
            checked = check_sql(
                sql, allowed_schemas=self._allowed_schemas, columns=self._catalog.column_index()
            )
            _check_binds(checked.binds, binds)
            if allowed_tables is not None:
                _check_tables(checked.tables, allowed_tables)
        except SqlRejected as rejection:
            self._log.rejected(
                query_id=query_id,
                session_id=session_id,
                sql=sql,
                reason=str(rejection),
                origin=origin,
            )
            raise

        tables = [str(t) for t in checked.tables]
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / f"{query_id}.csv"
        self._log.started(
            query_id=query_id,
            session_id=session_id,
            sql=checked.sql,
            binds=binds,
            tables=tables,
            origin=origin,
            result_path=out_path,
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
            self._log.finished(query_id, status="cancelled", message=str(error), reason="stopped")
            raise
        except QueryFailed as error:
            self._log.finished(query_id, status="failed", message=str(error))
            raise
        except asyncio.CancelledError:
            # Stopped just as it finished: the result isn't kept, as the log says.
            out_path.unlink(missing_ok=True)
            self._log.finished(query_id, status="cancelled", message="Stopped.", reason="stopped")
            raise
        except BaseException:
            # Anything else (a bug, an unexpected error): never left as running.
            self._log.finished(
                query_id, status="failed", message="The query failed in DataLab; see its log."
            )
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
            column_types=list(result.column_types),
        )


async def _cancel_on_task_cancel(work, cancel: threading.Event) -> ExtractResult:
    """If the caller is cancelled (Stop), cancel the database call too, then wait for it."""
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        # Keep the query's slot until the database call has really ended, even
        # if the caller is cancelled again while Oracle is still cancelling.
        while not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # retrieved: it's reported as the stop
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


def _check_tables(tables: tuple[TableRef, ...], allowed: frozenset[str]) -> None:
    outside = sorted(str(t) for t in tables if str(t) not in allowed)
    if outside:
        raise SqlRejected(
            f"This query reads {', '.join(outside)}, which the workflow doesn't declare "
            "under `reads:`."
        )


def _new_query_id() -> str:
    return f"q_{datetime.now(UTC):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
