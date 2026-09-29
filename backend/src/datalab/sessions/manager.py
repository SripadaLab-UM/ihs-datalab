"""Keeps track of every running conversation.

Turns run as background tasks, so closing the browser never stops one. Idle
conversations have their containers stopped after a while; the next message
starts them again and Codex resumes the same thread.

This is the turns' lifecycle: starting, stopping, continuing and ending them,
and holding a conversation while its files or attachments change. What a turn
gets, and what the log says about turns, is turns.py; a turn's number check
and rigor review, review.py; checkpoints and restores of /work, workfiles.py;
the container's extra mounts, mounts.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

from datalab.config import Settings
from datalab.sessions import modes, mounts, seeds, turns, workfiles
from datalab.sessions.approvals import Approvals
from datalab.sessions.checkpoints import CheckpointMissing, Checkpoints, RestoreResult
from datalab.sessions.containers import SessionContainers, SessionPaths, instance_of
from datalab.sessions.helper import ResearchHelper
from datalab.sessions.hooks import AfterTurnHook, MountProvider, TurnInfo, WorkspaceSeed
from datalab.sessions.inputs import AttachmentStore
from datalab.sessions.plan_schema import TYPES_BY_ID
from datalab.sessions.plans import PlanStore
from datalab.sessions.review import review_turn, trace_turn
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.seeds import Seed
from datalab.sessions.store import Conversation, ConversationStore, Event
from datalab.sessions.tokens import SessionTokens
from datalab.sessions.tracing import Source

log = logging.getLogger(__name__)


# What Continue sends: the same thread picks up where a turn stopped part-way.
CONTINUE_TEXT = (
    "Please continue where you left off: the last turn was interrupted before it finished."
)


class NothingToReview(RuntimeError):
    """There's no unfinished rigor review to run again."""


class NothingToContinue(RuntimeError):
    """The last turn didn't fail, so Continue has nothing to pick up."""


# How long a turn's after-turn hooks may take, together, before the turn
# ends without them.
AFTER_TURN_SECONDS = 30


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
        # Extension points for later milestones (see hooks.py).
        self._after_turn: list[AfterTurnHook] = []
        self._seeds: list[Seed] = []
        self._mount_providers: list[tuple[MountProvider, tuple[Path, ...]]] = []
        # Hooks still running after their turn ended (kept so they aren't
        # garbage-collected mid-run).
        self._detached: set[asyncio.Task[None]] = set()

    def register_after_turn(self, hook: AfterTurnHook) -> None:
        """Run `await hook(conversation_id, turn_info)` after every turn,
        once its checkpoint, number check and review are done."""
        self._after_turn.append(hook)

    def turn_sources(self, conversation_id: str) -> list[tuple[Source, str]]:
        """What the conversation's last turn produced, with where each piece came
        from (for provenance), or [] with no runtime. Read-only, in memory only."""
        runtime = self._runtimes.get(conversation_id)
        return runtime.turn_sources() if runtime is not None else []

    def register_workspace_seed(
        self,
        name: str,
        seed: WorkspaceSeed,
        *,
        into: str,
        modes: Sequence[str] | None = None,
    ) -> None:
        """Before each new conversation's first turn, run `seed(conversation,
        staging)` and move what it wrote to `/work/<into>`, once (seeds.py);
        only in conversations of `modes`, if given."""
        if any(s.name == name or s.into == into for s in self._seeds):
            raise ValueError(f"A workspace seed for {name!r} or /work/{into} already exists.")
        self._seeds.append(Seed(name, seed, into, tuple(modes) if modes is not None else None))

    def workspace_base(self, conversation_id: str, name: str) -> str | None:
        """What the seed `name` returned for this conversation, if it's in place."""
        return seeds.base(self.paths(conversation_id), name)

    def seeds_into(self, into: str, mode: str) -> bool:
        """Whether a seed puts `/work/<into>` in this mode's new conversations."""
        return any(s.into == into and (s.modes is None or mode in s.modes) for s in self._seeds)

    def seeded_folders(self, conversation_id: str) -> list[str]:
        """The /work folders DataLab copied in for this conversation (seeds in place)."""
        done = seeds.records(self.paths(conversation_id))
        return [s.into for s in self._seeds if (done.get(s.name) or {}).get("status") == "done"]

    def register_mounts(self, provider: MountProvider, *, roots: Sequence[Path]) -> None:
        """Add `provider(conversation)`'s read-only mounts to each container
        the conversation starts from now on. Their sources must be inside
        `roots`, folders the provider owns (such as a repo clone)."""
        mounts.check_mount_roots(roots, self._settings)
        self._mount_providers.append((provider, tuple(roots)))

    def paths(self, conversation_id: str) -> SessionPaths:
        return SessionPaths(self._settings.data_dir / "sessions" / conversation_id)

    def checkpoints(self, conversation_id: str) -> Checkpoints:
        paths = self.paths(conversation_id)
        return Checkpoints(paths.checkpoints, paths.work)

    def any_busy(self) -> bool:
        """Whether any conversation's agent is working, starting, or restoring."""
        if self._restoring or self._starting:
            return True
        return any(not task.done() for task in self._turns.values())

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
            if self._seeds:
                await asyncio.to_thread(self._seed_workspace, conversation)
            runtime = self._runtime(conversation)
            runtime.begin_turn()
            self._last_used[conversation.id] = time.monotonic()
            # Express and the rigor review as they are now, for the whole of
            # this turn: a switch during it applies from the next message.
            express = conversation.express
            # Always sent: a turn's effort may stay with the thread in Codex,
            # so an Express turn's low effort mustn't outlive it.
            effort = effort or turns.default_effort(self._store, conversation, continues)
            self._tokens.set_express(conversation.id, express)
            note = turns.express_note(self._store, conversation) + turns.workspace_note(
                self._store, conversation.id
            )
            self._store.append(
                conversation.id,
                "user_message",
                {
                    "text": text,
                    **({"continues": True} if continues else {}),
                    **({"express": True} if express else {}),
                    "rigor_review": conversation.rigor_review,
                    "effort": effort,
                },
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
        asked = turns.unfinished_review(self._store, conversation.id)
        if asked is None:
            raise NothingToReview("There's no unfinished review to run again.")
        self._starting.add(conversation.id)
        try:
            await self._make_room(keep=conversation.id)
            runtime = self._runtime(conversation)
            runtime.begin_turn()
            self._last_used[conversation.id] = time.monotonic()
            # The reviewed turn's own Express state, as its question recorded it.
            self._tokens.set_express(conversation.id, bool(asked.data.get("express")))
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
            checkpoint = None
            if runtime.ran_commands():
                label = f"After turn {turn}'s review"
                checkpoint = await self._checkpoint(
                    conversation_id, runtime, turn, label, review=True
                )
            info = TurnInfo(turn, "completed", began.seq, checkpoint, review_only=True)
            await self._run_after_turn(conversation_id, info)
        finally:
            if self._turns.get(conversation_id) is asyncio.current_task():
                del self._turns[conversation_id]
            self._store.append(conversation_id, "turn_done", {})

    def _last_turn_failed(self, conversation_id: str) -> bool:
        """Whether the chat offers Continue for the last turn (turns.py)."""
        return turns.last_turn_failed(self._store, conversation_id)

    def _work_began(self, conversation_id: str, asked: Event) -> Event:
        """The question a turn answers (turns.py: work_began)."""
        return turns.work_began(self._store, conversation_id, asked)

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
            result = await workfiles.restore_files(
                self._store, checkpoints, conversation.id, number, target
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
        if approved and kind == "analysis_plan":
            # The plan as approved (with the person's edits), for the card until it's frozen.
            answered["plan"] = json.loads(value)
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
        self._tokens.set_express(conversation_id, False)
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

    def end_cut_off_turns(self) -> None:
        """At startup: end the turns DataLab stopped in the middle of (it
        crashed, or its computer did), so the chat doesn't show them running."""
        for conversation in self._store.list():
            if self._end_cut_off_turn(conversation.id):
                log.info("ended a turn cut off by a restart in %s", conversation.id)

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
            extra_mounts=lambda: self._extra_mounts(conversation_id),
        )

    def _extra_mounts(self, conversation_id: str) -> list[str]:
        """Mounts beyond DataLab's own: attachments, then what providers add."""
        return mounts.extra_mounts(
            self._store, self._settings, self._attachments, self._mount_providers, conversation_id
        )

    def _seed_workspace(self, conversation: Conversation) -> None:
        """Run the seeds that haven't run for this conversation (on a worker
        thread). Only before its first turn: after that, /work is the agent's."""
        seeds.run_seeds(
            self._seeds,
            conversation,
            self.paths(conversation.id),
            had_turn=self._store.count(conversation.id, "user_message") > 0,
            notice=lambda text: self._store.append(conversation.id, "notice", {"text": text}),
        )

    def _input_mounts(self, conversation_id: str) -> list[str]:
        """Mounts for the attachments that still pass every check, right now."""
        assert self._attachments is not None
        return mounts.input_mounts(self._store, self._settings, self._attachments, conversation_id)

    async def _checkpoint(
        self,
        conversation_id: str,
        runtime: SessionRuntime,
        turn: int,
        label: str = "",
        *,
        review: bool = False,
    ) -> int | None:
        """Checkpoint /work after a turn, with the container frozen meanwhile.
        Its number, or None if it couldn't be saved (workfiles.py)."""
        return await workfiles.checkpoint_after_turn(
            self._store,
            self.checkpoints(conversation_id),
            runtime.containers,
            self._settings,
            self.paths(conversation_id).root,
            conversation_id,
            turn,
            label,
            review=review,
        )

    def _runtime(self, conversation: Conversation) -> SessionRuntime:
        runtime = self._runtimes.get(conversation.id)
        if runtime is None:
            paths = self.paths(conversation.id)

            async def emit(kind: str, data: dict[str, Any]) -> None:
                self._store.append(conversation.id, kind, data)

            mode = modes.MODES[conversation.mode]
            runtime = SessionRuntime(
                conversation.id,
                conversation.kind,
                paths,
                self._containers(conversation),
                self._tokens,
                model=conversation.model,
                developer_instructions=modes.instructions(
                    conversation.mode, knowledge_base=self.seeds_into("kb", conversation.mode)
                ),
                tool_timeout_seconds=int(self._settings.limits.deadline_seconds) + 60,
                emit=emit,
                approvals=self._approvals,
                tools=mode.allowed_tools if mode.kind == "data" else mode.tools,
                tools_off=mode.tools_off,
                mode_label=mode.label,
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
        since = started.seq if started else 0
        completed = False
        status = "failed"
        try:
            result = await runtime.send(text, effort=effort)
            status = result.status
            completed = result.status == "completed"
            if not completed and result.error:
                self._store.append(conversation_id, "error", {"message": result.error})
        except asyncio.CancelledError:
            # DataLab is closing: the turn ends here, and the log says so.
            if self._turns.get(conversation_id) is asyncio.current_task():
                del self._turns[conversation_id]
            self._end_cut_off_turn(conversation_id)
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
            checkpoint = await self._checkpoint(conversation_id, runtime, turn)
            if completed:
                checkpoint = await self._trace_and_review(
                    conversation_id, runtime, started, turn, checkpoint
                )
            info = TurnInfo(turn, turns.turn_status(status), since, checkpoint)
            await self._run_after_turn(conversation_id, info)
        finally:
            # The turn, with its checkpoint, trace, and review, is over: no
            # longer busy by the time the chat hears so.
            if self._turns.get(conversation_id) is asyncio.current_task():
                del self._turns[conversation_id]
            self._store.append(conversation_id, "turn_done", {})

    async def _trace_and_review(
        self,
        conversation_id: str,
        runtime: SessionRuntime,
        started: Event | None,
        turn: int,
        checkpoint: int | None,
    ) -> int | None:
        """A completed turn's number check, then its rigor review if wanted.
        The checkpoint of /work afterwards."""
        since = started.seq if started else 0
        evidence = runtime.take_evidence()
        ran_commands = runtime.ran_commands()
        claims = await self._trace(conversation_id, since, evidence)
        # Reviewed only if the review was on when the turn began (its question
        # records it: switching Express on meanwhile doesn't cancel it, and
        # switching the review on during an Express turn doesn't add one), if
        # there's something to review (work was done, or the answer states
        # numbers), and not if the person pressed Stop.
        wanted = turns.review_wanted(self._store, conversation_id, started)
        if wanted and (ran_commands or evidence or claims) and not runtime.stop_requested:
            began = self._work_began(conversation_id, started) if started else None
            question = str(began.data.get("text", "")) if began else ""
            await self._review(conversation_id, runtime, began.seq if began else 0, question)
            runtime.take_evidence()
            if runtime.ran_commands():
                # The review ran commands, which could have changed files.
                label = f"After turn {turn}'s review"
                checkpoint = await self._checkpoint(
                    conversation_id, runtime, turn, label, review=True
                )
        return checkpoint

    async def _run_after_turn(self, conversation_id: str, info: TurnInfo) -> None:
        """The after-turn hooks, all at once, within one time limit. One that
        fails is logged; one still going then is cancelled and left to end on
        its own (never awaited), so the turn always ends. If the turn itself
        is cancelled (DataLab closing), so are its hooks."""
        if not self._after_turn:
            return
        tasks = [asyncio.ensure_future(hook(conversation_id, info)) for hook in self._after_turn]
        try:
            _, late = await asyncio.wait(tasks, timeout=AFTER_TURN_SECONDS)
        except asyncio.CancelledError:
            for task in tasks:
                self._detach(task, conversation_id)
            raise
        for task in tasks:
            if task in late:
                log.error("an after-turn hook took too long in %s; left to end", conversation_id)
                self._detach(task, conversation_id)
            elif not task.cancelled() and task.exception() is not None:
                log.error(
                    "an after-turn hook failed in %s",
                    conversation_id,
                    exc_info=task.exception(),
                )

    def _detach(self, task: asyncio.Task[None], conversation_id: str) -> None:
        """Cancel a hook without waiting for it; log how it ends."""
        if task.done():
            return
        task.cancel()
        self._detached.add(task)

        def ended(done: asyncio.Task[None]) -> None:
            self._detached.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log.error(
                    "an after-turn hook failed in %s", conversation_id, exc_info=done.exception()
                )

        task.add_done_callback(ended)

    def _end_cut_off_turn(self, conversation_id: str) -> bool:
        """End a turn the log shows still going (turns.py: end_cut_off_turn)."""
        return turns.end_cut_off_turn(self._store, conversation_id)

    async def _trace(self, conversation_id: str, since: int, evidence: list[str]) -> list:
        """Flag numbers in the turn's answer that nothing the turn produced contains."""
        return await trace_turn(
            self._store, self.checkpoints(conversation_id), conversation_id, since, evidence
        )

    async def _review(
        self, conversation_id: str, runtime: SessionRuntime, since: int, question: str
    ) -> None:
        """The rigor review (review.py)."""
        await review_turn(self._store, self.plans, runtime, conversation_id, since, question)

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

    def current_turn(self, conversation_id: str) -> object | None:
        """The running turn (the same object only within one turn), or None."""
        task = self._turns.get(conversation_id)
        return task if task is not None and not task.done() else None

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
