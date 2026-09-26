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
from datalab.sessions.checkpoints import CheckpointMissing, Checkpoints, RestoreResult
from datalab.sessions.containers import DockerError, SessionContainers, SessionPaths
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
        # Conversations whose files are being restored: no turn may start.
        self._restoring: set[str] = set()
        # Conversations whose next turn is being set up (between the busy
        # check and the turn's task starting), so a restore can't slip in.
        self._starting: set[str] = set()

    def paths(self, conversation_id: str) -> SessionPaths:
        return SessionPaths(self._settings.data_dir / "sessions" / conversation_id)

    def checkpoints(self, conversation_id: str) -> Checkpoints:
        paths = self.paths(conversation_id)
        return Checkpoints(paths.checkpoints, paths.work)

    def is_busy(self, conversation_id: str) -> bool:
        if conversation_id in self._restoring or conversation_id in self._starting:
            return True
        task = self._turns.get(conversation_id)
        return task is not None and not task.done()

    async def send(self, conversation: Conversation, text: str, effort: str | None) -> None:
        if self.is_busy(conversation.id):
            raise Busy("The agent is still working on the previous message.")
        # Reserved before the first await, so nothing else starts meanwhile.
        self._starting.add(conversation.id)
        try:
            await self._make_room(keep=conversation.id)
            runtime = self._runtime(conversation)
            runtime.begin_turn()
            self._last_used[conversation.id] = time.monotonic()
            note = self._workspace_note(conversation.id)
            self._store.append(conversation.id, "user_message", {"text": text})
            turn = self._store.count(conversation.id, "user_message")
            self._turns[conversation.id] = asyncio.create_task(
                self._run_turn(conversation.id, runtime, note + text, effort, turn)
            )
        finally:
            self._starting.discard(conversation.id)

    async def restore(self, conversation: Conversation, number: int) -> RestoreResult:
        """Put the workspace files back as they were at a checkpoint.

        The conversation isn't rewound; the agent is told on its next turn.
        The current files are checkpointed first, so a restore can be undone.
        """
        if self.is_busy(conversation.id):
            raise Busy("Stop the agent before restoring files.")
        checkpoints = self.checkpoints(conversation.id)
        target = checkpoints.get(number)
        if target is None:
            raise CheckpointMissing(number)
        self._restoring.add(conversation.id)
        try:
            # Nothing may run in the container while its files change.
            await self._shutdown(conversation.id)
            await self._containers(conversation).stop_and_confirm()
            before = await asyncio.to_thread(
                checkpoints.take, f"Before restoring to {target.label.lower()}"
            )
            # Whatever that checkpoint couldn't save is left alone: deleting it
            # would lose it for good.
            keep = frozenset(s.path for s in before.skipped)
            try:
                result = await asyncio.to_thread(checkpoints.restore, number, keep=keep)
            except Exception as error:
                log.exception("restore failed in %s", conversation.id)
                self._store.append(
                    conversation.id,
                    "files_restored",
                    {"label": target.label, "failed": True, "error": str(error)},
                )
                # So the files shown match what's there now.
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(checkpoints.take, "After a restore that failed")
                raise
            self._store.append(
                conversation.id,
                "files_restored",
                {
                    "checkpoint": number,
                    "label": target.label,
                    "turn": target.turn,
                    "written": result.written,
                    "removed": result.removed,
                    "left_alone": result.left_alone,
                    "not_restored": result.not_restored,
                },
            )
            try:
                await asyncio.to_thread(checkpoints.take, f"Restored to {target.label.lower()}")
            except Exception:
                log.exception("checkpoint after restore failed in %s", conversation.id)
                self._store.append(
                    conversation.id,
                    "notice",
                    {"text": "The files were restored, but DataLab couldn't save a checkpoint."},
                )
        finally:
            self._restoring.discard(conversation.id)
        return result

    async def stop(self, conversation_id: str) -> None:
        runtime = self._runtimes.get(conversation_id)
        if runtime and self.is_busy(conversation_id):
            self._store.append(conversation_id, "stop_requested", {})
            await runtime.stop_turn()

    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation and its workspace. Only the user does this."""
        await self._shutdown(conversation_id)
        runtime_paths = self.paths(conversation_id)
        await self._containers_for(conversation_id, "data").remove()
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

    def _containers(self, conversation: Conversation) -> SessionContainers:
        return self._containers_for(conversation.id, conversation.kind)

    def _containers_for(self, conversation_id: str, kind) -> SessionContainers:
        return SessionContainers(
            conversation_id,
            kind,
            self.paths(conversation_id),
            agent_image=self._settings.agent_image,
            host_port=self._settings.port,
            profile=self._settings.profile,
        )

    def _workspace_note(self, conversation_id: str) -> str:
        """Tell the agent if its files were restored since its last turn."""
        restored = self._store.last(conversation_id, "files_restored")
        asked = self._store.last(conversation_id, "user_message")
        if restored is None or (asked is not None and asked.seq > restored.seq):
            return ""
        label = str(restored.data.get("label", "an earlier checkpoint")).lower()
        if restored.data.get("failed"):
            return (
                f"[DataLab: the user tried to restore the files in /work to how they were "
                f"{label}, but it failed partway, so some files may be restored and others "
                "not. Check the files before relying on them.]\n\n"
            )
        return (
            f"[DataLab: the user restored the files in /work to how they were {label}. "
            "Changes made to /work since then were undone. Check the files before relying "
            "on anything you remember about them.]\n\n"
        )

    async def _checkpoint(self, conversation_id: str, runtime: SessionRuntime, turn: int) -> None:
        """Checkpoint /work after a turn, with the container frozen meanwhile."""
        containers = runtime.containers
        checkpoints = self.checkpoints(conversation_id)
        failed = {"text": "DataLab couldn't save a checkpoint of the files after this turn."}
        if not _enough_disk(self.paths(conversation_id).root, self._settings):
            self._store.append(
                conversation_id,
                "notice",
                {
                    "text": "This computer is low on disk space, so DataLab didn't save a "
                    "checkpoint of the files after this turn."
                },
            )
            return
        try:
            await containers.pause()
            take = asyncio.ensure_future(
                asyncio.to_thread(checkpoints.take, f"After turn {turn}", turn=turn)
            )
            try:
                checkpoint = await asyncio.shield(take)
            except asyncio.CancelledError:
                # Finish reading before the container can run again.
                with contextlib.suppress(Exception):
                    await take
                raise
        except DockerError:
            log.exception("couldn't pause %s for a checkpoint", conversation_id)
            self._store.append(conversation_id, "notice", failed)
            return
        except Exception:
            log.exception("checkpoint failed in %s", conversation_id)
            self._store.append(conversation_id, "notice", failed)
            return
        finally:
            # Always, even if cancelled: a paused agent can't work.
            with contextlib.suppress(DockerError):
                await containers.unpause()
        self._store.append(
            conversation_id,
            "checkpoint",
            {
                "number": checkpoint.number,
                "turn": turn,
                "files": checkpoint.files,
                "bytes": checkpoint.bytes,
                "skipped": len(checkpoint.skipped),
            },
        )

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
                self._containers(conversation),
                self._tokens,
                model=conversation.model,
                developer_instructions=modes.instructions(conversation.mode),
                tool_timeout_seconds=int(self._settings.limits.deadline_seconds) + 60,
                emit=emit,
            )
            self._runtimes[conversation.id] = runtime
        return runtime

    async def _run_turn(
        self,
        conversation_id: str,
        runtime: SessionRuntime,
        text: str,
        effort: str | None,
        turn: int,
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
        # Still part of the turn, so nothing else can start until it's saved.
        await self._checkpoint(conversation_id, runtime, turn)

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


def _enough_disk(folder, settings: Settings) -> bool:
    try:
        free = shutil.disk_usage(folder if folder.exists() else settings.data_dir).free
    except OSError:
        return True
    return free >= settings.limits.min_free_disk_bytes
