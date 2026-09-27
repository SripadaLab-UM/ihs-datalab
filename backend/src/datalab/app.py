"""Builds the DataLab web app: API, agent tools, and (later) the web UI."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from datalab import __version__, db
from datalab.api.conversations import build_conversations_router
from datalab.api.exports import build_exports_router
from datalab.api.files import Previews, build_files_router, build_preview_router
from datalab.api.inputs import build_inputs_router
from datalab.api.knowledge import KnowledgeServices, build_knowledge_router
from datalab.api.pipelines import PipelineServices, build_pipelines_router
from datalab.api.provenance import ProvenanceServices, build_provenance_router
from datalab.api.safety import build_safety_router
from datalab.api.settings import SettingsServices, build_settings_router
from datalab.api.sql import SqlServices, build_sql_router
from datalab.api.workflows import WorkflowServices, build_workflows_router
from datalab.config import Settings
from datalab.credentials import model_api_key, oracle_password
from datalab.data.access_log import AccessLog
from datalab.data.agent_tools import AgentTokenMiddleware, build_agent_tools
from datalab.data.catalog import Catalog
from datalab.data.oracle import ExtractResult, OracleDatabase, QueryFailed
from datalab.data.service import Database, DataService
from datalab.exports import DestinationStore
from datalab.relay import build_relay_router
from datalab.relay.policy import model_allowed
from datalab.safety import SafetyCheck
from datalab.safety.canary import Canaries
from datalab.sessions import helper
from datalab.sessions.approvals import Approvals
from datalab.sessions.containers import remove_all_session_containers
from datalab.sessions.helper import ResearchHelper
from datalab.sessions.inputs import AttachmentStore
from datalab.sessions.manager import SessionManager
from datalab.sessions.plans import PlanDesk, PlanStore
from datalab.sessions.store import ConversationStore
from datalab.sessions.titles import TitleWriter
from datalab.sessions.tokens import SessionTokens
from datalab.web import ApiProtection, BrowserSession, mount_web_ui

VERSION = __version__

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
    data = DataService(
        database or _LazyOracle(settings), access_log, settings.limits, allowed, catalog
    )
    tokens = SessionTokens()
    conversations = ConversationStore(connection)
    attachments = AttachmentStore(connection)
    approvals = Approvals()
    sessions = SessionManager(
        settings, conversations, tokens, attachments=attachments, approvals=approvals
    )
    services = Services(settings, data, catalog, tokens, access_log, conversations, sessions)

    research_helper = ResearchHelper(settings, tokens, approvals, conversations.append)
    research_helper.turn_running = sessions.turn_running
    sessions.helper = research_helper
    plans = PlanStore(connection)
    sessions.plans = plans
    plan_desk = PlanDesk(approvals, plans, conversations.append)
    plan_desk.turn_running = sessions.turn_running
    plan_desk.current_turn = sessions.current_turn
    plan_desk.queries_so_far = access_log.for_session
    agent_tools = build_agent_tools(data, catalog, tokens, research_helper, plan_desk)
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

    titles = TitleWriter(model_http, settings.model_base_url, model_key, settings.allowed_models)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if manage_containers:
            # Containers from a previous run that didn't shut down cleanly.
            await remove_all_session_containers(settings.profile, settings.data_dir)
            await helper.remove_leftovers(settings)
        # Turns the last run stopped in the middle of, if it didn't close cleanly.
        sessions.end_cut_off_turns()
        reaper = asyncio.create_task(sessions.reap_idle_forever())
        async with agent_tools.session_manager.run():
            yield
        reaper.cancel()
        await sessions.close_all()
        await titles.aclose()  # before the model client and database close
        await model_http.aclose()
        connection.close()

    app = FastAPI(title="DataLab", version=VERSION, lifespan=lifespan)
    app.state.services = services
    app.router.routes.extend(agent_tools_app.routes)

    def model_status(session_id: str, data: dict) -> None:
        # Conversations show how their model requests are going; helpers and
        # safety-check sessions only log it.
        if conversations.get(session_id) is not None:
            conversations.append(session_id, "model_status", data)

    app.include_router(
        build_relay_router(
            tokens,
            model_key,
            settings.model_base_url,
            model_http,
            settings.allowed_models,
            on_status=model_status,
            watch_turn=sessions.watch_turn,
        )
    )

    listing: dict[str, Any] = {}

    async def approved_models() -> list[str]:
        """Approved models U-M GPT offers, asked at most every five minutes."""
        if listing and time.monotonic() - listing["at"] < 300:
            return listing["models"]
        try:
            # The keychain can show a prompt: never on the event loop.
            key = await asyncio.to_thread(model_key)
            response = await model_http.get(
                f"{settings.model_base_url.rstrip('/')}/models",
                headers={"authorization": f"Bearer {key}"},
                timeout=15,
            )
            listed = [m.get("id") for m in response.json().get("data", [])]
        except Exception:  # no key yet, no VPN, U-M down: the picker shows the default
            return []
        models = sorted(m for m in listed if model_allowed(m, settings.allowed_models))
        listing.update(at=time.monotonic(), models=models)
        return models

    app.include_router(
        build_conversations_router(
            conversations,
            sessions,
            settings.default_model,
            access_log,
            models=approved_models,
            allowed_models=settings.allowed_models,
            titles=titles,
        )
    )
    previews = Previews()
    app.state.previews = previews
    app.include_router(build_files_router(conversations, sessions, previews))
    app.include_router(build_preview_router(previews))
    app.include_router(build_inputs_router(settings, conversations, attachments, sessions))
    app.include_router(
        build_exports_router(
            settings, conversations, DestinationStore(connection), sessions, attachments, access_log
        )
    )
    canaries = Canaries()
    app.include_router(canaries.router())
    safety = SafetyCheck(settings, tokens, canaries, model_key=model_key, previews=previews)
    app.state.canaries = canaries
    app.state.safety = safety
    app.include_router(build_safety_router(safety, settings.data_dir / "logs" / "safety-last.json"))
    # The areas still being built. Each gets what it needs here, once, and
    # otherwise changes only its own module (and registers any session hooks
    # or mounts from there).
    app.include_router(build_sql_router(SqlServices(settings, data, catalog, access_log)))
    app.include_router(
        build_knowledge_router(KnowledgeServices(settings, connection, conversations, sessions))
    )
    app.include_router(
        build_workflows_router(WorkflowServices(settings, connection, data, access_log))
    )
    app.include_router(build_pipelines_router(PipelineServices(settings, conversations, sessions)))
    app.include_router(
        build_provenance_router(ProvenanceServices(conversations, sessions, access_log))
    )
    app.include_router(build_settings_router(SettingsServices(settings, connection)))
    app.add_middleware(AgentTokenMiddleware, tokens=tokens)
    browser = browser or BrowserSession(settings.port)
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
