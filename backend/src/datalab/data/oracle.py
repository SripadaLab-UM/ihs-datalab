"""Talking to Oracle: read-only sessions, guarded extraction to CSV.

Every connection enables only the configured read-only roles and starts a
read-only transaction, before any query runs. That matters because the shared
service account also holds a write role (see docs/SAFETY.md).

`call_timeout` only limits a single database round trip, so each query also
has an end-to-end deadline: a timer cancels the call in Oracle when it
expires, and the partial output is removed.
"""

from __future__ import annotations

import contextlib
import csv
import re
import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oracledb

from datalab.config import OracleSettings, QueryLimits

# Return CLOBs as strings and dates as datetimes, so rows can be written as CSV.
oracledb.defaults.fetch_lobs = False

_FETCH_BATCH = 5_000
SYNTHETIC_MARKER = "DATALAB_SYNTHETIC.MARKER"
_ROLE_NAME = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


class QueryFailed(RuntimeError):
    """The database refused or failed the query. The message is safe to show."""


class QueryTimedOut(QueryFailed):
    pass


class QueryCancelled(QueryFailed):
    pass


class LimitExceeded(QueryFailed):
    pass


class NotSyntheticDatabase(QueryFailed):
    pass


@dataclass
class ExtractResult:
    columns: list[str]
    preview: list[list[str]]
    row_count: int
    bytes_written: int
    elapsed_seconds: float


@dataclass(frozen=True)
class SessionPrivileges:
    """What a DataLab session can actually do, as the database reports it."""

    enabled_roles: frozenset[str]
    system_privileges: frozenset[str]
    # (schema, privilege) pairs on study schemas other than plain reads.
    non_read_object_privileges: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    @property
    def is_read_only(self) -> bool:
        return self.system_privileges == {"CREATE SESSION"} and not self.non_read_object_privileges


class OracleDatabase:
    def __init__(self, settings: OracleSettings, password: str, limits: QueryLimits) -> None:
        for role in settings.read_only_roles:
            if not _ROLE_NAME.match(role):
                raise ValueError(f"Not a valid role name: {role!r}")
        self._settings = settings
        self._password = password
        self._limits = limits

    def connect(self, *, timeout: float | None = None) -> oracledb.Connection:
        """A read-only session. `timeout` (seconds) also limits connecting and each round trip."""
        extra = {"tcp_connect_timeout": timeout, "retry_count": 0} if timeout else {}
        connection = oracledb.connect(
            user=self._settings.user, password=self._password, dsn=self._settings.dsn, **extra
        )
        try:
            round_trip = timeout or self._limits.round_trip_timeout_seconds
            connection.call_timeout = int(round_trip * 1000)
            with connection.cursor() as cursor:
                roles = ", ".join(self._settings.read_only_roles) or "NONE"
                cursor.execute(f"SET ROLE {roles}")
                cursor.execute("SET TRANSACTION READ ONLY")
                if self._settings.require_synthetic_marker:
                    _require_marker(cursor)
        except BaseException:
            connection.close()
            raise
        return connection

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
    ) -> ExtractResult:
        """Run `sql` and stream every row to `out_path`, within the limits.

        On any failure the partial file is removed, so a result file only ever
        exists if the whole result was written.
        """
        started = time.monotonic()
        partial = out_path.with_name(out_path.name + ".partial")
        connection = self.connect()
        done = threading.Event()
        timed_out = threading.Event()

        def on_deadline() -> None:
            timed_out.set()
            _cancel_quietly(connection)

        deadline = threading.Timer(self._limits.deadline_seconds, on_deadline)
        watcher = threading.Thread(
            target=_cancel_when_requested, args=(cancel, done, connection), daemon=True
        )
        deadline.start()
        watcher.start()
        try:
            with connection.cursor() as cursor:
                cursor.arraysize = _FETCH_BATCH
                cursor.prefetchrows = _FETCH_BATCH + 1
                cursor.execute(sql, dict(binds))
                columns = [str(d[0]) for d in cursor.description or []]
                result = self._write_rows(
                    cursor, columns, partial, max_rows, max_bytes, preview_rows
                )
            partial.replace(out_path)
            result.elapsed_seconds = time.monotonic() - started
            return result
        except oracledb.Error as error:
            partial.unlink(missing_ok=True)
            if cancel.is_set():
                raise QueryCancelled("The query was stopped.") from error
            if timed_out.is_set():
                raise QueryTimedOut(
                    f"The query ran longer than {self._limits.deadline_seconds:.0f} seconds "
                    "and was cancelled."
                ) from error
            raise QueryFailed(_oracle_message(error)) from error
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            done.set()
            deadline.cancel()
            watcher.join(timeout=1)
            _close_quietly(connection)

    def _write_rows(
        self,
        cursor: oracledb.Cursor,
        columns: list[str],
        path: Path,
        max_rows: int,
        max_bytes: int,
        preview_rows: int,
    ) -> ExtractResult:
        preview: list[list[str]] = []
        rows = 0
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            while batch := cursor.fetchmany():
                rows += len(batch)
                if rows > max_rows:
                    raise LimitExceeded(
                        f"The result has more than {max_rows:,} rows. Narrow the query, "
                        "or ask the user to approve a larger extraction."
                    )
                values = [["" if v is None else str(v) for v in row] for row in batch]
                if len(preview) < preview_rows:
                    preview.extend(values[: preview_rows - len(preview)])
                writer.writerows(values)
                size = handle.tell()
                if size > max_bytes:
                    raise LimitExceeded(
                        f"The result is larger than {max_bytes / 1024**3:.1f} GB. Narrow the "
                        "query, or ask the user to approve a larger extraction."
                    )
                if shutil.disk_usage(path.parent).free < self._limits.min_free_disk_bytes:
                    raise LimitExceeded("The disk is nearly full, so the query was stopped.")
            size = handle.tell()
        return ExtractResult(columns, preview, rows, size, 0.0)

    def session_privileges(self, *, timeout: float | None = None) -> SessionPrivileges:
        """Ask the database what a DataLab session is allowed to do."""
        connection = self.connect(timeout=timeout)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT role FROM session_roles")
                roles = frozenset(r[0] for r in cursor)
                cursor.execute("SELECT privilege FROM session_privs")
                system = frozenset(r[0] for r in cursor)
                schemas = sorted(self._settings.allowed_schemas)
                placeholders = ", ".join(f":s{i}" for i in range(len(schemas)))
                cursor.execute(
                    "SELECT DISTINCT table_schema, privilege FROM all_tab_privs "
                    f"WHERE table_schema IN ({placeholders}) "
                    "AND privilege NOT IN ('SELECT', 'READ', 'INHERIT PRIVILEGES')",
                    {f"s{i}": s for i, s in enumerate(schemas)},
                )
                non_read = frozenset((r[0], r[1]) for r in cursor)
            return SessionPrivileges(roles, system, non_read)
        finally:
            _close_quietly(connection)


def _require_marker(cursor: oracledb.Cursor) -> None:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {SYNTHETIC_MARKER}")
        (count,) = cursor.fetchone() or (0,)
    except oracledb.Error:
        count = 0
    if not count:
        raise NotSyntheticDatabase(
            "Practice DataLab only works with the local synthetic database, and this "
            "database isn't it. No query was run."
        )


def _cancel_when_requested(
    cancel: threading.Event, done: threading.Event, connection: oracledb.Connection
) -> None:
    while not done.is_set():
        if cancel.wait(timeout=0.25):
            if not done.is_set():
                _cancel_quietly(connection)
            return


def _cancel_quietly(connection: oracledb.Connection) -> None:
    with contextlib.suppress(oracledb.Error):
        connection.cancel()


def _close_quietly(connection: oracledb.Connection) -> None:
    with contextlib.suppress(oracledb.Error):
        connection.rollback()
    with contextlib.suppress(oracledb.Error):
        connection.close()


def _oracle_message(error: oracledb.Error) -> str:
    # The first line of an Oracle error names the problem (e.g. ORA-00942: table
    # or view does not exist) without echoing data values.
    return str(error).splitlines()[0][:300]
