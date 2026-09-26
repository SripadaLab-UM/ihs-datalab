"""Keeps track of every running conversation.

Turns run as background tasks, so closing the browser never stops one. Idle
conversations have their containers stopped after a while; the next message
starts them again and Codex resumes the same thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from typing import Any

from datalab.config import Settings
from datalab.sessions import modes
from datalab.sessions.containers import SessionContainers, SessionPaths
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.store import Conversation, ConversationStore
from datalab.sessions.tokens import SessionTokens

log = logging.getLogger(__name__)


class Busy(RuntimeError):
    pass


class SessionManager:
    def __init__(
        self,
        settings: Settings,
        store: ConversationStore,
        tokens: SessionTokens,
        *,
        idle_seconds: float = 30 * 60,
        max_running: int = 3,
    ) -> None:
        self._settings = settings
        self._store = store
        self._tokens = tokens
        self._idle_seconds = idle_seconds
        self._max_running = max_running
        self._runtimes: dict[str, SessionRuntime] = {}
        self._last_used: dict[str, float] = {}
        self._turns: dict[str, asyncio.Task[None]] = {}

    def paths(self, conversation_id: str) -> SessionPaths:
        return SessionPaths(self._settings.data_dir / "sessions" / conversation_id)

    def is_busy(self, conversation_id: str) -> bool:
        task = self._turns.get(conversation_id)
        return task is not None and not task.done()

    async def send(self, conversation: Conversation, text: str, effort: str | None) -> None:
        if self.is_busy(conversation.id):
            raise Busy("The agent is still working on the previous message.")
        await self._make_room(keep=conversation.id)
        runtime = self._runtime(conversation)
        runtime.begin_turn()
        self._last_used[conversation.id] = time.monotonic()
        self._store.append(conversation.id, "user_message", {"text": text})
        self._turns[conversation.id] = asyncio.create_task(
            self._run_turn(conversation.id, runtime, text, effort)
        )

    async def stop(self, conversation_id: str) -> None:
        runtime = self._runtimes.get(conversation_id)
        if runtime and self.is_busy(conversation_id):
            self._store.append(conversation_id, "stop_requested", {})
            await runtime.stop_turn()

    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation and its workspace. Only the user does this."""
        await self._shutdown(conversation_id)
        runtime_paths = self.paths(conversation_id)
        await SessionContainers(
            conversation_id,
            "data",
            runtime_paths,
            agent_image=self._settings.agent_image,
            host_port=self._settings.port,
            profile=self._settings.profile,
        ).remove()
        self._store.delete(conversation_id)
        shutil.rmtree(runtime_paths.root, ignore_errors=True)

    async def reap_idle_forever(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for conversation_id in list(self._runtimes):
                idle = now - self._last_used.get(conversation_id, now)
                if idle > self._idle_seconds and not self.is_busy(conversation_id):
                    await self._shutdown(conversation_id)

    async def close_all(self) -> None:
        for task in self._turns.values():
            task.cancel()
        for conversation_id in list(self._runtimes):
            await self._shutdown(conversation_id)

    # ------------------------------------------------------------------------

    def _runtime(self, conversation: Conversation) -> SessionRuntime:
        runtime = self._runtimes.get(conversation.id)
        if runtime is None:
            paths = self.paths(conversation.id)

            async def emit(kind: str, data: dict[str, Any]) -> None:
                self._store.append(conversation.id, kind, data)

            runtime = SessionRuntime(
                conversation.id,
                conversation.kind,
                paths,
                SessionContainers(
                    conversation.id,
                    conversation.kind,
                    paths,
                    agent_image=self._settings.agent_image,
                    host_port=self._settings.port,
                    profile=self._settings.profile,
                ),
                self._tokens,
                model=conversation.model,
                developer_instructions=modes.instructions(conversation.mode),
                tool_timeout_seconds=int(self._settings.limits.deadline_seconds) + 60,
                emit=emit,
            )
            self._runtimes[conversation.id] = runtime
        return runtime

    async def _run_turn(
        self, conversation_id: str, runtime: SessionRuntime, text: str, effort: str | None
    ) -> None:
        try:
            result = await runtime.send(text, effort=effort)
            if result.status != "completed" and result.error:
                self._store.append(conversation_id, "error", {"message": result.error})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("turn failed in %s", conversation_id)
            self._store.append(
                conversation_id, "turn_finished", {"status": "failed", "error": str(error)}
            )
        finally:
            self._last_used[conversation_id] = time.monotonic()

    async def _make_room(self, keep: str) -> None:
        """Stop the least recently used idle conversation if too many are running."""
        running = [c for c in self._runtimes if c != keep]
        while len(running) + 1 > self._max_running:
            idle = [c for c in running if not self.is_busy(c)]
            if not idle:
                raise Busy(
                    f"{self._max_running} conversations are already working. "
                    "Wait for one to finish, or stop one."
                )
            oldest = min(idle, key=lambda c: self._last_used.get(c, 0))
            await self._shutdown(oldest)
            running.remove(oldest)

    async def _shutdown(self, conversation_id: str) -> None:
        task = self._turns.pop(conversation_id, None)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        runtime = self._runtimes.pop(conversation_id, None)
        if runtime:
            await runtime.close()
