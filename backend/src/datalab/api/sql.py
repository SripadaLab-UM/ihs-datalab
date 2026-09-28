"""SQL Playground (milestone 4): run SQL by hand, through the data service.

Playground queries go through `DataService.run_query` with
`origin="playground"` and the Playground's `pg_…` owner id, so they get the
same SQL check, limits and logging as the agent's, and list with
`AccessLog.for_origin("playground", …)`. See `datalab/playground.py`.

- `POST /check`: the SQL check's errors and warnings, with where they are.
- `POST /runs`: start a query in the background; `GET /runs/{id}` follows
  it (optionally waiting), and `POST /runs/{id}/stop` stops it.
- `GET /results/{query_id}`: a page of a result, within the preview limit.
- `GET /history`: this Playground's queries, newest first.
- `GET /catalog` and `GET /catalog/search`: the table browser.
- `POST /results/{query_id}/export`: export a result, with a manifest.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from datalab import exports
from datalab.api.exports import ExportOut, export_out, export_target
from datalab.config import Settings
from datalab.data.access_log import AccessLog, QueryRecord
from datalab.data.catalog import Catalog, TableInfo
from datalab.data.service import DataService
from datalab.data.sqlcheck import MAX_SQL_BYTES
from datalab.exports import DestinationStore, ExportError, ExportSource
from datalab.playground import Diagnostic, Playground, PlaygroundBusy, Run
from datalab.sessions.checkpoints import UnsafePath, open_workspace_file


@dataclass(frozen=True)
class SqlServices:
    """What the Playground's routes use, given by the app (app.py)."""

    settings: Settings  # `settings.playground`; results go under `settings.data_dir`
    data: DataService
    catalog: Catalog  # the table browser
    access_log: AccessLog  # the Playground's query history
    destinations: DestinationStore  # where results can be exported to


class SqlStatus(BaseModel):
    available: bool
    database_configured: bool
    playground_id: str
    # Rows the results grid shows at most; the whole result is in its CSV.
    preview_rows: int
    max_rows: int
    max_bytes: int


class SqlIn(BaseModel):
    # A little over the check's own limit, so the check can say what's wrong.
    sql: str = Field(max_length=2 * MAX_SQL_BYTES)


BindValue = str | int | float | None


class RunIn(SqlIn):
    binds: dict[str, BindValue] = Field(default_factory=dict, max_length=100)


class PositionOut(BaseModel):
    """1-based; the end is just after the last character."""

    line: int
    column: int
    end_line: int
    end_column: int


class DiagnosticOut(BaseModel):
    message: str
    severity: Literal["error", "warning"]
    position: PositionOut | None


class CheckOut(BaseModel):
    ok: bool
    errors: list[DiagnosticOut]
    warnings: list[DiagnosticOut]
    tables: list[str]
    binds: list[str]


class ColumnOut(BaseModel):
    name: str
    type: str | None


class RunOut(BaseModel):
    id: str
    state: Literal["running", "succeeded", "failed", "rejected", "stopped"]
    sql: str
    started_at: str
    finished_at: str | None
    message: str | None
    # Where the SQL check found the problem, for a rejected run.
    diagnostic: DiagnosticOut | None
    # Set when it succeeded: the result's id, for /results and export.
    query_id: str | None
    row_count: int | None
    bytes_written: int | None
    elapsed_seconds: float | None
    columns: list[ColumnOut]
    # The tables it read, as the check found them.
    tables: list[str]
    warnings: list[str]


class ResultPageOut(BaseModel):
    query_id: str
    columns: list[ColumnOut]
    row_count: int | None
    offset: int
    rows: list[list[str]]
    # Rows that can be shown here at most; the rest are only in the file.
    preview_limit: int
    has_more: bool


class HistoryItem(BaseModel):
    query_id: str
    status: str
    sql: str
    binds: dict[str, object]
    tables: list[str]
    started_at: str
    finished_at: str | None
    row_count: int | None
    elapsed_ms: int | None
    message: str | None
    has_result: bool


class CatalogColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    comment: str


class CatalogTable(BaseModel):
    name: str
    type: str
    comment: str
    primary_key: list[str]
    columns: list[CatalogColumn]


class CatalogCohort(BaseModel):
    schema_name: str
    tables: list[CatalogTable]


class CatalogHit(BaseModel):
    schema_name: str
    name: str
    type: str
    comment: str
    matching_columns: list[str]


class ExportIn(BaseModel):
    destination_id: str


def build_sql_router(services: SqlServices) -> APIRouter:
    router = APIRouter(prefix="/api/sql", tags=["sql"])
    settings = services.settings
    catalog = services.catalog
    playground = Playground(settings, services.data, catalog, services.access_log)
    # Made when DataLab starts: nothing half-written from a previous run stays.
    playground.remove_leftovers()

    def find_run(run_id: str) -> Run:
        run = playground.get(run_id)
        if run is None:
            raise HTTPException(404, "No such run. Runs are forgotten when DataLab restarts.")
        return run

    def find_record(query_id: str) -> QueryRecord:
        record = playground.record(query_id)
        if record is None:
            raise HTTPException(404, "No such Playground query.")
        return record

    @router.get("/status")
    def status() -> SqlStatus:
        return SqlStatus(
            available=True,
            database_configured=settings.oracle is not None,
            playground_id=playground.id,
            preview_rows=settings.playground.preview_rows,
            max_rows=settings.limits.max_rows,
            max_bytes=settings.limits.max_bytes,
        )

    @router.post("/check")
    def check(body: SqlIn) -> CheckOut:
        """The SQL check's verdict, for the editor. Nothing is run or logged."""
        report = playground.check(body.sql)
        return CheckOut(
            ok=not report.errors,
            errors=[_diagnostic(d) for d in report.errors],
            warnings=[_diagnostic(d) for d in report.warnings],
            tables=report.tables,
            binds=report.binds,
        )

    @router.post("/runs", status_code=202)
    async def start_run(body: RunIn) -> RunOut:
        """Start a query. It runs in the background; follow it with GET /runs/{id}."""
        try:
            run = playground.start(body.sql, body.binds)
        except PlaygroundBusy as error:
            raise HTTPException(409, str(error)) from error
        # One turn of the event loop, so a query the check refuses says so at once.
        await asyncio.sleep(0)
        return _run_out(run)

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str, wait: float = Query(default=0, ge=0, le=30)) -> RunOut:
        """A run's state. With `wait`, answers when it finishes or after that many seconds."""
        run = find_run(run_id)
        await playground.wait(run, wait)
        return _run_out(run)

    @router.post("/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> RunOut:
        """Stop a query: it's cancelled in the database, and no result is kept."""
        run = find_run(run_id)
        if run.state != "running":
            raise HTTPException(409, "That query has already finished.")
        await playground.stop(run)
        return _run_out(run)

    @router.get("/results/{query_id}")
    def result(
        query_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ResultPageOut:
        """A page of a result. Only its first `preview_rows` rows can be shown;
        the whole result is in its file, and can be exported."""
        page = playground.page(find_record(query_id), offset, limit)
        if page is None:
            raise HTTPException(404, "That query has no result to show.")
        return ResultPageOut(
            query_id=page.query_id,
            columns=_columns(page.columns, page.column_types),
            row_count=page.row_count,
            offset=page.offset,
            rows=page.rows,
            preview_limit=page.preview_limit,
            has_more=page.has_more,
        )

    @router.get("/history")
    def history(limit: int = Query(default=100, ge=1, le=1000)) -> list[HistoryItem]:
        """This Playground's own queries, newest first."""
        return [
            HistoryItem(
                query_id=r.id,
                status=r.status,
                sql=r.sql_text,
                binds=r.binds,
                tables=r.tables,
                started_at=r.started_at,
                finished_at=r.finished_at,
                row_count=r.row_count,
                elapsed_ms=r.elapsed_ms,
                message=r.message,
                has_result=playground.result_file(r) is not None,
            )
            for r in playground.history()[:limit]
        ]

    @router.get("/catalog")
    async def browse() -> list[CatalogCohort]:
        """Every cohort's tables and columns, with their comments. Metadata only."""
        await services.data.ensure_catalog()
        cohorts = []
        for schema in catalog.schemas:
            tables = [catalog.get(f"{schema}.{name}") for name in catalog.names(schema)]
            cohorts.append(
                CatalogCohort(schema_name=schema, tables=[_table(t) for t in tables if t])
            )
        return cohorts

    @router.get("/catalog/search")
    async def search(
        q: str = Query(max_length=200),
        schemas: Annotated[list[str] | None, Query()] = None,
        limit: int = Query(default=30, ge=1, le=100),
    ) -> list[CatalogHit]:
        """Tables whose names, columns or comments match, best first."""
        await services.data.ensure_catalog()
        return [
            CatalogHit(
                schema_name=hit.table.schema,
                name=hit.table.name,
                type=hit.table.type,
                comment=hit.table.comment,
                matching_columns=hit.matching_columns,
            )
            for hit in catalog.search(q, limit=limit, schemas=schemas)
        ]

    @router.post("/results/{query_id}/export", status_code=201)
    async def export(query_id: str, body: ExportIn) -> ExportOut:
        """Export a result's file to an export folder, with a manifest saying what it is."""
        record = find_record(query_id)
        path = playground.result_file(record)
        if path is None:
            raise HTTPException(404, "That query has no result to export.")
        target = export_target(settings, services.destinations, body.destination_id)
        folder = target.path
        practice = settings.profile == "practice"
        header = playground.page(record, 0, 0)
        columns = _columns(header.columns, header.column_types) if header else []
        about = {
            "profile": settings.profile,
            "contains_study_data": not practice,
            "playground": {"id": playground.id},
            "query": {
                "id": record.id,
                "sql": record.sql_text,
                "binds": record.binds,
                "tables": record.tables,
                "ran_at": record.started_at,
                "row_count": record.row_count,
                "columns": [c.model_dump() for c in columns],
            },
        }
        results = playground.results_dir
        source = ExportSource(
            open=lambda: open_workspace_file(results, path.name),
            path=f"query-results/{path.name}",
            container_path=f"playground/results/{path.name}",
        )

        def run() -> exports.ExportResult:
            return exports.export(
                folder, title="SQL Playground", tag=record.id, sources=[source], about=about
            )

        try:
            done = await asyncio.to_thread(run)
        except (ExportError, UnsafePath, OSError) as error:
            raise HTTPException(422, f"The export didn't finish: {error}") from error
        services.access_log.record_export(
            session_id=playground.id,
            destination=str(folder),
            files=len(done.files),
            contains_study_data=not practice,
            query_id=record.id,
        )
        return export_out(target, done)

    return router


def _columns(names: list[str], types: list[str] | list[str | None]) -> list[ColumnOut]:
    padded = [*types, *[None] * len(names)][: len(names)]
    return [
        ColumnOut(name=name, type=kind or None) for name, kind in zip(names, padded, strict=True)
    ]


def _diagnostic(diagnostic: Diagnostic) -> DiagnosticOut:
    p = diagnostic.position
    position = (
        PositionOut(line=p.line, column=p.column, end_line=p.end_line, end_column=p.end_column)
        if p
        else None
    )
    return DiagnosticOut(
        message=diagnostic.message, severity=diagnostic.severity, position=position
    )


def _run_out(run: Run) -> RunOut:
    outcome = run.outcome
    return RunOut(
        id=run.id,
        state=run.state,
        sql=run.sql,
        started_at=run.started_at,
        finished_at=run.finished_at,
        message=run.message,
        diagnostic=_diagnostic(run.diagnostic) if run.diagnostic else None,
        query_id=outcome.query_id if outcome else None,
        row_count=outcome.row_count if outcome else None,
        bytes_written=outcome.bytes_written if outcome else None,
        elapsed_seconds=outcome.elapsed_seconds if outcome else None,
        columns=_columns(outcome.columns, outcome.column_types) if outcome else [],
        tables=list(outcome.tables) if outcome else [],
        warnings=list(outcome.warnings) if outcome else [],
    )


def _table(table: TableInfo) -> CatalogTable:
    return CatalogTable(
        name=table.name,
        type=table.type,
        comment=table.comment,
        primary_key=list(table.primary_key),
        columns=[
            CatalogColumn(name=c.name, type=c.type, nullable=c.nullable, comment=c.comment)
            for c in table.columns
        ],
    )
