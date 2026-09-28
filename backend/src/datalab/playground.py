"""The SQL Playground: the person's own SQL, run through the data service.

Each DataLab has one Playground, with a `pg_…` id kept in its folder. Its
queries go through `DataService.run_query` with `origin="playground"`, so they
get the same SQL check, extraction cap and logging as the agent's, and list
as its history with `AccessLog.for_origin("playground", id)`.

Results are kept in `<data_dir>/playground/<id>/results/`: never in any
conversation's folder, so no agent can see them. They leave only through an
export the person makes.

Queries run in the background, so they can be stopped. Runs are remembered
only while DataLab is running; the history (and each result's file) stays.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import sqlglot
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.tokenizer_core import TokenType

from datalab.config import Settings
from datalab.data.access_log import AccessLog, QueryRecord
from datalab.data.catalog import Catalog
from datalab.data.oracle import QueryCancelled, QueryFailed
from datalab.data.service import DataService, QueryOutcome
from datalab.data.sqlcheck import SqlRejected, check_sql

RunState = Literal["running", "succeeded", "failed", "rejected", "stopped"]

_ID = re.compile(r"pg_[0-9a-f]{12}")
_QUERY_ID = re.compile(r"q_\d{8}T\d{6}_[0-9a-f]{6}")
# Runs remembered after they finish; older ones are forgotten (their history stays).
_KEEP_FINISHED = 50
# Longest cell shown in the results grid. The whole value is in the CSV.
_CELL_LIMIT = 2_000


@dataclass(frozen=True)
class Position:
    """Where in the SQL a problem is: 1-based lines and columns, end exclusive."""

    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class Diagnostic:
    message: str
    severity: Literal["error", "warning"]
    position: Position | None = None


@dataclass(frozen=True)
class CheckReport:
    errors: list[Diagnostic]
    warnings: list[Diagnostic]
    tables: list[str]
    binds: list[str]


@dataclass
class Run:
    id: str
    sql: str
    binds: dict[str, Any]
    started_at: str
    state: RunState = "running"
    finished_at: str | None = None
    message: str | None = None
    # Where the SQL check found the problem, for a rejected run.
    diagnostic: Diagnostic | None = None
    outcome: QueryOutcome | None = None
    # Stop was pressed: a second Stop only waits for the first.
    stopping: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ResultPage:
    query_id: str
    columns: list[str]
    column_types: list[str | None]
    row_count: int | None
    offset: int
    rows: list[list[str]]
    # Rows the grid can show at most; the rest are only in the CSV.
    preview_limit: int
    has_more: bool


class PlaygroundBusy(RuntimeError):
    """A query is already running in the Playground."""


class Playground:
    def __init__(
        self, settings: Settings, data: DataService, catalog: Catalog, access_log: AccessLog
    ) -> None:
        self._settings = settings
        self._data = data
        self._catalog = catalog
        self._log = access_log
        self._root = settings.data_dir / "playground"
        self._id: str | None = None
        self._runs: dict[str, Run] = {}

    # Identity and storage --------------------------------------------------

    @property
    def id(self) -> str:
        """This DataLab's Playground id, made the first time it's needed."""
        if self._id is None:
            self._id = self._load_or_make_id()
        return self._id

    @property
    def results_dir(self) -> Path:
        return self._root / self.id / "results"

    def remove_leftovers(self) -> int:
        """Remove half-written results a previous run of DataLab left behind
        (it stopped mid-query). Called at startup. Returns how many there were."""
        removed = 0
        for partial in self._root.glob("pg_*/results/*.partial"):
            with contextlib.suppress(OSError):
                partial.unlink()
                removed += 1
        return removed

    def _load_or_make_id(self) -> str:
        file = self._root / "playground.json"
        with contextlib.suppress(OSError, ValueError, AttributeError):
            stored = json.loads(file.read_text(encoding="utf-8")).get("id")
            if isinstance(stored, str) and _ID.fullmatch(stored):
                return stored
        new = f"pg_{secrets.token_hex(6)}"
        self._root.mkdir(parents=True, exist_ok=True)
        partial = file.with_name(file.name + ".partial")
        partial.write_text(json.dumps({"id": new}) + "\n", encoding="utf-8")
        partial.replace(file)
        return new

    # Checking --------------------------------------------------------------

    def check(self, sql: str) -> CheckReport:
        """The SQL check's verdict, for the editor. Nothing is run or logged."""
        if not len(self._catalog):
            # No column can be checked, so none would resolve: say why instead.
            return CheckReport([Diagnostic(self._catalog.missing_message(), "error")], [], [], [])
        try:
            checked = check_sql(
                sql, allowed_schemas=self._allowed_schemas, columns=self._catalog.column_index()
            )
        except SqlRejected as rejection:
            return CheckReport([diagnose(sql, rejection)], [], [], [])
        warnings = [
            Diagnostic(w, "warning", locate(sql, w, prefer_star=w.startswith("SELECT *")))
            for w in checked.warnings
        ]
        return CheckReport([], warnings, [str(t) for t in checked.tables], list(checked.binds))

    @property
    def _allowed_schemas(self) -> frozenset[str]:
        oracle = self._settings.oracle
        return oracle.allowed_schemas if oracle else frozenset()

    # Running ---------------------------------------------------------------

    def start(self, sql: str, binds: Mapping[str, Any]) -> Run:
        """Start a query in the background and return its run at once.

        One query runs at a time here, so the Playground can never take more
        than one of the query slots it shares with chat and workflows.
        """
        if any(r.state == "running" for r in self._runs.values()):
            raise PlaygroundBusy(
                "A query is already running here. Stop it, or wait for it to finish."
            )
        run = Run(
            id=f"pgr_{secrets.token_hex(8)}",
            sql=sql,
            binds=dict(binds),
            started_at=_now(),
        )
        self._forget_old_runs()
        self._runs[run.id] = run
        run.task = asyncio.get_running_loop().create_task(self._execute(run))
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def wait(self, run: Run, seconds: float) -> None:
        """Wait up to `seconds` for a run to finish. The run goes on either way."""
        if run.task is not None and seconds > 0:
            await asyncio.wait({run.task}, timeout=seconds)

    async def stop(self, run: Run) -> None:
        """Stop a running query: the database call is cancelled, and no result is kept."""
        if run.task is None or run.task.done():
            return
        if not run.stopping:
            run.stopping = True
            run.task.cancel()
        # The data service cancels the call in Oracle and waits for it (keeping
        # its slot until then); this only waits a while to report how it went.
        await asyncio.wait({run.task}, timeout=15)

    async def _execute(self, run: Run) -> None:
        try:
            outcome = await self._data.run_query(
                session_id=self.id,
                sql=run.sql,
                binds=run.binds,
                results_dir=self.results_dir,
                preview_rows=self._settings.playground.preview_rows,
                origin="playground",
            )
        except SqlRejected as rejection:
            self._finish(run, "rejected", str(rejection))
            run.diagnostic = diagnose(run.sql, rejection)
            return
        except QueryCancelled as error:
            self._finish(run, "stopped", str(error))
            return
        except QueryFailed as error:
            self._finish(run, "failed", str(error))
            return
        except asyncio.CancelledError:
            self._finish(run, "stopped", "The query was stopped.")
            raise
        except Exception:
            self._finish(run, "failed", "The query failed in DataLab. See the log for details.")
            raise
        run.outcome = outcome
        _write_columns(outcome)
        self._finish(run, "succeeded", None)

    def _finish(self, run: Run, state: RunState, message: str | None) -> None:
        run.state = state
        run.message = message
        run.finished_at = _now()

    def _forget_old_runs(self) -> None:
        finished = [r for r in self._runs.values() if r.state != "running"]
        for run in finished[: max(0, len(finished) - _KEEP_FINISHED + 1)]:
            del self._runs[run.id]

    # History and results ---------------------------------------------------

    def history(self) -> list[QueryRecord]:
        """This Playground's queries, newest first. Never another owner's."""
        return list(reversed(self._log.for_origin("playground", self.id)))

    def record(self, query_id: str) -> QueryRecord | None:
        if not _QUERY_ID.fullmatch(query_id):
            return None
        return next(
            (r for r in self._log.for_origin("playground", self.id) if r.id == query_id), None
        )

    def result_file(self, record: QueryRecord) -> Path | None:
        """A query's result file, if it's in this Playground's results folder."""
        if record.status != "succeeded" or not record.result_path:
            return None
        path = self.results_dir / f"{record.id}.csv"
        # The logged path must be the file named for the query here, and a
        # plain file (not a link to somewhere else).
        if Path(record.result_path) != path or path.is_symlink() or not path.is_file():
            return None
        return path

    def page(self, record: QueryRecord, offset: int, limit: int) -> ResultPage | None:
        """Rows `offset` to `offset + limit` of a result, within the preview limit."""
        path = self.result_file(record)
        if path is None:
            return None
        cap = self._settings.playground.preview_rows
        start = min(max(offset, 0), cap)
        end = min(start + max(limit, 0), cap)
        columns, rows, more = _read_rows(path, start, end)
        types = _read_types(path, len(columns))
        row_count = record.row_count
        has_more = end < cap and (more if row_count is None else end < row_count)
        return ResultPage(record.id, columns, types, row_count, start, rows, cap, has_more)


def diagnose(sql: str, rejection: SqlRejected) -> Diagnostic:
    """The SQL check's refusal, with where in the SQL it is when that can be told."""
    cause = rejection.__cause__
    position = None
    if isinstance(cause, ParseError) and cause.errors:
        position = _parse_error_position(sql, cause.errors[0])
    if position is None:
        position = locate(sql, str(rejection))
    return Diagnostic(str(rejection), "error", position)


# Words in the check's messages that are prose, not a name from the SQL.
_PROSE = frozenset(
    """
    A AN AND ANY AS BE BY CAN COLUMN DATA DATABASE DATALAB EXAMPLE FOR FROM
    FUNCTION FUNCTIONS I IN IS IT ITS NAME NAMES NO NOT OF ON ONE OR PART
    QUERY SCHEMA SQL TABLE TABLES THE THIS TO USE WITH
    """.split()  # noqa: SIM905 (a word list reads better)
)
_NAME_TOKENS = frozenset({TokenType.VAR, TokenType.IDENTIFIER})


def locate(sql: str, message: str, *, prefer_star: bool = False) -> Position | None:
    """Find the part of `sql` a check message is about.

    The message names the problem (a column, function, schema or keyword);
    of the SQL's names, the one the message mentions first is taken. Names
    in strings and comments are never matched. A statement-level problem
    ("Send exactly one statement") has no position.
    """
    try:
        tokens = sqlglot.tokenize(sql, dialect="oracle")
    except SqlglotError:
        return None
    if prefer_star:
        star = next((t for t in tokens if t.token_type == TokenType.STAR), None)
        return _span(sql, star.start, star.end + 1) if star else None
    if message.startswith("Only SELECT") and tokens:
        # Not a query: the word that starts the statement is the problem.
        return _span(sql, tokens[0].start, tokens[0].end + 1)
    upper = message.upper()
    best: tuple[int, int, int] | None = None  # (place in message, start, end)
    for i, token in enumerate(tokens):
        if token.token_type not in _NAME_TOKENS:
            continue
        name = token.text if token.token_type == TokenType.IDENTIFIER else token.text.upper()
        if name.upper() in _PROSE:
            continue
        # Prefer the dotted name as written (UTL_HTTP.REQUEST) over its parts.
        end = token.end + 1
        if (
            i + 2 < len(tokens)
            and tokens[i + 1].token_type == TokenType.DOT
            and tokens[i + 2].token_type in _NAME_TOKENS
        ):
            end = tokens[i + 2].end + 1
        found = re.search(rf"(?<![A-Z0-9_$#]){re.escape(name.upper())}(?![A-Z0-9_$#])", upper)
        if token.token_type == TokenType.IDENTIFIER:
            # A quoted name must match as written.
            found = re.search(rf"(?<![\w$#]){re.escape(name)}(?![\w$#])", message)
        if found and (best is None or found.start() < best[0]):
            best = (found.start(), token.start, end)
    if best is None:
        # "DELETE isn't allowed": a keyword that starts the message.
        first = upper.split(" ", 1)[0]
        keyword = next((t for t in tokens if t.text.upper() == first and len(first) > 2), None)
        return _span(sql, keyword.start, keyword.end + 1) if keyword else None
    return _span(sql, best[1], best[2])


def _parse_error_position(sql: str, error: Mapping[str, Any]) -> Position | None:
    """sqlglot's parse error position, which is in the SQL as the check saw it:
    stripped, and with the end of the highlighted token as its column."""
    line, column = error.get("line"), error.get("col")
    if not isinstance(line, int) or not isinstance(column, int):
        return None
    stripped = sql.strip().rstrip(";").strip()
    lines = stripped.split("\n")
    if not 1 <= line <= len(lines):
        return None
    line_start = sum(len(text) + 1 for text in lines[: line - 1])
    end = line_start + min(column, len(lines[line - 1]))
    highlight = str(error.get("highlight") or "")
    start = max(end - max(len(highlight), 1), line_start)
    lead = len(sql) - len(sql.lstrip())
    return _span(sql, lead + start, lead + end)


def _span(sql: str, start: int, end: int) -> Position:
    """Offsets into `sql` as 1-based line and column numbers."""

    def at(offset: int) -> tuple[int, int]:
        before = sql[:offset]
        return before.count("\n") + 1, offset - (before.rfind("\n") + 1) + 1

    line, column = at(start)
    end_line, end_column = at(max(end, start + 1))
    return Position(line, column, end_line, end_column)


def _columns_file(result: Path) -> Path:
    return result.with_suffix(".columns.json")


def _write_columns(outcome: QueryOutcome) -> None:
    """Keep the result's column types beside it, for when it's opened from history."""
    file = _columns_file(outcome.result_path)
    with contextlib.suppress(OSError):
        file.write_text(
            json.dumps({"columns": outcome.columns, "types": outcome.column_types}) + "\n",
            encoding="utf-8",
        )


def _read_types(result: Path, count: int) -> list[str | None]:
    types: list[Any] = []
    with contextlib.suppress(OSError, ValueError, AttributeError):
        types = json.loads(_columns_file(result).read_text(encoding="utf-8")).get("types") or []
    return [t if isinstance(t, str) else None for t in types[:count]] + [None] * (
        count - len(types[:count])
    )


def _read_rows(path: Path, start: int, end: int) -> tuple[list[str], list[list[str]], bool]:
    """The header, rows `start` to `end`, and whether there are more after them."""
    # Long text values (CLOBs) can be larger than csv's default field limit.
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        columns = next(reader, [])
        rows: list[list[str]] = []
        for number, row in enumerate(reader):
            if number >= end:
                return columns, rows, True
            if number >= start:
                rows.append([_shorten(value) for value in row])
    return columns, rows, False


def _shorten(value: str) -> str:
    return value if len(value) <= _CELL_LIMIT else value[:_CELL_LIMIT] + "…"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
