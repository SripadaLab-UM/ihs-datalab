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
from pydantic import BaseModel

from datalab import __version__, datalock, db, updates
from datalab.api.conversations import build_conversations_router
from datalab.api.exports import build_exports_router
from datalab.api.files import Previews, build_files_router, build_preview_router
from datalab.api.github import GitHubServices, build_github_router
from datalab.api.inputs import build_inputs_router
from datalab.api.knowledge import KnowledgeServices, build_knowledge_router
from datalab.api.pipelines import PipelineServices, build_pipelines_router
from datalab.api.provenance import ProvenanceServices, build_provenance_router
from datalab.api.safety import build_safety_router
from datalab.api.settings import SettingsServices, build_settings_router
from datalab.api.sql import SqlServices, build_sql_router
from datalab.api.textguard import RefuseNonText
from datalab.api.workflows import WorkflowServices, build_workflows_router
from datalab.config import Settings
from datalab.credentials import model_api_key, oracle_password
from datalab.data.access_log import AccessLog
from datalab.data.agent_tools import AgentTokenMiddleware, build_agent_tools
from datalab.data.autocatalog import CatalogAutoBuild, CatalogState
from datalab.data.catalog import Catalog
from datalab.data.oracle import ExtractResult, OracleDatabase, QueryFailed
from datalab.data.service import Database, DataService
from datalab.data.sql_drafts import SqlDrafts
from datalab.exports import DestinationStore
from datalab.relay import build_relay_router
from datalab.relay.policy import model_allowed
from datalab.releases import UpdateChecker
from datalab.repos.github import GitHubAuth
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
from datalab.update_gate import UpdateGate, UpdateGateMiddleware
from datalab.updater import Updater, busy_reason
from datalab.web import ApiProtection, BrowserSession, add_session_routes, mount_web_ui

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
    recovery: updates.Recovery | None = None,
) -> FastAPI:
    require_data_folder_lock(settings)
    connection = db.connect(settings.database_file)
    access_log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    loads_catalog = catalog is None
    if catalog is None:
        catalog = Catalog.load(catalog_folder(settings))
    allowed = settings.oracle.allowed_schemas if settings.oracle else frozenset()
    # Connects on first use (and again after a new password is saved).
    lazy: _LazyOracle | None = None
    if database is None:
        database = lazy = _LazyOracle(settings)
    # Practice DataLab's own catalog is built the first time it reaches the database.
    catalog_build = (
        catalog_auto_build(
            settings, catalog, lazy.build_catalog, on_built=access_log.record_catalog_build
        )
        if lazy is not None and loads_catalog
        else None
    )
    data = DataService(
        database, access_log, settings.limits, allowed, catalog, catalog_build=catalog_build
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
    # The SQL Playground chat's proposed queries (propose_sql), checked as the editor checks.
    sql_drafts = SqlDrafts(
        conversations,
        catalog,
        lambda: settings.oracle.allowed_schemas if settings.oracle else frozenset(),
        access_log,
    )
    sql_drafts.turn_running = sessions.turn_running
    # One GitHub sign-in for both lab repos: only one object may refresh its
    # tokens, since each refresh replaces the refresh token.
    github = GitHubAuth(settings.repos.client_id) if settings.repos.client_id else None
    # Built here, before the agent tools, whose check_workflow uses the
    # workflows' check (they're included below, in their place).
    pipelines = build_pipelines_router(
        PipelineServices(settings, connection, conversations, sessions, auth=github)
    )
    workflows_router = build_workflows_router(
        WorkflowServices(
            settings,
            connection,
            data,
            access_log,
            catalog=catalog,
            # New workflow files are shared with the pipelines repo's Save & share.
            pipelines=pipelines.pipelines,  # type: ignore[attr-defined]
        )
    )
    agent_tools = build_agent_tools(
        data,
        catalog,
        tokens,
        research_helper,
        plan_desk,
        check_workflow_text=workflows_router.runner.check_text,  # type: ignore[attr-defined]
        drafts=sql_drafts,
    )
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
        # And the queries it was running, for chat, the Playground and workflows.
        access_log.end_cut_off_queries()
        # DataLab's own catalog, if it has none yet. In the background: the
        # database may not be up yet (then it's tried again at the first query).
        building = (
            asyncio.create_task(asyncio.to_thread(catalog_build.ensure))
            if catalog_build is not None
            else None
        )
        reaper = asyncio.create_task(sessions.reap_idle_forever())
        async with agent_tools.session_manager.run():
            yield
        reaper.cancel()
        if building is not None:
            building.cancel()
        await sessions.close_all()
        await titles.aclose()  # before the model client and database close
        await model_http.aclose()
        connection.close()

    app = FastAPI(title="DataLab", version=VERSION, lifespan=lifespan)
    app.state.services = services
    app.state.sql_drafts = sql_drafts
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
    destinations = DestinationStore(connection)
    app.include_router(
        build_exports_router(
            settings, conversations, destinations, sessions, attachments, access_log
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
    app.include_router(
        build_sql_router(
            SqlServices(settings, data, catalog, access_log, destinations, drafts=sql_drafts)
        )
    )
    app.include_router(build_github_router(GitHubServices(settings, github)))
    app.include_router(
        build_knowledge_router(
            KnowledgeServices(settings, connection, conversations, sessions, auth=github)
        )
    )
    app.include_router(workflows_router)
    app.include_router(pipelines)
    app.include_router(
        build_provenance_router(ProvenanceServices(conversations, sessions, access_log))
    )
    # Checking GitHub for a newer release happens here in the host process
    # only (`datalab serve` asks once at start, if `updates.check_on_start`).
    update_checker = UpdateChecker(settings)
    app.state.update_checker = update_checker

    def request_shutdown() -> bool:
        # `datalab serve` sets this; without it (tests), nothing restarts.
        shutdown = getattr(app.state, "shutdown", None)
        if shutdown is None:
            return False
        shutdown()
        return True

    # Closed by the updater once it starts changing things: from then on
    # nothing new may start (update_gate.py).
    gate = UpdateGate()
    updater = Updater(
        settings,
        update_checker,
        busy=lambda: busy_reason(connection, sessions.any_busy, gate.in_flight),
        stop_sessions=sessions.close_all,
        shutdown=request_shutdown,
        gate=gate,
    )
    app.include_router(
        build_settings_router(
            SettingsServices(
                settings,
                connection,
                turn_running=sessions.is_busy,
                model_http=model_http,
                model_key=model_key,
                password_changed=lambda: _password_changed(lazy, catalog_build),
                recovery=recovery,
                checker=update_checker,
                updater=updater,
            )
        )
    )
    app.add_middleware(RefuseNonText)
    app.add_middleware(AgentTokenMiddleware, tokens=tokens)
    app.add_middleware(UpdateGateMiddleware, gate=gate)
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
            # Coarse on purpose: health is the one route that needs no sign-in.
            # The details (they can name the database user) are at /api/catalog/status.
            "catalog_state": catalog_state(catalog, catalog_build),
        }

    @app.get("/api/catalog/status")
    def catalog_status() -> CatalogStatusOut:
        """Why the catalog is empty and what happens next (signed in only)."""
        return CatalogStatusOut(
            state=catalog_state(catalog, catalog_build),
            tables=len(catalog),
            detail=catalog_problem(settings, catalog, catalog_build),
        )

    add_session_routes(app, browser)
    # Last, so the web UI's catch-all route never shadows the API.
    mount_web_ui(app, browser, web_dist)
    return app


def catalog_folder(settings: Settings) -> Path:
    """The lab's catalog folder (the knowledge base's generated/schema), or
    else DataLab's own in the data folder."""
    return settings.catalog_dir or settings.data_dir / "catalog"


def catalog_auto_build(
    settings: Settings,
    catalog: Catalog,
    build: Callable[[], Catalog],
    *,
    on_built: Callable[[Catalog], None] | None = None,
) -> CatalogAutoBuild | None:
    """Building the catalog from the database: practice DataLab's own folder
    only, for now. A folder the lab's settings name is never written."""
    if settings.profile != "practice" or settings.catalog_dir is not None:
        return None
    if settings.oracle is None:
        return None
    return CatalogAutoBuild(catalog, catalog_folder(settings), build, on_built=on_built)


class CatalogStatusOut(BaseModel):
    state: CatalogState
    tables: int
    # Why it's empty and what happens next; None once it has tables.
    detail: str | None


def catalog_state(catalog: Catalog, build: CatalogAutoBuild | None) -> CatalogState:
    if build is not None:
        return build.state
    return "ready" if len(catalog) else "empty"


def catalog_problem(
    settings: Settings, catalog: Catalog, build: CatalogAutoBuild | None
) -> str | None:
    """Why the catalog is empty, and what happens next, for Settings (signed in)."""
    if len(catalog):
        return None
    if build is not None:
        if build.stopped:
            return (
                "DataLab stopped trying to build it, since trying again wouldn't help: "
                f"{build.problem} Fix that (for a password: datalab --profile practice "
                "setup --update), then start DataLab again."
            )
        if build.problem:
            return (
                "DataLab builds it (metadata only) once it can connect to the database; "
                f"it tries again at the next query, at most every 30 s. Last try: {build.problem}"
            )
        return "DataLab builds it (metadata only) the first time it connects to the database."
    if settings.oracle is None:
        return "No database is set up yet."
    if settings.catalog_dir is not None:
        return (
            f"The catalog folder in DataLab's settings ({settings.catalog_dir}) has no tables. "
            "Build it with: datalab catalog --from-database --out <folder>"
        )
    return (
        "Build it with: datalab catalog --from-database --out <folder>, then name that "
        "folder as catalog_dir in DataLab's settings.toml."
    )


def _password_changed(lazy: _LazyOracle | None, build: CatalogAutoBuild | None) -> None:
    if lazy is not None:
        lazy.reset()
    if build is not None:
        build.try_again_soon()


class DataFolderNotLocked(RuntimeError):
    """create_app was called without holding the data folder's lock."""


def require_data_folder_lock(settings: Settings) -> None:
    """Refuse to build DataLab on a data folder this process hasn't locked.

    At startup DataLab ends the turns and queries the last run left going
    and removes its containers and half-written results: with a second
    DataLab on the same folder, that would be done to one still working. So
    every caller takes the lock first (`datalock.refuse_second_instance`).
    """
    if not datalock.held(settings.data_dir):
        raise DataFolderNotLocked(
            f"DataLab needs the lock on its data folder ({settings.data_dir}) before it "
            "starts: call datalock.refuse_second_instance first."
        )


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

    def build_catalog(self) -> Catalog:
        """The catalog, read from the database's catalog views: metadata only."""
        oracle = self._settings.oracle
        assert oracle is not None
        # 15 s to connect; the dictionary queries get the usual round-trip limit.
        connection = self._get().connect(timeout=15)
        try:
            connection.call_timeout = int(self._settings.limits.round_trip_timeout_seconds * 1000)
            return Catalog.from_database(connection, sorted(oracle.allowed_schemas))
        finally:
            connection.close()

    def reset(self) -> None:
        """Connect with the password saved now, from the next query on."""
        with self._lock:
            self._database = None

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
