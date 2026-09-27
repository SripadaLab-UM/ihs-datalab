"""Keeps track of every running conversation.

Turns run as background tasks, so closing the browser never stops one. Idle
conversations have their containers stopped after a while; the next message
starts them again and Codex resumes the same thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from datalab.config import Settings, default_data_dir
from datalab.sessions import modes, rigor
from datalab.sessions.approvals import Approvals
from datalab.sessions.checkpoints import CheckpointMissing, Checkpoints, RestoreResult
from datalab.sessions.containers import DockerError, SessionContainers, SessionPaths, instance_of
from datalab.sessions.helper import ResearchHelper
from datalab.sessions.inputs import (
    AttachmentStore,
    is_sample,
    mount_args,
    practice_samples,
    recheck,
)
from datalab.sessions.plan_schema import TYPES_BY_ID
from datalab.sessions.plans import PlanStore
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.store import Conversation, ConversationStore, Event
from datalab.sessions.tokens import SessionTokens
from datalab.sessions.tracing import trace

log = logging.getLogger(__name__)


# What Continue sends: the same thread picks up where a turn stopped part-way.
CONTINUE_TEXT = (
    "Please continue where you left off: the last turn was interrupted before it finished."
)


class NothingToReview(RuntimeError):
    """There's no unfinished rigor review to run again."""


class NothingToContinue(RuntimeError):
    """The last turn didn't fail, so Continue has nothing to pick up."""


# What the chat reads a turn from (frontend transcript.ts), for Continue.
# During a review, the review's own events aren't the turn's.
_REVIEW_TURN_EVENTS = frozenset(
    {
        "answer_started", "answer_delta", "answer", "turn_started", "turn_finished",
        "reasoning_delta", "command_started", "command_output", "command_finished",
        "tool_call", "files_changed", "web_search", "model_status", "error",
    }
)  # fmt: skip
# The agent doing anything means its model requests went through again.
_ACTIVITY = frozenset(
    {"answer_started", "answer_delta", "reasoning_delta", "command_started", "tool_call"}
)
# Shown by the chat as a turn of their own, after the one that failed.
_OWN_TURN_EVENTS = frozenset({"exported", "files_restored", "input_attached", "input_removed"})
# Model trouble that picking up again won't fix.
_NOT_CONTINUABLE = frozenset({"quota", "auth", "request"})


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
        attachments: AttachmentStore | None = None,
        approvals: Approvals | None = None,
    ) -> None:
        self._approvals = approvals or Approvals()
        # The research helper, so Stop and close can stop its work too (set by the app).
        self.helper: ResearchHelper | None = None
        # Approved analysis plans, for the rigor review (set by the app).
        self.plans: PlanStore | None = None
        self._settings = settings
        self._store = store
        self._tokens = tokens
        self._attachments = attachments
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

    async def send(
        self, conversation: Conversation, text: str, effort: str | None, *, continues: bool = False
    ) -> None:
        """Start a turn. `continues`: it picks up a turn that failed (Continue), so
        its review reads that turn's question and work too."""
        if self.is_busy(conversation.id):
            raise Busy("The agent is still working on the previous message.")
        if continues and not self._last_turn_failed(conversation.id):
            raise NothingToContinue(
                "There's nothing to continue: Continue picks up a turn that failed, "
                "and the last one didn't."
            )
        # Reserved before the first await, so nothing else starts meanwhile.
        self._starting.add(conversation.id)
        try:
            await self._make_room(keep=conversation.id)
            runtime = self._runtime(conversation)
            runtime.begin_turn()
            self._last_used[conversation.id] = time.monotonic()
            note = self._workspace_note(conversation.id)
            self._store.append(
                conversation.id,
                "user_message",
                {"text": text, **({"continues": True} if continues else {})},
            )
            turn = self._store.count(conversation.id, "user_message")
            self._turns[conversation.id] = asyncio.create_task(
                self._run_turn(conversation.id, runtime, note + text, effort, turn)
            )
        finally:
            self._starting.discard(conversation.id)

    async def rerun_review(self, conversation: Conversation) -> None:
        """Run the last turn's rigor review again after it couldn't finish (the
        model service was busy, say, or it was stopped). The analysis isn't
        redone: the review reads the same answer, plans and queries."""
        if self.is_busy(conversation.id):
            raise Busy("The agent is still working.")
        asked = self._store.last(conversation.id, "user_message")
        started = self._store.last(conversation.id, "review_started")
        finished = self._store.last(conversation.id, "review_finished")
        # Anchored on the review's start: one cut short by a restart never finished.
        if (
            asked is None
            or started is None
            or started.seq < asked.seq
            or (
                finished is not None
                and finished.seq > started.seq
                and finished.data.get("status") == "completed"
            )
        ):
            raise NothingToReview("There's no unfinished review to run again.")
        self._starting.add(conversation.id)
        try:
            await self._make_room(keep=conversation.id)
            runtime = self._runtime(conversation)
            runtime.begin_turn()
            self._last_used[conversation.id] = time.monotonic()
            turn = self._store.count(conversation.id, "user_message")
            self._turns[conversation.id] = asyncio.create_task(
                self._rerun_review(conversation.id, runtime, asked, turn)
            )
        finally:
            self._starting.discard(conversation.id)

    async def _rerun_review(
        self, conversation_id: str, runtime: SessionRuntime, asked: Event, turn: int
    ) -> None:
        try:
            began = self._work_began(conversation_id, asked)
            await self._review(conversation_id, runtime, began.seq, str(began.data.get("text", "")))
            runtime.take_evidence()
            if runtime.ran_commands():
                label = f"After turn {turn}'s review"
                await self._checkpoint(conversation_id, runtime, turn, label)
        finally:
            if self._turns.get(conversation_id) is asyncio.current_task():
                del self._turns[conversation_id]
            self._store.append(conversation_id, "turn_done", {})

    def _last_turn_failed(self, conversation_id: str) -> bool:
        """Whether the chat offers Continue for the last turn: it failed, and not
        because of model trouble that waiting can't fix (a used-up allowance, a
        refused key, a bad request). The same rule as the chat's canContinue."""
        asked = self._store.last(conversation_id, "user_message")
        if asked is None:
            return False
        status = None
        trouble = None
        reviewing = False
        for event in self._store.all_events_after(conversation_id, asked.seq):
            if reviewing and event.type in _REVIEW_TURN_EVENTS:
                reviewing = event.type != "turn_finished"
                continue
            if event.type in _ACTIVITY:
                trouble = None
            if event.type in _OWN_TURN_EVENTS:
                return False
            if event.type == "review_started":
                reviewing = True
            elif event.type == "review_finished":
                reviewing = False
            elif event.type == "model_status":
                state = event.data.get("state")
                if state in ("retrying", "recovered", "failed"):
                    trouble = event.data.get("kind") if state == "failed" else None
            elif event.type == "turn_finished":
                status = event.data.get("status")
        return status == "failed" and trouble not in _NOT_CONTINUABLE

    def _work_began(self, conversation_id: str, asked: Event) -> Event:
        """The question a turn answers: a turn that Continue started picks up the
        one before it (which failed), and so on back."""
        messages = [
            e
            for e in self._store.events_of_types_after(conversation_id, 0, ("user_message",))
            if e.seq <= asked.seq
        ]
        began = asked
        for earlier in reversed(messages[:-1]):
            if not began.data.get("continues"):
                break
            began = earlier
        return began

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

    @contextlib.asynccontextmanager
    async def changing_inputs(self, conversation: Conversation) -> AsyncIterator[None]:
        """Hold the conversation while its attachments change.

        Nothing may start meanwhile, and the container must be confirmed gone
        before the change (mounts are fixed when a container starts), so a
        removed attachment can't stay mounted. The next message starts a new
        container and Codex picks the conversation up where it left off.
        """
        if self.is_busy(conversation.id):
            raise Busy("Wait for the agent to finish, or stop it, first.")
        self._restoring.add(conversation.id)
        try:
            await self._shutdown(conversation.id)
            await self._containers(conversation).stop_and_confirm()
            yield
        finally:
            self._restoring.discard(conversation.id)

    def answer_approval(
        self,
        conversation_id: str,
        approval_id: str,
        approved: bool,
        question: str = "",
        plan: dict[str, Any] | None = None,
        change_type: str | None = None,
    ) -> None:
        """The person's decision on a question or plan, recorded on the host."""
        pending = self._approvals.get(approval_id, conversation_id)
        kind = pending.kind if pending else ""
        value = self._approvals.answer(
            conversation_id, approval_id, approved, question, plan, change_type
        )
        answered: dict[str, Any] = {"id": approval_id, "approved": approved}
        if approved and kind == "research_helper":
            answered["question"] = value
        # A plan sent back: the type the person asked for instead, if they did.
        sent_back = kind == "analysis_plan" and not approved and value
        requested = json.loads(value).get("change_type") if sent_back else None
        if requested:
            answered["change_type"] = requested
            answered["change_type_label"] = TYPES_BY_ID[requested].label
        self._store.append(conversation_id, "approval_answered", answered)

    def watch_turn(self, session_id: str) -> Callable[[], bool]:
        """For a model request arriving now: a check, made by the relay before
        each attempt, that is true once this request's turn is over. That is,
        the person pressed Stop, or the conversation was shut down, reaped or
        deleted. Tied to this turn: a new turn clearing the Stop doesn't make
        an old request's check false again. Other sessions (research helpers,
        safety probes) have no turns here; revoking their token ends them."""
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return lambda: False
        turn = runtime.turn_number

        def over() -> bool:
            return (
                self._runtimes.get(session_id) is not runtime
                or runtime.turn_number != turn
                or runtime.stop_requested
            )

        return over

    async def stop(self, conversation_id: str) -> None:
        if self.helper is not None:
            self.helper.cancel(conversation_id)
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
            instance=instance_of(self._settings.data_dir),
            extra_mounts=(lambda: self._input_mounts(conversation_id))
            if self._attachments
            else None,
        )

    def _input_mounts(self, conversation_id: str) -> list[str]:
        """Mounts for the attachments that still pass every check, right now."""
        assert self._attachments is not None
        protected = [self._settings.data_dir, *(default_data_dir(p) for p in ("real", "practice"))]
        mountable = []
        for attachment in self._attachments.list(conversation_id):
            samples = practice_samples()
            if self._settings.profile == "practice" and is_sample(attachment, samples):
                mountable.append(attachment)
                continue
            problem = recheck(attachment, protected=protected)
            if problem is None:
                mountable.append(attachment)
            else:
                log.warning("not mounting an attachment in %s: %s", conversation_id, problem)
                self._store.append(
                    conversation_id,
                    "input_unavailable",
                    {"path": attachment.container_path, "reason": problem},
                )
        return mount_args(mountable)

    def _workspace_note(self, conversation_id: str) -> str:
        """Tell the agent what changed in its files since its last turn."""
        asked = self._store.last(conversation_id, "user_message")
        changes = self._store.events_of_types_after(
            conversation_id,
            asked.seq if asked else 0,
            ("files_restored", "input_attached", "input_removed", "input_unavailable"),
        )
        notes = [_note(event.type, event.data) for event in changes]
        return "".join(f"[DataLab: {note}]\n" for note in notes if note) + ("\n" if notes else "")

    async def _checkpoint(
        self, conversation_id: str, runtime: SessionRuntime, turn: int, label: str = ""
    ) -> None:
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
                asyncio.to_thread(checkpoints.take, label or f"After turn {turn}", turn=turn)
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
                approvals=self._approvals,
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
        started = self._store.last(conversation_id, "user_message")
        completed = False
        try:
            result = await runtime.send(text, effort=effort)
            completed = result.status == "completed"
            if not completed and result.error:
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
        try:
            # Still part of the turn, so nothing else can start until it's saved.
            await self._checkpoint(conversation_id, runtime, turn)
            if not completed:
                return
            since = started.seq if started else 0
            evidence = runtime.take_evidence()
            ran_commands = runtime.ran_commands()
            claims = await self._trace(conversation_id, since, evidence)
            conversation = self._store.get(conversation_id)
            # Reviewed only if there's something to review (work was done, or
            # the answer states numbers), and not if the person pressed Stop.
            wanted = conversation is not None and conversation.rigor_review
            if wanted and (ran_commands or evidence or claims) and not runtime.stop_requested:
                began = self._work_began(conversation_id, started) if started else None
                question = str(began.data.get("text", "")) if began else ""
                await self._review(conversation_id, runtime, began.seq if began else 0, question)
                runtime.take_evidence()
                if runtime.ran_commands():
                    # The review ran commands, which could have changed files.
                    label = f"After turn {turn}'s review"
                    await self._checkpoint(conversation_id, runtime, turn, label)
        finally:
            # The turn, with its checkpoint, trace, and review, is over: no
            # longer busy by the time the chat hears so.
            if self._turns.get(conversation_id) is asyncio.current_task():
                del self._turns[conversation_id]
            self._store.append(conversation_id, "turn_done", {})

    def _final_answer(
        self, conversation_id: str, since: int, until: int | None = None
    ) -> Event | None:
        events = self._store.all_events_after(conversation_id, since)
        if until is not None:
            events = [e for e in events if e.seq < until]
        answers = [e for e in events if e.type == "answer" and e.data.get("text")]
        final = [e for e in answers if e.data.get("phase") == "final_answer"] or answers
        return final[-1] if final else None

    async def _trace(self, conversation_id: str, since: int, evidence: list[str]) -> list:
        """Flag numbers in the turn's answer that nothing the turn produced contains."""
        try:
            answer = self._final_answer(conversation_id, since)
            if answer is None:
                return []

            def work() -> list:
                texts = evidence + self._output_data(conversation_id)
                return trace(str(answer.data["text"]), texts)

            # Can be slow on big outputs: not on the event loop.
            claims = await asyncio.to_thread(work)
        except Exception:
            log.exception("tracing failed in %s", conversation_id)
            return []
        if claims:
            self._store.append(
                conversation_id,
                "trace",
                {
                    "answer": answer.data.get("id"),
                    "numbers": len(claims),
                    "untraced": [c.text for c in claims if not c.traced],
                },
            )
        return claims

    def _output_data(self, conversation_id: str, budget: int = 2 * 1024**2) -> list[str]:
        """Data files in /work/outputs as of the latest checkpoint (not prose), up to a budget.

        Reports the agent wrote aren't evidence: they'd "trace" whatever they say.
        """
        checkpoints = self.checkpoints(conversation_id)
        latest = checkpoints.latest()
        if latest is None:
            return []
        texts: list[str] = []
        for rel, entry in checkpoints.entries(latest.number).items():
            if budget <= 0:
                break
            if not rel.startswith("outputs/") or not rel.lower().endswith((".csv", ".tsv")):
                continue
            with os.fdopen(checkpoints.open_object(entry), "rb") as source:
                data = source.read(min(budget, 1024**2))
            budget -= len(data)
            texts.append(data.decode("utf-8", "replace"))
        return texts

    async def _review(
        self, conversation_id: str, runtime: SessionRuntime, since: int, question: str
    ) -> None:
        """The rigor review: Codex's review mode, with DataLab's checklist.

        Review mode starts without the conversation's history, so what it
        needs is given to it: the question, the approved plans, the queries
        run, and the answer.
        """
        # The turn's own work: not an earlier review of it (when run again).
        events = self._store.all_events_after(conversation_id, since)
        first_review = next((e.seq for e in events if e.type == "review_started"), None)
        if first_review is not None:
            events = [e for e in events if e.seq < first_review]
        answer = self._final_answer(conversation_id, since, until=first_review)
        queries = [
            str((e.data.get("arguments") or {}).get("sql", ""))
            for e in events
            if e.type == "tool_call" and e.data.get("tool") == "query"
        ]
        plans, checks = self.plans.for_review(conversation_id) if self.plans else ([], [])
        context = rigor.instructions(
            answer=str(answer.data.get("text", "")) if answer else "",
            question=question,
            plans=plans,
            checks=checks,
            queries=[q for q in queries if q],
        )
        self._store.append(conversation_id, "review_started", {})
        status = "failed"
        try:
            result = await runtime.review(context)
            status = result.status
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception:
            log.exception("rigor review failed in %s", conversation_id)
        finally:
            # Always, so the chat never waits for a review that ended.
            self._store.append(conversation_id, "review_finished", {"status": status})

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

    def turn_running(self, conversation_id: str) -> bool:
        task = self._turns.get(conversation_id)
        return task is not None and not task.done()

    async def _shutdown(self, conversation_id: str) -> None:
        if self.helper is not None:
            self.helper.cancel(conversation_id)
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


def _note(kind: str, data: dict[str, Any]) -> str:
    if kind == "files_restored":
        label = str(data.get("label", "an earlier checkpoint")).lower()
        if data.get("failed"):
            return (
                f"the user tried to restore the files in /work to how they were {label}, but "
                "it failed partway, so some files may be restored and others not. Check the "
                "files before relying on them."
            )
        return (
            f"the user restored the files in /work to how they were {label}. Changes made "
            "to /work since then were undone. Check the files before relying on anything you "
            "remember about them."
        )
    items = data.get("items") or []
    # Names come from files, so they're quoted: they can't pose as DataLab's words.
    listed = ", ".join(f"{json.dumps(str(i.get('path')))} ({i.get('kind')})" for i in items)
    if kind == "input_attached":
        return f"the user attached {listed}, read-only (names are the files' own)."
    if kind == "input_removed":
        return f"the user removed {listed}; no longer available."
    if kind == "input_unavailable":
        path = str(data.get("path"))
        reason = str(data.get("reason", "")).replace(path, "it")
        return f"{json.dumps(path)} isn't available this time ({reason})."
    return ""
