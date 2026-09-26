"""The ihs-data tools, exercised over real HTTP with a real MCP client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from datalab.app import create_app
from datalab.sessions.tokens import SessionAccess
from tests.conftest import FakeDatabase, live_server


@pytest.fixture
def server(settings, catalog) -> Iterator[tuple[str, object, FakeDatabase]]:
    database = FakeDatabase()
    app = create_app(settings, database=database, catalog=catalog, manage_containers=False)
    with live_server(app) as base_url:
        yield base_url, app.state.services, database


@asynccontextmanager
async def mcp_session(base_url: str, token: str):
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with client, streamable_http_client(f"{base_url}/mcp", http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def data_token(services, tmp_path: Path, kind: str = "data") -> str:
    access = SessionAccess(session_id="sess1", kind=kind, results_dir=tmp_path / "oracle")  # type: ignore[arg-type]
    return services.tokens.issue(access)


def payload(result) -> object:
    assert not result.is_error, result
    return json.loads(result.content[0].text)


async def test_tools_are_listed(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
    assert tools == {"search_catalog", "describe_table", "query"}


async def test_search_and_describe(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        hits = payload(await session.call_tool("search_catalog", {"query": "mood"}))
        described = payload(
            await session.call_tool("describe_table", {"table": "IHS_2025.VFITBITDAILYDATA"})
        )
    assert hits[0]["table"] == "IHS_2025.VW_DAILY_MOOD"
    assert described["also_in"] == ["IHS_2024"]
    assert described["columns"][2]["name"] == "TRACKERSTEPS"


async def test_query_writes_result_to_the_session_folder(server, tmp_path):
    base_url, services, database = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = payload(
            await session.call_tool(
                "query",
                {"sql": "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA"},
            )
        )
    assert result["row_count"] == 2
    assert result["result_file"].startswith("/data/oracle/q_")
    assert (tmp_path / "oracle" / Path(result["result_file"]).name).exists()
    assert services.access_log.for_session("sess1")[0].status == "succeeded"
    assert len(database.calls) == 1


async def test_rejected_sql_is_a_tool_error(server, tmp_path):
    base_url, services, database = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("query", {"sql": "DELETE FROM IHS_2025.T"})
    assert result.is_error
    assert "Only SELECT" in result.content[0].text
    assert database.calls == []


def test_requests_without_a_data_session_token_are_refused(server, tmp_path):
    base_url, services, _ = server
    research = data_token(services, tmp_path, kind="research")
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {research}"}):
        response = httpx.post(f"{base_url}/mcp", headers=headers, json={})
        assert response.status_code == 401


def test_revoked_tokens_stop_working(server, tmp_path):
    base_url, services, _ = server
    token = data_token(services, tmp_path)
    services.tokens.revoke_session("sess1")
    response = httpx.post(f"{base_url}/mcp", headers={"Authorization": f"Bearer {token}"}, json={})
    assert response.status_code == 401


def test_health(server):
    base_url, _, _ = server
    body = httpx.get(f"{base_url}/api/health").json()
    assert body["status"] == "ok" and body["catalog_tables"] == 3
