"""Running one conversation: its containers, its Codex, and its turns.

Codex's app-server notifications are turned into a small set of DataLab
events (answer text, reasoning, commands, tool calls, turn status). The web
UI, the event log, and the CLI all consume these.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from datalab.data.failures import read_tag
from datalab.sessions import codex_config
from datalab.sessions.approvals import Approvals
from datalab.sessions.appserver import AppServerClient, AppServerError
from datalab.sessions.containers import SessionContainers, SessionPaths
from datalab.sessions.tokens import SessionAccess, SessionKind, SessionTokens
from datalab.sessions.tracing import Source

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
    started: bool = False  # turn/started has given the running turn's id
    interrupted: str | None = None  # the turn id a turn/interrupt went to
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
        approvals: Approvals | None = None,
        tools: frozenset[str] | None = None,
        tools_off: tuple[str, ...] = (),
    ) -> None:
        self.session_id = session_id
        self.kind: SessionKind = kind
        self.paths = paths
        self.containers = containers
        self._tokens = tokens
        self._model = model
        self._instructions = developer_instructions
        self._tool_timeout = tool_timeout_seconds
        self._tools = tools
        self._tools_off = tools_off
        self._emit = emit
        self._client: AppServerClient | None = None
        self._thread_id: str | None = self._saved_thread_id()
        self._turn: _Turn | None = None
        self._stop_requested = False
        self._turns_begun = 0  # which turn is current, for the relay's retries
        self._lock = asyncio.Lock()
        self._approvals = approvals
        self._requests: dict[Any, str] = {}  # Codex request id -> approval id
        self._evidence: list[str] = []  # this turn's output, for tracing
        # The same, with where each came from, for provenance. Not drained by
        # take_evidence, and not added to by a review (which isn't the turn's work).
        self._sourced: list[tuple[Source, str]] = []
        self._reviewing = False
        self._ran_commands = False
        self._background: set[asyncio.Task[None]] = set()

    @property
    def busy(self) -> bool:
        return self._turn is not None

    def begin_turn(self) -> None:
        """Call before scheduling `send`, so a Stop that arrives early still counts."""
        self._stop_requested = False
        self._turns_begun += 1

    async def send(self, text: str, *, effort: str | None = None) -> TurnResult:
        """Run one turn and wait for it to finish, emitting events as it goes."""
        async with self._lock:
            await self._ensure_started()
            assert self._client is not None and self._thread_id is not None
            if self._stop_requested:
                # Stopped while the container was starting: don't begin the work.
                await self._emit("turn_finished", {"status": "interrupted"})
                return TurnResult("", "interrupted")
            self._turn = turn = _Turn()
            self._evidence = []
            self._sourced = []
            self._ran_commands = False
            params: dict[str, Any] = {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": text, "text_elements": []}],
            }
            if effort:
                params["effort"] = effort
            try:
                response = await self._client.request("turn/start", params)
                # turn/started may already have given the running turn's id.
                turn.id = turn.id or (response.get("turn") or {}).get("id")
                if self._stop_requested:
                    await self._interrupt(turn)
                return await self._wait_for(turn, self._client)
            finally:
                self._turn = None

    async def review(self, instructions: str) -> TurnResult:
        """Run Codex's review mode on the thread, with our checklist, and wait for it."""
        async with self._lock:
            await self._ensure_started()
            assert self._client is not None and self._thread_id is not None
            if self._stop_requested:
                return TurnResult("", "interrupted")
            self._turn = turn = _Turn()
            self._ran_commands = False
            self._reviewing = True
            params = {
                "threadId": self._thread_id,
                "target": {"type": "custom", "instructions": instructions},
                "delivery": "inline",
            }
            try:
                response = await self._client.request("review/start", params)
                # turn/started may already have given the running turn's id.
                turn.id = turn.id or (response.get("turn") or {}).get("id")
                if self._stop_requested:
                    await self._interrupt(turn)
                return await self._wait_for(turn, self._client)
            finally:
                self._turn = None
                self._reviewing = False

    def take_evidence(self) -> list[str]:
        """What the last turn produced, for tracing its answer. Kept in memory only."""
        evidence, self._evidence = self._evidence, []
        return evidence

    def turn_sources(self) -> list[tuple[Source, str]]:
        """What the last turn produced, with where each piece came from: each
        command's output and each query's result. For provenance; kept in
        memory only, like the evidence."""
        return list(self._sourced)

    def ran_commands(self) -> bool:
        """Whether the last turn (or review) ran any command. Resets."""
        ran, self._ran_commands = self._ran_commands, False
        return ran

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def turn_number(self) -> int:
        """Counts `begin_turn` calls: a model request belongs to the turn that
        was current when it arrived."""
        return self._turns_begun

    def _collect_evidence(self, item: dict[str, Any]) -> None:
        # A command's whole output, and the data tool's query results. Not other
        # tools (the research helper's web answer, the agent's own plan).
        if item.get("type") == "fileChange":
            self._ran_commands = True  # files changed, as a command could have
        if item.get("type") == "commandExecution":
            self._ran_commands = True
            text = str(item.get("aggregatedOutput") or "")
            source = Source("command", str(item.get("id") or ""))
        elif item.get("type") == "mcpToolCall" and item.get("tool") == "query":
            text = _result_text(item.get("result"))
            source = Source("query", _query_id(text))
        else:
            return
        if text and sum(len(t) for t in self._evidence) < _MAX_EVIDENCE:
            self._evidence.append(text[:_MAX_RESULT_TEXT])
            if not self._reviewing and source.ref:
                self._sourced.append((source, text[:_MAX_RESULT_TEXT]))

    async def stop_turn(self) -> None:
        """Stop the running turn, including any commands it started.

        If the turn hasn't started yet (the container is still starting), the
        request is remembered and `send` stops before doing any work.
        """
        self._stop_requested = True
        turn = self._turn
        if turn is not None and turn.id is not None:
            await self._interrupt(turn)

    async def close(self) -> None:
        """Stop Codex and the containers. The workspace stays on disk."""
        # Revoked first, so nothing can open a new question while closing.
        self._tokens.revoke_session(self.session_id)
        await self._withdraw()
        if self._client:
            await self._client.close()
            self._client = None
        await self.containers.stop()
        self._tokens.revoke_session(self.session_id)

    async def _interrupt(self, turn: _Turn) -> None:
        if self._client is None or self._thread_id is None or turn.id is None:
            return
        # A Stop while turn/start is answered reaches here twice (from
        # stop_turn and from send): one interrupt per turn id, or the turn
        # could be ended twice. A refused one (the wrong id, before Codex
        # named the running review) can be sent again.
        if turn.interrupted == turn.id:
            return
        sent = turn.interrupted = turn.id
        try:
            await self._client.request(
                "turn/interrupt", {"threadId": self._thread_id, "turnId": sent}, timeout=30
            )
        except (AppServerError, TimeoutError) as error:
            # Only this call's mark: a late refusal of an old id mustn't clear
            # the mark of an interrupt sent since to the new one.
            if turn.interrupted == sent:
                turn.interrupted = None
            log.warning("couldn't interrupt turn in %s: %s", self.session_id, error)
        except asyncio.CancelledError:
            if turn.interrupted == sent:
                turn.interrupted = None
            raise
        except Exception:
            log.exception("interrupting a turn failed in %s", self.session_id)
        finally:
            # Whatever Codex said: the commands the turn started are stopped,
            # unless a later turn has started meanwhile.
            if self._turn is turn or self._turn is None:
                with contextlib.suppress(Exception):
                    await self.containers.kill_turn_processes()

    async def _wait_for(self, turn: _Turn, client: AppServerClient) -> TurnResult:
        """The turn's result, or a failure if Codex exits before finishing it."""
        await asyncio.wait({turn.done, client.closed}, return_when=asyncio.FIRST_COMPLETED)
        if turn.done.done():
            return turn.done.result()
        await self._withdraw()
        message = (
            "The agent stopped unexpectedly. What it did so far is kept: "
            "Continue picks up where it left off."
        )
        await self._emit("turn_finished", {"status": "failed", "error": message})
        return TurnResult(turn.id or "", "failed", message)

    # Starting -------------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._client and self._client.alive and await self.containers.is_running():
            return
        # (Re)start everything together: a container that is still running
        # holds the previous session token, which is revoked below.
        if self._client:
            await self._client.close()
            self._client = None
        # Confirmed gone, never reused: a container that couldn't be removed
        # may still have an old mount (such as a removed attachment).
        await self.containers.stop_and_confirm()
        self._tokens.revoke_session(self.session_id)
        token = self._tokens.issue(
            SessionAccess(
                session_id=self.session_id,
                kind=self.kind,
                results_dir=self.paths.oracle_results,
                tools=self._tools,
            )
        )
        self.paths.create()
        self.paths.codex_config.write_text(
            codex_config.render(
                self.kind,
                model=self._model,
                tool_timeout_seconds=self._tool_timeout,
                tools_off=self._tools_off,
            ),
            encoding="utf-8",
            newline="\n",
        )
        await self.containers.start(token)
        process = await self.containers.open_app_server()
        self._client = AppServerClient(
            process, on_notification=self._on_notification, on_server_request=self._on_request
        )
        await self._client.initialize()
        await self._add_skill_roots()
        await self._open_thread()

    async def _add_skill_roots(self) -> None:
        assert self._client is not None
        try:
            roots = list(codex_config.SKILL_ROOTS)
            await self._client.request("skills/extraRoots/set", {"extraRoots": roots})
        except AppServerError as error:
            # The session works without the lab's skills; the app skills remain.
            log.warning("couldn't add the lab's skills for %s: %s", self.session_id, error)

    async def _open_thread(self) -> None:
        assert self._client is not None
        common = {"cwd": "/work", "developerInstructions": self._instructions}
        if self._thread_id:
            try:
                await self._client.request("thread/resume", {"threadId": self._thread_id, **common})
                return
            except AppServerError as error:
                # Say so: otherwise the conversation looks intact on screen
                # while the agent has silently lost everything said earlier.
                log.warning("couldn't resume thread for %s: %s", self.session_id, error)
                await self._emit(
                    "notice",
                    {
                        "text": "The agent couldn't restore its memory of this conversation, "
                        "so it's starting fresh. Repeat any context it needs."
                    },
                )
        response = await self._client.request("thread/start", {"model": self._model, **common})
        self._thread_id = (response.get("thread") or {}).get("id")
        if not self._thread_id:
            raise RuntimeError("Codex didn't return a thread id.")
        (self.paths.root / "thread.json").write_text(
            json.dumps({"thread_id": self._thread_id}), encoding="utf-8"
        )

    def _saved_thread_id(self) -> str | None:
        path = self.paths.root / "thread.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("thread_id")
        return None

    # Codex -> DataLab events ------------------------------------------------

    async def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        thread = params.get("threadId")
        if thread is not None and self._thread_id is not None and thread != self._thread_id:
            return  # another thread's news isn't this conversation's
        if method == "serverRequest/resolved":
            # Codex withdrew a request it had sent (for example, on Stop).
            await self._withdraw(params.get("requestId"))
        if method == "item/completed":
            self._collect_evidence(params.get("item") or {})
        if method == "turn/started" and self._turn is not None and not self._turn.started:
            # The id of the turn actually running. For a review, Codex 0.157
            # answers review/start with a different id than the turn it runs,
            # and only this one can be interrupted.
            started = (params.get("turn") or {}).get("id")
            if started:
                self._turn.id = started
                self._turn.started = True
                if self._stop_requested:
                    # Stop came before Codex said which turn is running.
                    task = asyncio.get_running_loop().create_task(self._interrupt(self._turn))
                    self._background.add(task)
                    task.add_done_callback(self._background.discard)
        if self._stop_requested:
            # Stopping. A model request Codex sent before the interrupt reached
            # it is refused by the relay (its turn is over), and Codex reports
            # that as an error and a failed turn. It was the person's Stop.
            if method == "error":
                return
            turn = params.get("turn") or {}
            if method == "turn/completed" and turn.get("status") == "failed":
                params = {**params, "turn": {**turn, "status": "interrupted", "error": None}}
        event = _to_event(method, params)
        if event:
            await self._emit(*event)
        if method == "turn/completed":
            await self._withdraw()
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

    async def _on_request(
        self, method: str, params: dict[str, Any], request_id: Any
    ) -> dict[str, Any]:
        """Codex asking DataLab something. Only DataLab's own approvals are answered.

        The research helper's tool (data/agent_tools.py) asks for approval with
        an MCP elicitation, which Codex forwards here. The question and the
        person's decision live in DataLab's own record (approvals.py); this
        reply only lets Codex carry on once the person has decided.
        """
        requested = _approval_id(method, params)
        kind, _, approval_id = (requested or "").partition(":")
        pending = (
            self._approvals.get(approval_id, self.session_id)
            if approval_id and self._approvals
            else None
        )
        if pending is not None and pending.kind != kind:
            pending = None
        if pending is None:
            # Nothing else should ask, given the approval policy: decline it.
            log.warning("declining unexpected request from Codex: %s", method)
            if method == "mcpServer/elicitation/request":
                return {"action": "decline"}
            return {"decision": "decline"}
        self._requests[request_id] = pending.id
        if not pending.shown:
            pending.shown = True
            await self._emit("approval_requested", pending.card())
        try:
            approved, _ = await asyncio.shield(pending.decision)
        finally:
            self._requests.pop(request_id, None)
        return {"action": "accept", "content": {}} if approved else {"action": "decline"}

    async def _withdraw(self, request_id: Any = None) -> None:
        """Withdraw pending approvals: the one Codex resolved itself, or all of them."""
        if self._approvals is None:
            return
        approval_id = None
        if request_id is not None:
            approval_id = self._requests.get(request_id)
            if approval_id is None:
                return
        for withdrawn in self._approvals.withdraw(self.session_id, approval_id):
            await self._emit("approval_withdrawn", {"id": withdrawn})


def _approval_id(method: str, params: dict[str, Any]) -> str | None:
    """The approval an elicitation is about, if it's DataLab's own request."""
    if method != "mcpServer/elicitation/request" or params.get("serverName") != "ihs-data":
        return None
    if (params.get("_meta") or {}).get("codex_approval_kind"):
        return None  # a Codex tool-call prompt, not DataLab's
    try:
        message = json.loads(params.get("message") or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict) or message.get("datalab") not in (
        "research_helper",
        "analysis_plan",
    ):
        return None
    approval = message.get("approval")
    return f"{message['datalab']}:{approval}" if isinstance(approval, str) else None


_MAX_RESULT_TEXT = 200_000
_MAX_EVIDENCE = 5 * 1024**2


# The query tool's result starts with its Data accessed id ({"query_id": ...}).
# Read with a pattern, not by parsing: a wide preview is cut to
# _MAX_RESULT_TEXT, which leaves the JSON unparseable.
_QUERY_ID = re.compile(r'\A\s*\{\s*"query_id"\s*:\s*"([\w.-]{1,64})"')


def _query_id(result_text: str) -> str:
    """The Data accessed log's id for a query, from the query tool's result."""
    found = _QUERY_ID.match(result_text)
    return found.group(1) if found else ""


def _result_text(result: Any) -> str:
    """The text of a tool's result, shortened."""
    if not isinstance(result, dict):
        return ""
    parts = [
        c.get("text", "")
        for c in result.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "\n".join(parts)[:_MAX_RESULT_TEXT]


def tool_summary(tool: Any, server: Any, result: Any) -> dict[str, Any] | None:
    """What an ihs-data tool returned, for the chat's step views: metadata only.

    Catalog tools return names, types, and comments, kept (trimmed). A query
    keeps its row count, columns, and file, never its rows: study data stays
    out of the event log (the person can open the result file itself). The
    text arrives through Codex in the container, so it's untrusted: only plain
    fields are kept, and the chat renders them as text.
    """
    if server != "ihs-data" or not isinstance(result, dict):
        return None
    text = "\n".join(
        c.get("text", "")
        for c in result.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    )[:1_000_000]
    try:
        data = json.loads(text)
    except ValueError:
        return None

    def s(value: Any, limit: int = 120) -> str:
        return value[:limit] if isinstance(value, str) else ""

    def names(values: Any, limit: int = 8) -> list[str]:
        return [s(v, 80) for v in values[:limit]] if isinstance(values, list) else []

    def hit(h: dict) -> dict[str, Any]:
        return {
            "table": s(h.get("table"), 80),
            "comment": s(h.get("comment")),
            "columns": names(h.get("matching_columns"), 6),
        }

    if tool == "search_catalog" and isinstance(data, list):
        return {"tables": [hit(h) for h in data[:15] if isinstance(h, dict)]}
    if tool == "find_concept" and isinstance(data, dict):
        found = [h for h in (data.get("candidates") or [])[:12] if isinstance(h, dict)]
        return {"tables": [hit(h) for h in found]}
    if tool == "describe_table" and isinstance(data, dict):
        columns = [c for c in data.get("columns") or [] if isinstance(c, dict)]
        return {
            "table": s(data.get("table"), 80),
            "comment": s(data.get("comment"), 300),
            "column_count": len(columns),
            "columns": [
                {
                    "name": s(c.get("name"), 80),
                    "type": s(c.get("type"), 40),
                    "comment": s(c.get("comment"), 160),
                }
                for c in columns[:80]
            ],
            "also_in": names(data.get("also_in")),
        }
    if tool == "join_paths" and isinstance(data, dict):
        return {
            "tables": names(data.get("tables"), 2),
            "shared": [
                {
                    "column": s(k.get("column"), 80),
                    "role": s(k.get("role"), 20),
                    "note": s(k.get("note"), 200),
                }
                for k in (data.get("shared_columns") or [])[:10]
                if isinstance(k, dict)
            ],
            "notes": [s(n, 300) for n in (data.get("notes") or [])[:5]],
        }
    if tool == "check_workflow" and isinstance(data, dict):
        # Whether it passed and how many problems: the problems' text stays in the tool's own view.
        problems = data.get("problems")
        return {
            "valid": data.get("valid") is True,
            "problem_count": len(problems) if isinstance(problems, list) else 0,
        }
    if tool == "query" and isinstance(data, dict):
        count = data.get("row_count")
        query_id = data.get("query_id")
        return {
            "query_id": query_id
            if isinstance(query_id, str) and _DATALAB_QUERY_ID.fullmatch(query_id)
            else None,
            "row_count": count if isinstance(count, int) else None,
            "columns": names(data.get("columns"), 40),
            "result_file": s(data.get("result_file"), 200),
            "tables": names(data.get("tables"), 10),
            "warnings": [s(w, 200) for w in (data.get("warnings") or [])[:5]],
        }
    return None


_DATALAB_QUERY_ID = re.compile(r"q_\d{8}T\d{6}_[0-9a-f]{6}")
# What the chat shows of a failed tool call's text: enough for the reason.
MAX_TOOL_ERROR = 1_000
_TOOL_ERROR_PREFIX = re.compile(r"^Error executing tool [A-Za-z0-9_]+: ")


def tool_failure(item: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
    """A failed MCP tool call's error, and its structured failure (failures.py).

    Codex puts a tool's own error in the result's text content and leaves
    `error` empty, so the reason is taken from there when `error` has none.
    Codex's rollout file marks that result `isError`, but the live app-server
    notification doesn't: it only says `status: "failed"`. Either is enough.
    It's bounded text, rendered as text by the chat. Only failed calls: a
    successful result (a query's rows) is never read as an error.
    """
    error = item.get("error")
    result = item.get("result")
    text = ""
    failed = item.get("status") == "failed"
    if isinstance(result, dict) and (failed or result.get("isError") or result.get("is_error")):
        text = "\n".join(
            c.get("text", "")
            for c in result.get("content") or []
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
        )
    elif isinstance(error, dict) and isinstance(error.get("message"), str):
        text = error["message"]
    elif isinstance(error, str):
        text = error
    if not text:
        return error, None
    # Only the query tool's errors carry a failure tag (data/failures.py).
    if item.get("tool") == "query" and item.get("server") == "ihs-data":
        shown, failure = read_tag(text[-20_000:])
    else:
        shown, failure = text, None
    shown = _TOOL_ERROR_PREFIX.sub("", shown.strip())[:MAX_TOOL_ERROR]
    found = (
        {"category": failure.category, "code": failure.code, "query_id": failure.query_id}
        if failure
        else None
    )
    return (error if error and not shown else shown or error), found


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
                # Fast commands arrive whole, without streamed output. The chat
                # uses this only if nothing streamed (the same text either way).
                "output": str(item.get("aggregatedOutput") or "")[-20_000:],
            }
        if kind == "mcpToolCall":
            error, failure = tool_failure(item)
            failed = bool(error) or failure is not None
            return "tool_call", {
                "id": item.get("id"),
                "server": item.get("server"),
                "tool": item.get("tool"),
                "arguments": item.get("arguments"),
                "status": item.get("status"),
                "error": error or None,
                "failure": failure,
                "summary": None
                if failed
                else tool_summary(item.get("tool"), item.get("server"), item.get("result")),
            }
        if kind == "fileChange":
            changes = item.get("changes") or []
            return "files_changed", {
                "paths": [c.get("path") for c in changes if isinstance(c, dict)]
            }
        if kind == "webSearch":
            return "web_search", {"query": item.get("query")}
        if kind == "exitedReviewMode":
            return "review", {"id": item.get("id"), "text": item.get("review") or ""}
    if method == "turn/started":
        return "turn_started", {"turn_id": (params.get("turn") or {}).get("id")}
    if method == "turn/completed":
        turn = params.get("turn") or {}
        return "turn_finished", {"turn_id": turn.get("id"), "status": turn.get("status")}
    if method == "thread/tokenUsage/updated":
        return "usage", params.get("tokenUsage") or params
    if method == "error":
        message = (params.get("error") or {}).get("message") or str(params)
        if params.get("willRetry") is True:
            # Codex is retrying by itself, typically a model stream that
            # dropped ("Reconnecting... 1/2"). Not a turn error: if the retries
            # run out, Codex sends another error with willRetry false.
            return "notice", {"tone": "info", "text": retrying_note(message), "retry": True}
        return "error", {"message": message}
    return None


def retrying_note(message: str) -> str:
    """What the chat says while Codex retries a model request by itself."""
    note = "The connection to the model dropped for a moment; the agent is trying again"
    return f"{note} ({message})."
