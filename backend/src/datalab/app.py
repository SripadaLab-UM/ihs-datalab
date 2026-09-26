"""Builds the DataLab web app: API, agent tools, and (later) the web UI."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from datalab import db
from datalab.api.conversations import build_conversations_router
from datalab.api.safety import build_safety_router
from datalab.config import Settings
from datalab.credentials import model_api_key, oracle_password
from datalab.data.access_log import AccessLog
from datalab.data.agent_tools import AgentTokenMiddleware, build_agent_tools
from datalab.data.catalog import Catalog
from datalab.data.oracle import ExtractResult, OracleDatabase, QueryFailed
from datalab.data.service import Database, DataService
from datalab.relay import build_relay_router
from datalab.safety import SafetyCheck
from datalab.safety.canary import Canaries
from datalab.sessions.containers import remove_all_session_containers
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.tokens import SessionTokens
from datalab.web import ApiProtection, BrowserSession, mount_web_ui

VERSION = "0.1.0"

# Hosts the agent-tools endpoint answers to: the local browser, and the
# gateway container reaching the host.
_MCP_HOSTS = ["127.0.0.1:*", "localhost:*", "host.docker.internal:*"]


@dataclass
class Services:
    settings: Settings
    data: DataService
    catalog: Catalog
    tokens: SessionTokens
    access_log: AccessLog
    conversations: ConversationStore
    sessions: SessionManager


def create_app(
    settings: Settings,
    *,
    database: Database | None = None,
    catalog: Catalog | None = None,
    model_client: httpx.AsyncClient | None = None,
    model_key: Callable[[], str] = model_api_key,
    manage_containers: bool = True,
    browser: BrowserSession | None = None,
    protect_api: bool = True,
    web_dist: Path | None = None,
) -> FastAPI:
    connection = db.connect(settings.database_file)
    access_log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    if catalog is None:
        catalog = Catalog.load(settings.catalog_dir) if settings.catalog_dir else Catalog([])
    allowed = settings.oracle.allowed_schemas if settings.oracle else frozenset()
    data = DataService(database or _LazyOracle(settings), access_log, settings.limits, allowed)
    tokens = SessionTokens()
    conversations = ConversationStore(connection)
    sessions = SessionManager(settings, conversations, tokens)
    services = Services(settings, data, catalog, tokens, access_log, conversations, sessions)

    agent_tools = build_agent_tools(data, catalog, tokens)
    agent_tools_app = agent_tools.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=_MCP_HOSTS
        ),
    )

    # Long read timeout: model responses stream for as long as a turn runs.
    model_http = model_client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15, read=900, write=60, pool=15)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if manage_containers:
            # Containers from a previous run that didn't shut down cleanly.
            await remove_all_session_containers(settings.profile)
        reaper = asyncio.create_task(sessions.reap_idle_forever())
        async with agent_tools.session_manager.run():
            yield
        reaper.cancel()
        await sessions.close_all()
        await model_http.aclose()
        connection.close()

    app = FastAPI(title="DataLab", version=VERSION, lifespan=lifespan)
    app.state.services = services
    app.router.routes.extend(agent_tools_app.routes)
    app.include_router(build_relay_router(tokens, model_key, settings.model_base_url, model_http))
    app.include_router(
        build_conversations_router(conversations, sessions, settings.default_model, access_log)
    )
    canaries = Canaries()
    app.include_router(canaries.router())
    safety = SafetyCheck(settings, tokens, canaries, model_key=model_key)
    app.state.canaries = canaries
    app.state.safety = safety
    app.include_router(build_safety_router(safety, settings.data_dir / "logs" / "safety-last.json"))
    app.add_middleware(AgentTokenMiddleware, tokens=tokens)
    browser = browser or BrowserSession()
    app.state.browser = browser
    app.add_middleware(ApiProtection, session=browser, enforce=protect_api)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": VERSION,
            "profile": settings.profile,
            "database_configured": settings.oracle is not None,
            "catalog_tables": len(catalog),
            "catalog_schemas": catalog.schemas,
        }

    # Last, so the web UI's catch-all route never shadows the API.
    mount_web_ui(app, browser, web_dist)
    return app


class _LazyOracle:
    """Connects on first use, so DataLab starts even before a password is saved."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database: OracleDatabase | None = None
        self._lock = threading.Lock()

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
        return self._get().extract_to_csv(
            sql,
            binds,
            out_path,
            max_rows=max_rows,
            max_bytes=max_bytes,
            preview_rows=preview_rows,
            cancel=cancel,
        )

    def _get(self) -> OracleDatabase:
        with self._lock:
            if self._database is None:
                oracle = self._settings.oracle
                if oracle is None:
                    raise QueryFailed(
                        "No database is configured. Set it up in Settings → Connections."
                    )
                self._database = OracleDatabase(
                    oracle, oracle_password(oracle), self._settings.limits
                )
            return self._database
