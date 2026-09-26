"""Running one conversation: its containers, its Codex, and its turns.

Codex's app-server notifications are turned into a small set of DataLab
events (answer text, reasoning, commands, tool calls, turn status). The web
UI, the event log, and the CLI all consume these.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from datalab.sessions import codex_config
from datalab.sessions.appserver import AppServerClient
from datalab.sessions.containers import SessionContainers, SessionPaths
from datalab.sessions.tokens import SessionAccess, SessionKind, SessionTokens

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class TurnResult:
    turn_id: str
    status: str  # completed, interrupted, failed
    error: str | None = None


@dataclass
class _Turn:
    id: str | None = None
    done: asyncio.Future[TurnResult] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class SessionRuntime:
    def __init__(
        self,
        session_id: str,
        kind: SessionKind,
        paths: SessionPaths,
        containers: SessionContainers,
        tokens: SessionTokens,
        *,
        model: str,
        developer_instructions: str,
        tool_timeout_seconds: int,
        emit: Emit,
    ) -> None:
        self.session_id = session_id
        self.kind: SessionKind = kind
        self.paths = paths
        self.containers = containers
        self._tokens = tokens
        self._model = model
        self._instructions = developer_instructions
        self._tool_timeout = tool_timeout_seconds
        self._emit = emit
        self._client: AppServerClient | None = None
        self._thread_id: str | None = self._saved_thread_id()
        self._turn: _Turn | None = None
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._turn is not None

    async def send(self, text: str, *, effort: str | None = None) -> TurnResult:
        """Run one turn and wait for it to finish, emitting events as it goes."""
        async with self._lock:
            await self._ensure_started()
            assert self._client is not None and self._thread_id is not None
            self._turn = _Turn()
            params: dict[str, Any] = {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": text, "text_elements": []}],
            }
            if effort:
                params["effort"] = effort
            try:
                response = await self._client.request("turn/start", params)
                self._turn.id = (response.get("turn") or {}).get("id")
                return await self._turn.done
            finally:
                self._turn = None

    async def stop_turn(self) -> None:
        """Stop the running turn, including any commands it started."""
        turn = self._turn
        if turn is None or self._client is None or self._thread_id is None or turn.id is None:
            return
        await self._client.request(
            "turn/interrupt", {"threadId": self._thread_id, "turnId": turn.id}, timeout=30
        )
        await self.containers.kill_turn_processes()

    async def close(self) -> None:
        """Stop Codex and the containers. The workspace stays on disk."""
        if self._client:
            await self._client.close()
            self._client = None
        await self.containers.stop()
        self._tokens.revoke_session(self.session_id)

    # Starting -------------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._client and self._client.alive and await self.containers.is_running():
            return
        if self._client:
            await self._client.close()
        self._tokens.revoke_session(self.session_id)
        token = self._tokens.issue(
            SessionAccess(
                session_id=self.session_id, kind=self.kind, results_dir=self.paths.oracle_results
            )
        )
        self.paths.create()
        (self.paths.codex_home / "config.toml").write_text(
            codex_config.render(
                self.kind, model=self._model, tool_timeout_seconds=self._tool_timeout
            )
        )
        await self.containers.start(token)
        process = await self.containers.open_app_server()
        self._client = AppServerClient(
            process, on_notification=self._on_notification, on_server_request=self._on_request
        )
        await self._client.initialize()
        await self._open_thread()

    async def _open_thread(self) -> None:
        assert self._client is not None
        common = {"cwd": "/work", "developerInstructions": self._instructions}
        if self._thread_id:
            try:
                await self._client.request("thread/resume", {"threadId": self._thread_id, **common})
                return
            except Exception:
                log.warning("couldn't resume thread for %s; starting a new one", self.session_id)
        response = await self._client.request("thread/start", {"model": self._model, **common})
        self._thread_id = (response.get("thread") or {}).get("id")
        if not self._thread_id:
            raise RuntimeError("Codex didn't return a thread id.")
        (self.paths.root / "thread.json").write_text(json.dumps({"thread_id": self._thread_id}))

    def _saved_thread_id(self) -> str | None:
        path = self.paths.root / "thread.json"
        if path.exists():
            return json.loads(path.read_text()).get("thread_id")
        return None

    # Codex -> DataLab events ------------------------------------------------

    async def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        event = _to_event(method, params)
        if event:
            await self._emit(*event)
        if method == "turn/completed" and self._turn and not self._turn.done.done():
            turn = params.get("turn") or {}
            error = turn.get("error")
            self._turn.done.set_result(
                TurnResult(
                    turn_id=turn.get("id", ""),
                    status=turn.get("status", "completed"),
                    error=(error or {}).get("message") if isinstance(error, dict) else error,
                )
            )

    async def _on_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        # DataLab's own approval flows (the research helper) arrive here as MCP
        # elicitations; they are added in a later milestone. Nothing else
        # should ask, given the approval policy, so decline anything that does.
        log.warning("declining unexpected request from Codex: %s", method)
        if method == "mcpServer/elicitation/request":
            return {"action": "decline"}
        return {"decision": "decline"}


def _to_event(method: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    item = params.get("item") or {}
    kind = item.get("type")
    if method == "item/agentMessage/delta":
        return "answer_delta", {"id": params.get("itemId"), "text": params.get("delta", "")}
    if method == "item/started" and kind == "agentMessage":
        # "commentary" is the agent narrating its progress; "final_answer" is the answer.
        return "answer_started", {"id": item.get("id"), "phase": item.get("phase")}
    if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
        return "reasoning_delta", {"text": params.get("delta", "")}
    if method == "item/commandExecution/outputDelta":
        return "command_output", {"id": params.get("itemId"), "text": params.get("delta", "")}
    if method == "item/started" and kind == "commandExecution":
        return "command_started", {"id": item.get("id"), "command": item.get("command")}
    if method == "item/completed":
        if kind == "agentMessage":
            return "answer", {
                "id": item.get("id"),
                "phase": item.get("phase"),
                "text": item.get("text", ""),
            }
        if kind == "commandExecution":
            return "command_finished", {
                "id": item.get("id"),
                "exit_code": item.get("exitCode"),
                "status": item.get("status"),
            }
        if kind == "mcpToolCall":
            return "tool_call", {
                "id": item.get("id"),
                "server": item.get("server"),
                "tool": item.get("tool"),
                "arguments": item.get("arguments"),
                "status": item.get("status"),
                "error": item.get("error"),
            }
        if kind == "fileChange":
            changes = item.get("changes") or []
            return "files_changed", {
                "paths": [c.get("path") for c in changes if isinstance(c, dict)]
            }
        if kind == "webSearch":
            return "web_search", {"query": item.get("query")}
    if method == "turn/started":
        return "turn_started", {"turn_id": (params.get("turn") or {}).get("id")}
    if method == "turn/completed":
        turn = params.get("turn") or {}
        return "turn_finished", {"turn_id": turn.get("id"), "status": turn.get("status")}
    if method == "thread/tokenUsage/updated":
        return "usage", params.get("tokenUsage") or params
    if method == "error":
        return "error", {"message": (params.get("error") or {}).get("message") or str(params)}
    return None
