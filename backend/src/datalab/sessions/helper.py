"""The research helper: one approved question, answered by a throwaway research session.

A data-session agent can ask a question that needs the internet (package docs,
a method, a paper). The person sees the exact question first and approves,
edits, or declines it. The question and the decision are kept on the host
(approvals.py), never taken from the agent's side. Only then does this run: a
new research container with its own empty workspace, no conversation history,
no attached files, and no route to the data service. It runs one `codex exec`,
returns the answer, and is deleted. See docs/SAFETY.md.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import secrets
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio

from datalab.config import Settings
from datalab.sessions import codex_config
from datalab.sessions.approvals import Approvals
from datalab.sessions.checkpoints import UnsafePath, open_workspace_file
from datalab.sessions.containers import SessionContainers, SessionPaths, instance_of
from datalab.sessions.tokens import SessionAccess, SessionTokens

log = logging.getLogger(__name__)

_MAX_ANSWER_BYTES = 30_000
# How long a question waits for the person before it's withdrawn.
APPROVAL_WAIT_SECONDS = 15 * 60
# How long the helper itself may take, inside Codex's tool timeout.
HELPER_SECONDS = 420
# The helper's own instructions (it runs in the same image as data sessions,
# whose AGENTS.md is about study data).
_HELPER_AGENTS = """\
# Research helper

You are a research helper for a scientist. You have the internet (web search
and ordinary web access) but no study data, no files from the scientist, and
no earlier conversation. Answer the one question you're given, concisely and
accurately, in Markdown. Cite sources as links. If you're not sure, say so.
Don't ask follow-up questions; there is no one to answer them.
"""

Emit = Callable[[str, str, dict[str, Any]], object]
# Sends the approval request to Codex (an MCP elicitation) and waits for its
# reply. It only keeps Codex's tool timeout paused: the decision comes from
# the host's own record, never from the reply.
Elicit = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class HelperAnswer:
    status: str  # "answered", "declined", "failed"
    text: str
    question_sent: str | None = None


class ResearchHelper:
    def __init__(
        self, settings: Settings, tokens: SessionTokens, approvals: Approvals, emit: Emit
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._approvals = approvals
        self._emit = emit
        self._asking: dict[str, asyncio.Task[Any]] = {}  # conversation -> its question
        self._running = asyncio.Semaphore(2)  # helpers at once, across the app
        # Is a turn running in this conversation? Set by the app.
        self.turn_running: Callable[[str], bool] = lambda conversation_id: True

    async def ask(self, conversation_id: str, question: str, elicit: Elicit) -> HelperAnswer:
        """Ask the person to approve `question`; if they do, run the helper on their text."""
        if not self.turn_running(conversation_id):
            # Only the agent, while it's working on a message, may ask.
            return HelperAnswer("declined", "Questions can only be asked during a turn.")
        if conversation_id in self._asking:
            return HelperAnswer("declined", "Only one research-helper question at a time.")
        task = asyncio.current_task()
        assert task is not None
        self._asking[conversation_id] = task
        try:
            approved, text, approval_id = await self._approval(conversation_id, question, elicit)
            if not approved:
                return HelperAnswer("declined", "The person didn't send this question.")
            async with self._running:
                answer = await self._run(text)
            self._emit(
                conversation_id,
                "helper_answered",
                {"approval": approval_id, "status": answer.status, "answer": answer.text},
            )
            return HelperAnswer(answer.status, answer.text, text)
        finally:
            self._asking.pop(conversation_id, None)

    def cancel(self, conversation_id: str) -> None:
        """Stop a conversation's question, and its helper if one is running (Stop, close)."""
        task = self._asking.get(conversation_id)
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    async def _approval(
        self, conversation_id: str, question: str, elicit: Elicit
    ) -> tuple[bool, str, str]:
        pending = self._approvals.open(conversation_id, question)
        approved, text = await self._approvals.decide(
            pending, elicit, self._emit, APPROVAL_WAIT_SECONDS
        )
        return approved, text, pending.id

    async def _run(self, question: str) -> HelperAnswer:
        settings = self._settings
        helper_id = f"h_{secrets.token_hex(8)}"
        paths = SessionPaths(settings.data_dir / "helpers" / helper_id)
        containers = SessionContainers(
            helper_id,
            "research",
            paths,
            agent_image=settings.agent_image,
            host_port=settings.port,
            profile=settings.profile,
            instance=instance_of(settings.data_dir),
        )
        token = self._tokens.issue(
            SessionAccess(session_id=helper_id, kind="research", results_dir=paths.oracle_results)
        )
        process: asyncio.subprocess.Process | None = None
        try:
            paths.create()
            paths.codex_config.write_text(
                codex_config.render(
                    "research", model=settings.default_model, tool_timeout_seconds=60
                )
            )
            await containers.start(token)
            # Its own instructions, written from inside its own fresh container.
            await _run_in(containers.agent, 'cat > "$CODEX_HOME/AGENTS.md"', _HELPER_AGENTS)
            process = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", containers.agent,
                "codex", "exec", "--strict-config", "--skip-git-repo-check", "--ephemeral",
                "-C", "/work", "-o", "/work/answer.md", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            assert process.stdin is not None
            process.stdin.write(question.encode())
            process.stdin.close()
            tail = await asyncio.wait_for(_tail(process), HELPER_SECONDS)
            if process.returncode != 0:
                log.warning("research helper failed: %s", tail[-500:])
                return HelperAnswer("failed", "The research helper couldn't answer.")
            return HelperAnswer("answered", _read_answer(paths))
        except TimeoutError:
            return HelperAnswer("failed", "The research helper took too long and was stopped.")
        except Exception:
            log.exception("research helper failed")
            return HelperAnswer("failed", "The research helper couldn't start.")
        finally:
            self._tokens.revoke_session(helper_id)
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            # Cleanup must finish even if this call is cancelled, even more than
            # once: otherwise an internet-connected container would be left
            # running. So it runs as its own task, which cancelling this one
            # can't interrupt.
            cleanup = asyncio.ensure_future(_remove(containers, paths))
            with anyio.CancelScope(shield=True):
                while not cleanup.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.shield(cleanup)


async def _remove(containers: SessionContainers, paths: SessionPaths) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(containers.remove(), 60)
    shutil.rmtree(paths.root, ignore_errors=True)


async def _run_in(container: str, command: str, stdin: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "docker", "exec", "-i", container, "sh", "-c", command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    await process.communicate(stdin.encode())


async def _tail(process: asyncio.subprocess.Process, keep: int = 4096) -> str:
    """Wait for the process, keeping only the end of its error output."""
    assert process.stderr is not None
    tail: collections.deque[bytes] = collections.deque()
    size = 0
    while chunk := await process.stderr.read(4096):
        tail.append(chunk)
        size += len(chunk)
        while size > keep and len(tail) > 1:
            size -= len(tail.popleft())
    await process.wait()
    return b"".join(tail).decode(errors="replace")


def _read_answer(paths: SessionPaths) -> str:
    try:
        fd = open_workspace_file(paths.work, "answer.md")
    except UnsafePath:
        return "The research helper didn't write an answer."
    with os.fdopen(fd, "rb") as source:
        data = source.read(_MAX_ANSWER_BYTES + 1)
    text = data[:_MAX_ANSWER_BYTES].decode("utf-8", "replace")
    return text + ("\n\n(Answer shortened.)" if len(data) > _MAX_ANSWER_BYTES else "")


async def remove_leftovers(settings: Settings) -> None:
    """Helper folders left by a DataLab that stopped mid-question (containers go too)."""
    folder = settings.data_dir / "helpers"
    if folder.exists():
        await asyncio.to_thread(shutil.rmtree, folder, True)
