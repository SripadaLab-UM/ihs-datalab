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
import datetime
import re
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oracledb

from datalab.config import OracleSettings, QueryLimits
from datalab.data.oracle_errors import (
    CALL_TIMEOUT_CODES,
    SIGN_IN,
    connection_message,
    explain,
    oracle_code,
    sign_in_message,
)
from datalab.data.oracle_errors import category as error_category

# Return CLOBs as strings and dates as datetimes, so rows can be written as CSV.
oracledb.defaults.fetch_lobs = False

_FETCH_BATCH = 5_000
SYNTHETIC_MARKER = "DATALAB_SYNTHETIC.MARKER"
_ROLE_NAME = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


class QueryFailed(RuntimeError):
    """The database refused or failed the query. The message is safe to show.

    `category` says what kind of failure (oracle_errors.py: sql, permission,
    timeout, connection, cancelled, result_limit) and `code` the Oracle or
    driver code, for the agent and the chat. DataLab's data service adds
    `query_id` and `elapsed_ms` once it has logged the failure."""

    category = "sql"

    def __init__(self, message: str, *, category: str | None = None, code: str | None = None):
        super().__init__(message)
        if category is not None:
            self.category = category
        self.code = code
        self.query_id: str | None = None
        self.elapsed_ms: int | None = None


class QueryTimedOut(QueryFailed):
    """`reason` is call_timeout (one round trip to Oracle took longer than
    `limit_seconds`) or deadline (the whole query did)."""

    category = "timeout"

    def __init__(
        self,
        message: str,
        *,
        reason: str = "deadline",
        limit_seconds: float | None = None,
        code: str | None = None,
    ):
        super().__init__(message, code=code)
        self.reason = reason
        self.limit_seconds = limit_seconds


class QueryCancelled(QueryFailed):
    category = "cancelled"


class LimitExceeded(QueryFailed):
    category = "result_limit"


class NotSyntheticDatabase(QueryFailed):
    """The practice port's database has no marker table: not the synthetic one."""

    category = "connection"


class MarkerNotVerified(QueryFailed):
    """The marker couldn't be checked just now (the database answered with
    another error). Refused like NotSyntheticDatabase, since no query runs
    without a verified marker, but worth trying again: it isn't known to be
    another database."""

    category = "connection"


@dataclass
class ExtractResult:
    columns: list[str]
    preview: list[list[str]]
    row_count: int
    bytes_written: int
    elapsed_seconds: float
    # Oracle's type for each column, such as NUMBER or VARCHAR2(64), as the
    # database described the result. Empty when it wasn't given.
    column_types: list[str] = field(default_factory=list)


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
        try:
            connection = self.connect()
        except oracledb.Error as error:
            # No database to talk to (it's down, or the port is closed): the
            # query failed, with a message that's safe to show.
            code = oracle_code(error)
            said = sign_in_message(code) if code in SIGN_IN else connection_message(code)
            raise QueryFailed(said, category="connection", code=code) from error
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
                description = cursor.description or []
                columns = [str(d[0]) for d in description]
                result = self._write_rows(
                    cursor,
                    columns,
                    partial,
                    max_rows,
                    max_bytes,
                    preview_rows,
                    stopped=lambda: self._stopped(cancel, timed_out),
                )
                result.column_types = [_type_label(d) for d in description]
            partial.replace(out_path)
            result.elapsed_seconds = time.monotonic() - started
            return result
        except oracledb.Error as error:
            partial.unlink(missing_ok=True)
            if cancel.is_set():
                raise QueryCancelled("The query was stopped.") from error
            code = oracle_code(error)
            if timed_out.is_set():
                limit = self._limits.deadline_seconds
                raise QueryTimedOut(
                    f"The query ran longer than {limit:.0f} seconds and was cancelled.",
                    reason="deadline",
                    limit_seconds=limit,
                    code=code,
                ) from error
            if code in CALL_TIMEOUT_CODES:
                limit = self._limits.round_trip_timeout_seconds
                raise QueryTimedOut(
                    f"Oracle took longer than {limit:.0f} s to answer one request ({code}), so "
                    "the query was cancelled.",
                    reason="call_timeout",
                    limit_seconds=limit,
                    code=code,
                ) from error
            raise QueryFailed(
                explain(error, sql), category=error_category(code), code=code
            ) from error
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
        stopped: Callable[[], None] = lambda: None,
    ) -> ExtractResult:
        """Write every row to `path`. `stopped` raises if Stop or the deadline came.

        The driver call is cancelled in Oracle for those; `stopped` is for the
        rewrite afterwards, which doesn't talk to the database.
        """
        preview: list[list[str]] = []
        rows = 0
        # Per column: whether it held a date or time, and whether any had a time of day.
        dated = [False] * len(columns)
        timed = [False] * len(columns)
        # Only Oracle DATE columns can be written as plain dates. A TIMESTAMP
        # keeps its time, midnight or not: it is a moment, not a day.
        described = list(getattr(cursor, "description", None) or [])
        is_date = [
            index < len(described) and described[index][1] is oracledb.DB_TYPE_DATE
            for index in range(len(columns))
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            while batch := cursor.fetchmany():
                rows += len(batch)
                if rows > max_rows:
                    raise LimitExceeded(
                        f"The result has more than {max_rows:,} rows, the most one query can "
                        "return, so it was stopped and nothing was kept. Narrow the query: "
                        "fewer rows, columns or cohorts."
                    )
                values = [
                    [csv_value(v, i, dated, timed) for i, v in enumerate(row)] for row in batch
                ]
                if len(preview) < preview_rows:
                    preview.extend(values[: preview_rows - len(preview)])
                writer.writerows(values)
                size = handle.tell()
                if size > max_bytes:
                    raise LimitExceeded(
                        f"The result is larger than {max_bytes / 1024**3:.1f} GB, the most one "
                        "query can return, so it was stopped and nothing was kept. Narrow the "
                        "query: fewer rows, columns or cohorts."
                    )
                if shutil.disk_usage(path.parent).free < self._limits.min_free_disk_bytes:
                    raise LimitExceeded("The disk is nearly full, so the query was stopped.")
            size = handle.tell()
        date_only = [i for i in range(len(columns)) if is_date[i] and dated[i] and not timed[i]]
        if date_only:
            if shutil.disk_usage(path.parent).free < self._limits.min_free_disk_bytes + size:
                raise LimitExceeded("The disk is nearly full, so the query was stopped.")
            size = _dates_only(path, date_only, stopped)
            preview = [_drop_midnight(row, date_only) for row in preview]
        return ExtractResult(columns, preview, rows, size, 0.0)

    def _stopped(self, cancel: threading.Event, timed_out: threading.Event) -> None:
        if cancel.is_set():
            raise QueryCancelled("The query was stopped.")
        if timed_out.is_set():
            raise QueryTimedOut(
                f"The query ran longer than {self._limits.deadline_seconds:.0f} seconds "
                "and was cancelled."
            )

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


def csv_value(value: Any, index: int, dated: list[bool], timed: list[bool]) -> str:
    """One value as text for a result CSV, the way R and the lab's code read it.

    - A date or time is `YYYY-MM-DD HH:MM:SS` (with `.ffffff` if it has
      fractions), which R's `as.POSIXct` reads; it drops the time from the
      ISO `T` form without a warning. A DATE column whose every value is at
      midnight is then written as plain dates by `_dates_only`, as R writes
      such a column and as the legacy scripts (through ROracle) saw it: the
      Garmin calendar-date rule in ihsDataR treats any time part as a cutoff.
      python-oracledb returns a TIMESTAMP WITH TIME ZONE as the local time
      without its offset, and it is written so.
    - RAW is upper-case hex, as Oracle's RAWTOHEX gives it, so a key reads
      the same whether the SQL converts it or not.
    - Missing is empty.
    """
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        dated[index] = True
        if value.time() != datetime.time(0) or value.tzinfo is not None:
            timed[index] = True
        return str(value)
    if isinstance(value, bytes):
        return value.hex().upper()
    return str(value)


_MIDNIGHT = " 00:00:00"


def _drop_midnight(row: list[str], columns: list[int]) -> list[str]:
    out = list(row)
    for i in columns:
        if out[i].endswith(_MIDNIGHT):
            out[i] = out[i][: -len(_MIDNIGHT)]
    return out


_REWRITE_CHECK_ROWS = 5_000


def _dates_only(path: Path, columns: list[int], stopped: Callable[[], None]) -> int:
    """Rewrite `path` with the given columns as plain dates. Returns the new size.

    Checks `stopped` every few thousand rows, so Stop and the deadline still
    end a query while its result is being rewritten.
    """
    rewritten = path.with_name(path.name + ".dates")
    try:
        with (
            path.open(newline="", encoding="utf-8") as source,
            rewritten.open("w", newline="", encoding="utf-8") as target,
        ):
            reader = csv.reader(source)
            writer = csv.writer(target)
            writer.writerow(next(reader))
            for number, row in enumerate(reader, 1):
                if number % _REWRITE_CHECK_ROWS == 0:
                    stopped()
                writer.writerow(_drop_midnight(row, columns))
            stopped()
            size = target.tell()
        rewritten.replace(path)
    except BaseException:
        rewritten.unlink(missing_ok=True)
        raise
    return size


def _require_marker(cursor: oracledb.Cursor) -> None:
    """Fail closed: a query runs only once the marker is seen. Only "no
    such table" (ORA-00942, which is also what a table this user can't read
    looks like) says it isn't the synthetic database; any other error means
    it couldn't be checked, which is refused too."""
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {SYNTHETIC_MARKER}")
        (count,) = cursor.fetchone() or (0,)
    except oracledb.Error as error:
        code = getattr(error.args[0], "full_code", None) if error.args else None
        if code != "ORA-00942":
            raise MarkerNotVerified(
                "DataLab couldn't check that this is the local synthetic database "
                f"({code or type(error).__name__}), so no query was run. Try again in a moment."
            ) from None
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


# python-oracledb's type names that differ from Oracle's own. LOBs are
# fetched as strings and bytes (`fetch_lobs = False`), so the driver calls
# them LONG types; they are almost always CLOBs and BLOBs, and are named so.
_TYPE_NAMES = {
    "VARCHAR": "VARCHAR2",
    "NVARCHAR": "NVARCHAR2",
    "LONG": "CLOB",
    "LONG_NVARCHAR": "NCLOB",
    "LONG_RAW": "BLOB",
    "BINARY_DOUBLE": "BINARY_DOUBLE",
    "BINARY_FLOAT": "BINARY_FLOAT",
    "BINARY_INTEGER": "BINARY_INTEGER",
    "INTERVAL_DS": "INTERVAL DAY TO SECOND",
    "INTERVAL_YM": "INTERVAL YEAR TO MONTH",
}
_TIMESTAMPS = {
    "TIMESTAMP": "",
    "TIMESTAMP_TZ": " WITH TIME ZONE",
    "TIMESTAMP_LTZ": " WITH LOCAL TIME ZONE",
}


def _type_label(column: Any) -> str:
    """A result column's type as Oracle names it: NUMBER(10,2), VARCHAR2(64), DATE."""
    raw = getattr(column.type_code, "name", str(column.type_code)).removeprefix("DB_TYPE_")
    if raw in _TIMESTAMPS:
        precision = f"({column.scale})" if column.scale is not None else ""
        return f"TIMESTAMP{precision}{_TIMESTAMPS[raw]}"
    name = _TYPE_NAMES.get(raw, raw.replace("_", " "))
    if name in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW") and column.display_size:
        # In characters, as the column was declared (internal_size is in bytes).
        return f"{name}({column.display_size})"
    if name == "NUMBER" and column.precision:
        if column.scale == -127:
            # A FLOAT: its precision is in binary digits.
            return f"FLOAT({column.precision})"
        return (
            f"NUMBER({column.precision},{column.scale})"
            if column.scale
            else f"NUMBER({column.precision})"
        )
    return name
