"""The `ihs-data` tools that agents use, served over MCP at /mcp.

Only data sessions can use them. A request's session token decides which
session it belongs to, so results land only in that session's workspace.
Research sessions have no route here at all (their gateway doesn't forward to
/mcp); the token check refuses them anyway, as a second layer.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from datalab.data.catalog import Catalog, TableInfo
from datalab.data.oracle import QueryFailed
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from datalab.sessions.tokens import SessionAccess, SessionTokens, bearer_token

INSTRUCTIONS = """\
Read-only access to the Intern Health Study (IHS) Oracle database, one schema
per cohort year (for example IHS_2024, IHS_2025). Start with search_catalog,
check columns with describe_table, then run SELECT queries with query. Qualify
every table with its schema. Results are saved as CSV files in /data/oracle;
work with the file for anything beyond the preview.
"""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def build_agent_tools(service: DataService, catalog: Catalog, tokens: SessionTokens) -> MCPServer:
    server = MCPServer(name="ihs-data", instructions=INSTRUCTIONS)

    @server.tool(annotations=_READ_ONLY)
    def search_catalog(query: str, cohorts: list[str] | None = None, limit: int = 15) -> str:
        """Find tables and views by name, comment, or column. Metadata only.

        query: words to look for, e.g. "fitbit sleep" or "phq9".
        cohorts: optional schema names to search, e.g. ["IHS_2025"].
        """
        hits = catalog.search(query, limit=max(1, min(limit, 50)), schemas=cohorts)
        return _json(
            [
                {
                    "table": hit.table.qualified_name,
                    "type": hit.table.type,
                    "comment": hit.table.comment,
                    "matching_columns": hit.matching_columns,
                    "also_in": [
                        c for c in catalog.cohorts_with(hit.table.name) if c != hit.table.schema
                    ],
                }
                for hit in hits
            ]
        )

    @server.tool(annotations=_READ_ONLY)
    def describe_table(table: str) -> str:
        """Columns, types, comments, and primary key of one table.

        table: schema-qualified name, e.g. "IHS_2025.VFITBITDAILYDATA".
        """
        info = catalog.get(table)
        if info is None:
            raise ToolError(f"{table} isn't in the catalog. Use search_catalog to find tables.")
        return _json(_describe(info, catalog))

    @server.tool(annotations=_READ_ONLY)
    async def query(
        sql: str, ctx: Context, binds: dict[str, Any] | None = None, preview_rows: int = 20
    ) -> str:
        """Run one read-only SELECT. The full result is saved as a CSV file.

        sql: a single SELECT (WITH is fine). Use bind variables like :start_date.
        binds: values for the bind variables, e.g. {"start_date": "2025-04-01"}.
        preview_rows: how many rows to return inline (the file has them all).
        """
        access = _session(ctx, tokens)
        try:
            outcome = await service.run_query(
                session_id=access.session_id,
                sql=sql,
                binds=binds,
                results_dir=access.results_dir,
                preview_rows=max(0, min(preview_rows, 200)),
            )
        except (SqlRejected, QueryFailed) as error:
            raise ToolError(str(error)) from error
        return _json(
            {
                "query_id": outcome.query_id,
                "row_count": outcome.row_count,
                "result_file": f"{access.results_path_in_container}/{outcome.result_path.name}",
                "columns": outcome.columns,
                "preview": outcome.preview,
                "warnings": outcome.warnings,
                "tables": outcome.tables,
            }
        )

    return server


class AgentTokenMiddleware:
    """Refuse /mcp requests that don't carry a live data-session token."""

    def __init__(self, app: ASGIApp, tokens: SessionTokens, prefix: str = "/mcp") -> None:
        self._app = app
        self._tokens = tokens
        self._prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self._prefix):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            access = self._tokens.resolve(bearer_token(headers.get("authorization")))
            if access is None or access.kind != "data":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


def _session(ctx: Context, tokens: SessionTokens) -> SessionAccess:
    request = ctx.request_context.request
    headers = getattr(request, "headers", None) or {}
    access = tokens.resolve(bearer_token(headers.get("authorization")))
    if access is None or access.kind != "data":
        raise ToolError("This session isn't allowed to query data.")
    return access


def _describe(info: TableInfo, catalog: Catalog) -> dict[str, Any]:
    return {
        "table": info.qualified_name,
        "type": info.type,
        "comment": info.comment,
        "primary_key": info.primary_key,
        "also_in": [c for c in catalog.cohorts_with(info.name) if c != info.schema],
        "columns": [
            {"name": c.name, "type": c.type, "nullable": c.nullable, "comment": c.comment}
            for c in info.columns
        ],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)
