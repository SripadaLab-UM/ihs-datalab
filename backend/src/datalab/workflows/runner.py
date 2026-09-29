"""The workflow runner: one host task per run, steps in order, no AI involved.

- **SQL steps** go through `DataService.run_query` with origin `run`, the
  run's `run_…` id as owner, and the workflow's declared `reads:` as the only
  tables it may read. The full result is kept in the run folder: it is the
  run's extracted input, and what a Replay reruns on.
- **R, pipeline, and custom QC steps** each run in a fresh no-network
  container (sandbox.py) with the step contract of the runner spike: a
  read-only `/run/step/step.json` and script, read-only inputs under
  `/run/in/<name>/`, and `/run/out/` and `/run/result/` to write. Only
  declared outputs come back, as regular files, with checksums.
- **Built-in QC** runs here, in DataLab's own code (qc.py). A failure stops
  the run: later steps are skipped and nothing is delivered.
- **Delivery** goes through `exports.export()` only after every step passed.

Every run and every step gets a new folder under `<data folder>/runs/`, and
nothing is deleted and recreated in place: Docker Desktop often can't mount
a folder recreated at the same path (spike, §1). A Replay copies the kept
inputs into its own new folder.

**Run again** runs the current workflow file afresh, with the original
parameters and seed, re-extracting from today's database. **Replay** reruns
the original definition on the original extracts, with the original image,
seed and pipeline library; before it starts it says what can't be pinned
(the image gone, another platform, DataLab's wrapper changed), and afterwards
whether every output matched byte for byte.

This file runs the steps. A step's files and result (the step contract) are
stepfiles.py's, delivery is delivery.py's, what Replay checks and compares is
replay.py's, and the plan they share is runstate.py's.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datalab import exports as exports  # tests patch exports.export through here
from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.data.oracle import QueryFailed
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from datalab.exports import DestinationStore
from datalab.workflows.delivery import Delivery

# Moved to delivery.py; still importable from here, where tests look.
from datalab.workflows.delivery import delivery_message as delivery_message
from datalab.workflows.delivery import delivery_title as delivery_title
from datalab.workflows.model import (
    ORACLE_INPUT,
    BuiltinQc,
    CustomQc,
    Pipeline,
    PipelineLookup,
    PipelineStep,
    Problem,
    QcStep,
    RStep,
    Scalar,
    SqlStep,
    Step,
    Workflow,
    WorkflowInvalid,
    bind_value,
    extract_sql,
    load_workflow,
    resolve_params,
    sql_binds,
    step_inputs,
    step_kind,
    step_outputs,
)
from datalab.workflows.qc import builtin_qc
from datalab.workflows.records import FINISHED, InputsGone, RunStore, now
from datalab.workflows.replay import (
    ReplayCheck,
    compare,
    kept_path,
    kept_pipelines,
    replay_check,
)
from datalab.workflows.runstate import Output, Plan, StepFailed
from datalab.workflows.sandbox import (
    RNG_KIND,
    RUNTIME_ENV,
    Bind,
    ContainerStep,
    ImageFacts,
    Sandbox,
    SandboxError,
    folder_bytes,
)
from datalab.workflows.source import (
    SourceError,
    WorkflowFile,
    WorkflowFolder,
    tree_sha256,
)
from datalab.workflows.stepfiles import (
    WRAPPER_NAME,
    collect,
    container_failure,
    copy_checked,
    copy_tree,
    file_facts,
    input_ref,
    lookup_output,
    package_name,
    read_result,
    remove_quietly,
    runner_version,
    spec_facts,
    wrapper_bytes,
)

log = logging.getLogger(__name__)

# A test run's `workflow_path`: a draft, not a file in the folder (see start_test).
DRAFT_PREFIX = "draft:"
# How many test runs of one draft are kept (the newest).
KEEP_TEST_RUNS = 3


class RunRefused(RuntimeError):
    """A run can't start (the message says why): nothing was recorded."""


class ReplayNotExact(RuntimeError):
    """A Replay can't pin everything; `reasons` say what. It may still run on request."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def step_seed(run_seed: int, step_id: str) -> int:
    """Each step's seed, from the run's: adding a step never changes another's."""
    return int(hashlib.sha256(f"{run_seed}:{step_id}".encode()).hexdigest()[:8], 16) % 2**31


def new_run_id() -> str:
    return f"run_{datetime.now(UTC):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"


class WorkflowRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RunStore,
        data: DataService,
        access_log: AccessLog,
        sandbox: Sandbox,
        folder: WorkflowFolder,
        destinations: DestinationStore,
        started_by: str | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.data = data
        self.access_log = access_log
        self.sandbox = sandbox
        self.folder = folder
        self.destinations = destinations
        self.started_by = started_by or who_is_running()
        self.runs_dir = settings.data_dir / "runs"
        self.cache_dir = settings.data_dir / "workflow-cache"
        self._slots = asyncio.Semaphore(settings.workflows.max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Runs whose delivery has started: Stop is refused, and a cancel
        # (DataLab shutting down) waits for it to finish and be recorded.
        self._delivering: set[str] = set()
        self._changed = asyncio.Event()
        # Runs a previous DataLab left going can't be resumed.
        self.store.mark_interrupted()

    @property
    def allowed_schemas(self) -> frozenset[str] | None:
        return self.settings.oracle.allowed_schemas if self.settings.oracle else None

    # --------------------------------------------------------- workflows

    def load(
        self, path: str, folder: WorkflowFolder | None = None
    ) -> tuple[WorkflowFile, Workflow]:
        """A workflow file from the folder (or a run's copy of it), checked.
        Raises WorkflowInvalid or SourceError."""
        folder = folder or self.folder
        file = folder.read(path)
        return file, self.check_text(file.text, folder=folder)

    def check_text(
        self,
        text: str,
        pipelines: PipelineLookup | None = None,
        *,
        folder: WorkflowFolder | None = None,
    ) -> Workflow:
        folder = folder or self.folder
        lookup = pipelines or folder.pipeline
        try:
            return load_workflow(
                text,
                pipelines=lookup,
                allowed_schemas=self.allowed_schemas,
                require_small_cells=self.settings.profile == "real",
            )
        except WorkflowInvalid as error:
            raise WorkflowInvalid(self._explain_pipelines(error.problems, text, folder)) from None

    def _explain_pipelines(
        self, problems: list[Problem], text: str, folder: WorkflowFolder
    ) -> list[Problem]:
        out = []
        explained: set[str] = set()
        for problem in problems:
            out.append(problem)
            if problem.pipeline is not None and problem.pipeline not in explained:
                explained.add(problem.pipeline)  # once per pipeline, however many steps
                for why in folder.pipeline_problems(problem.pipeline):
                    out.append(Problem(problem.path, f"pipeline.yaml: {why}"))
        return out

    # -------------------------------------------------------------- runs

    async def start(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        seed: int | None = None,
        set_id: str | None = None,
    ) -> str:
        run_id, folder = await self._pin(path)
        try:
            file, workflow = self.load(path, folder)
            values = resolve_params(workflow, params or {})
            plan = await self._plan(
                mode="run",
                workflow=workflow,
                file=file,
                params=values,
                seed=secrets.randbelow(2**31) if seed is None else seed,
                pipelines=folder.pipeline,
                set_id=set_id,
                run_id=run_id,
                folder=folder,
            )
            return self._launch(plan)
        except BaseException:
            # Not started: its copy of the files goes too.
            await asyncio.to_thread(shutil.rmtree, self.runs_dir / run_id, True)
            raise

    async def start_test(
        self, text: str, params: Mapping[str, Any] | None = None, *, seed: int | None = None
    ) -> str:
        """A test run of a draft that isn't saved: its text, checked as any
        file is, run as it would run, and never delivered. The record names it
        `draft:<name>`, so it's never taken for a saved file's run: Run again
        can't find it, and its Replay never delivers."""
        workflow = self.check_text(text)
        run_id = new_run_id()
        try:
            # The package as it is now, as for any run (the draft isn't a file here).
            folder = await asyncio.to_thread(
                self.folder.snapshot, self.runs_dir / run_id / "source"
            )
            workflow = self.check_text(text, folder=folder)
            data = text.encode("utf-8")
            file = WorkflowFile(
                path=f"{DRAFT_PREFIX}{workflow.name}",
                text=text,
                source="file",
                blob=f"sha256:{hashlib.sha256(data).hexdigest()}",
                commit=None,
            )
            plan = await self._plan(
                mode="run",
                workflow=workflow,
                file=file,
                params=resolve_params(workflow, params or {}),
                seed=secrets.randbelow(2**31) if seed is None else seed,
                pipelines=folder.pipeline,
                deliver=False,
                run_id=run_id,
                folder=folder,
            )
            plan.no_delivery = (
                "A test run doesn't deliver. Save the workflow, then run it to deliver."
            )
            launched = self._launch(plan)
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, self.runs_dir / run_id, True)
            raise
        await self.forget_tests(workflow.name, keep=KEEP_TEST_RUNS)
        return launched

    async def forget_tests(self, name: str, *, keep: int = 0) -> list[str]:
        """Remove a draft's test runs, but the newest `keep`: their folders and
        records (it was saved or discarded, or has had newer ones). A run still
        going is left until it finishes. Gives the ids removed."""
        runs = self.store.list_runs(f"{DRAFT_PREFIX}{name}", limit=1000)
        removed = []
        for run in runs[keep:]:
            if run["status"] in ("queued", "running") or self.running(run["id"]):
                continue
            await asyncio.to_thread(shutil.rmtree, self.runs_dir / run["id"], True)
            self.store.delete_run(run["id"])
            removed.append(run["id"])
        if removed:
            self._notify()
        return removed

    async def run_again(self, run_id: str) -> str:
        """The current workflow file afresh: new extracts, the original parameters and seed."""
        original = self._original(run_id)
        new_id, folder = await self._pin(original["workflow_path"])
        try:
            file, workflow = self.load(original["workflow_path"], folder)
            kept = {k: v for k, v in original["params"].items() if k in workflow.parameters}
            values = resolve_params(workflow, kept)
            plan = await self._plan(
                mode="run_again",
                workflow=workflow,
                file=file,
                params=values,
                seed=original["seed"],
                pipelines=folder.pipeline,
                original=original,
                run_id=new_id,
                folder=folder,
            )
            return self._launch(plan)
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, self.runs_dir / new_id, True)
            raise

    async def _pin(self, path: str) -> tuple[str, WorkflowFolder]:
        """A new run's id, and its own copy of its workflow file (`path`) and
        the package in its folder, which is all it reads from then on: a Sync
        or Save & share meanwhile can't change what it runs (source.py)."""
        run_id = new_run_id()
        try:
            folder = await asyncio.to_thread(
                self.folder.snapshot, self.runs_dir / run_id / "source", workflow=path
            )
        except BaseException:
            # A file that's missing, too large or not text: no run, and no folder.
            await asyncio.to_thread(shutil.rmtree, self.runs_dir / run_id, True)
            raise
        return run_id, folder

    async def replay_check(self, run_id: str) -> ReplayCheck:
        original = self._original(run_id)
        return await self._replay_check(original)

    async def replay(
        self, run_id: str, *, allow_inexact: bool = False, deliver: bool = False
    ) -> str:
        """Rerun on the original extracts with everything pinned.

        Raises ReplayNotExact unless every pin can be met or `allow_inexact`;
        RunRefused when it can't run at all (the extracts are gone, say).
        """
        original = self._original(run_id)
        if deliver and original["workflow_path"].startswith(DRAFT_PREFIX):
            raise RunRefused("A test run of a draft never delivers, and nor does its Replay.")
        check = await self._replay_check(original)
        if check.blocking:
            raise RunRefused(" ".join(check.blocking))
        if check.reasons and not allow_inexact:
            raise ReplayNotExact(check.reasons)
        pipelines = kept_pipelines(self.settings, original)
        workflow = self.check_text(original["workflow_text"], pipelines)
        file = WorkflowFile(
            path=original["workflow_path"],
            text=original["workflow_text"],
            source=original["workflow_source"],
            blob=original["workflow_blob"],
            commit=original["repo_commit"],
        )
        image = (
            await self._image_or_none(original["image_digest"])
            if original["image_digest"]
            else None
        )
        plan = await self._plan(
            mode="replay",
            workflow=workflow,
            file=file,
            params=dict(original["params"]),
            seed=original["seed"],
            pipelines=pipelines,
            original=original,
            image=image,
            deliver=deliver,
        )
        plan.replay_exact = not check.reasons
        plan.replay_notes = list(check.reasons)
        return self._launch(plan)

    async def stop(self, run_id: str) -> bool:
        """Stop a run. Refused (RunRefused) once its delivery has started:
        files may already be in the destination, and must be recorded."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        if run_id in self._delivering:
            raise RunRefused(
                "Delivery has started, so this run can't be stopped now. It finishes in a "
                "moment, and what was delivered is recorded."
            )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=30)
        return True

    async def close(self) -> None:
        """Stop every run (DataLab is shutting down). A delivery already
        under way finishes and is recorded first."""
        tasks = {run_id: t for run_id, t in self._tasks.items() if not t.done()}
        for task in tasks.values():
            task.cancel()
        for run_id, task in tasks.items():
            # A delivery under way has written, or is writing, files: however
            # long it takes, it finishes and is recorded before DataLab stops.
            delivering = run_id in self._delivering
            if delivering:
                log.warning("Waiting for run %s to finish its delivery before stopping.", run_id)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=None if delivering else 60)

    def running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)

    def detail(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if run is None:
            return None
        return {
            **run,
            "steps": self.store.steps(run_id),
            "deliveries": self.store.deliveries(run_id),
        }

    async def watch(self, run_id: str, heartbeat: float = 15) -> AsyncIterator[dict | None]:
        """The run as it changes; None as a heartbeat. Ends once the run has finished."""
        last: str | None = None
        while True:
            changed = self._changed
            snapshot = self.detail(run_id)
            if snapshot is None:
                return
            encoded = json.dumps(snapshot, sort_keys=True, default=str)
            if encoded != last:
                last = encoded
                yield snapshot
            if snapshot["status"] in FINISHED and not self.running(run_id):
                return
            try:
                await asyncio.wait_for(changed.wait(), timeout=heartbeat)
            except TimeoutError:
                yield None

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _original(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        run["steps"] = self.store.steps(run_id)
        return run

    # ------------------------------------------------------------ planning

    async def _plan(
        self,
        *,
        mode: str,
        workflow: Workflow,
        file: WorkflowFile,
        params: dict[str, Scalar],
        seed: int,
        pipelines: PipelineLookup,
        original: dict[str, Any] | None = None,
        set_id: str | None = None,
        image: ImageFacts | None = None,
        deliver: bool = True,
        run_id: str | None = None,
        folder: WorkflowFolder | None = None,
    ) -> Plan:
        needs_container = any(
            isinstance(s, RStep | PipelineStep) or (isinstance(s, QcStep) and s.custom)
            for s in workflow.steps
        )
        if image is None:
            image = await self._image_or_none(self.settings.agent_image)
        if needs_container and image is None:
            raise RunRefused(
                f"The agent image ({self.settings.agent_image}) isn't on this computer, and "
                "this workflow has R steps. Run the Safety check, or reinstall DataLab."
            )
        try:
            host = await self.sandbox.host_platform()
        except SandboxError:
            if needs_container:
                raise RunRefused(
                    "Docker isn't running. Start Docker Desktop and try again."
                ) from None
            host = ""
        return Plan(
            run_id=run_id or new_run_id(),
            mode=mode,
            workflow=workflow,
            file=file,
            params=params,
            seed=seed,
            image=image,
            host_platform=host,
            pipelines=pipelines,
            deliver=deliver,
            original=original,
            set_id=set_id,
            folder=folder,
        )

    async def _replay_check(self, original: dict[str, Any]) -> ReplayCheck:
        return await replay_check(
            original,
            settings=self.settings,
            sandbox=self.sandbox,
            image_or_none=self._image_or_none,
        )

    async def _image_or_none(self, ref: str) -> ImageFacts | None:
        try:
            return await self.sandbox.image(ref)
        except SandboxError:
            return None

    def _launch(self, plan: Plan) -> str:
        plan.run_dir = self.runs_dir / plan.run_id
        # Always new, never an old run's folder (a run's copy of its files is
        # already in it: see _pin).
        plan.run_dir.mkdir(parents=True, exist_ok=plan.folder is not None)
        pipelines = []
        for step in plan.workflow.steps:
            if isinstance(step, PipelineStep):
                pipelines.append({"name": step.pipeline, "step": step.id})
        try:
            self._record_new_run(plan, pipelines)
        except InputsGone:
            if plan.folder is not None:
                shutil.rmtree(plan.run_dir, ignore_errors=True)  # and its copy of the files
            with contextlib.suppress(OSError):
                plan.run_dir.rmdir()
            raise RunRefused("This run's extracted inputs have been removed.") from None
        task = asyncio.create_task(self._run(plan), name=f"workflow {plan.run_id}")
        self._tasks[plan.run_id] = task
        task.add_done_callback(lambda _: self._notify())
        self._notify()
        return plan.run_id

    def _record_new_run(self, plan: Plan, pipelines: list[dict[str, Any]]) -> None:
        workflow = plan.workflow
        image = plan.image
        self.store.create_run(
            {
                "id": plan.run_id,
                "workflow_name": workflow.name,
                "mode": plan.mode,
                "of_run": plan.original["id"] if plan.original else None,
                "set_id": plan.set_id,
                "status": "queued",
                "started_at": now(),
                "started_by": self.started_by,
                "workflow_path": plan.file.path,
                "workflow_source": plan.file.source,
                "repo_commit": plan.file.commit,
                "workflow_blob": plan.file.blob,
                "workflow_text": plan.file.text,
                "pipelines": pipelines,
                "image_ref": image.ref if image else self.settings.agent_image,
                "image_digest": image.digest if image else "",
                "image_platform": image.platform if image else "",
                "host_platform": plan.host_platform,
                "r_packages_sha256": image.r_packages_sha256 if image else "",
                "runner_version": runner_version(),
                "runtime": {
                    "env": RUNTIME_ENV,
                    "locale": "C.UTF-8 (the image's LC_ALL)",
                    "rng_kind": list(RNG_KIND),
                    "r_version": image.r_version if image else "",
                },
                "params": plan.params,
                "seed": plan.seed,
                "reads": sorted(workflow.read_objects),
                "run_dir": plan.run_dir.relative_to(self.settings.data_dir).as_posix(),
                "replay_exact": plan.replay_exact,
                "replay_notes": plan.replay_notes,
                "delivery_status": "pending" if workflow.deliver else "none",
            },
            [(s.id, i, step_kind(s)) for i, s in enumerate(workflow.steps)],
            # A Replay reads its original's kept inputs: they must still be there.
            needs_inputs_of=plan.original["id"]
            if plan.mode == "replay" and plan.original
            else None,
        )

    # ----------------------------------------------------------- running

    async def _run(self, plan: Plan) -> None:
        run_id = plan.run_id
        try:
            async with self._slots:
                self.store.update_run(run_id, status="running")
                self._notify()
                await self._execute(plan)
        except asyncio.CancelledError:
            self._finish_cancelled(plan)
            await asyncio.shield(self.sandbox.remove_run(run_id))
        except Exception as error:  # a bug, or the disk: the run fails, DataLab goes on
            log.exception("Workflow run %s failed", run_id)
            self._skip_rest(run_id, "failed")
            self.store.update_run(
                run_id,
                status="failed",
                finished_at=now(),
                message=f"DataLab hit an error: {type(error).__name__}.",
                delivery_status="skipped" if plan.workflow.deliver else "none",
            )
            with contextlib.suppress(Exception):
                await self.sandbox.remove_run(run_id)
        finally:
            with contextlib.suppress(Exception):
                self._write_record(plan)
            self._notify()

    def _finish_cancelled(self, plan: Plan) -> None:
        self._skip_rest(plan.run_id, "cancelled")
        run = self.store.get_run(plan.run_id) or {}
        fields: dict[str, Any] = {}
        # Never overwrite a delivery that happened (or failed) with "skipped".
        if run.get("delivery_status") == "pending":
            fields = {"delivery_status": "skipped", "delivery_message": "The run was stopped."}
        self.store.update_run(
            plan.run_id, status="cancelled", finished_at=now(), message="Stopped.", **fields
        )

    def _skip_rest(self, run_id: str, running_as: str) -> None:
        for step in self.store.steps(run_id):
            if step["status"] == "running":
                self.store.update_step(
                    run_id, step["step_id"], status=running_as, finished_at=now()
                )
            elif step["status"] == "pending":
                self.store.update_step(run_id, step["step_id"], status="skipped")

    async def _execute(self, plan: Plan) -> None:
        run_dir = plan.run_dir
        (run_dir / "workflow.yaml").write_text(plan.file.text, encoding="utf-8", newline="")
        (run_dir / "datalab").mkdir()
        (run_dir / "datalab" / WRAPPER_NAME).write_bytes(wrapper_bytes())
        plan.budget = self.settings.workflows.max_run_bytes
        done: dict[str, dict[str, Output]] = {}
        failed: str | None = None
        for step in plan.workflow.steps:
            if failed is not None:
                self.store.update_step(plan.run_id, step.id, status="skipped")
                continue
            self.store.update_step(plan.run_id, step.id, status="running", started_at=now())
            self._notify()
            started = asyncio.get_running_loop().time()
            step_dir = run_dir / "steps" / step.id
            step_dir.mkdir(parents=True)
            try:
                problem = self._disk_problem(plan)
                if problem:
                    raise StepFailed(problem)
                fields, outputs = await self._step(plan, step, step_dir, done)
                status = fields.pop("status", "succeeded")
            except StepFailed as failure:
                fields, outputs, status = failure.fields, {}, "failed"
                fields["message"] = failure.message
            fields.setdefault(
                "elapsed_ms", round((asyncio.get_running_loop().time() - started) * 1000)
            )
            self.store.update_step(plan.run_id, step.id, status=status, finished_at=now(), **fields)
            self._notify()
            done[step.id] = outputs
            if status != "succeeded":
                failed = step.id

        status = "failed" if failed else "succeeded"
        message = f"Step {failed} failed, so the rest didn't run." if failed else None
        self.store.update_run(plan.run_id, status=status, message=message)
        if plan.mode == "replay" and plan.original is not None:
            same, notes = compare(plan.original, self.store.steps(plan.run_id))
            self.store.update_run(
                plan.run_id, reproduced=same, replay_notes=[*plan.replay_notes, *notes]
            )
        await self._deliver_shielded(plan, done, failed)
        self.store.update_run(plan.run_id, finished_at=now())

    async def _deliver_shielded(
        self, plan: Plan, done: dict[str, dict[str, Output]], failed: str | None
    ) -> None:
        """Deliver, and don't let a cancel cut it short.

        `export()` runs in a thread that cancelling can't stop, so files may
        already be in the destination: the delivery always runs to its end
        and is recorded (its row, and the audit log's export entry).
        """
        self._delivering.add(plan.run_id)
        delivery = asyncio.ensure_future(self._deliver(plan, done, failed))
        interrupted = False
        try:
            while not delivery.done():
                try:
                    await asyncio.shield(delivery)
                except asyncio.CancelledError:
                    interrupted = True
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
            delivery.result()
        finally:
            self._delivering.discard(plan.run_id)
        if interrupted:
            run = self.store.get_run(plan.run_id) or {}
            self.store.update_run(
                plan.run_id,
                delivery_message=(
                    f"{run.get('delivery_message') or ''} DataLab was asked to stop during "
                    "delivery, which finished first."
                ).strip(),
            )

    def _disk_problem(self, plan: Plan) -> str | None:
        free = shutil.disk_usage(plan.run_dir).free
        if free < self.settings.limits.min_free_disk_bytes:
            return f"Only {free // 1024**2} MB of disk is free; free some space and run it again."
        used = folder_bytes(plan.run_dir)
        if used > plan.budget:
            return f"The run folder is over its cap ({plan.budget // 1024**2} MB)."
        return None

    async def _step(
        self, plan: Plan, step: Step, step_dir: Path, done: dict[str, dict[str, Output]]
    ) -> tuple[dict[str, Any], dict[str, Output]]:
        if isinstance(step, SqlStep):
            return await self._sql_step(plan, step, step_dir)
        if isinstance(step, QcStep) and isinstance(step.qc, BuiltinQc):
            return await self._builtin_qc(plan, step, step.qc, done)
        return await self._container_step(plan, step, step_dir, done)

    # SQL -------------------------------------------------------------

    async def _sql_step(
        self, plan: Plan, step: SqlStep, step_dir: Path
    ) -> tuple[dict[str, Any], dict[str, Output]]:
        out_dir = step_dir / "outputs"
        out_dir.mkdir()
        target = out_dir / step.output
        binds = {
            name: bind_value(plan.params[_param_name(plan, name)]) for name in sql_binds(step.sql)
        }
        fields: dict[str, Any] = {"sql_text": step.sql, "binds": binds}
        if plan.mode == "replay":
            old = self._original_step(plan, step.id)
            kept = old["outputs"]["final"]
            await asyncio.to_thread(self._copy_kept, plan, step.id, kept, target)
            fields["query_id"] = old.get("query_id")
        else:
            fields["query_id"] = await self._query(
                plan, step.sql, binds, step_dir / "query", target
            )
        facts = await asyncio.to_thread(file_facts, target)
        output = Output(step.id, "final", step.output, out_dir, facts)
        fields["outputs"] = {"final": {"file": step.output, **facts}}
        return fields, {"final": output}

    async def _query(
        self, plan: Plan, sql: str, binds: dict[str, Any], results: Path, target: Path
    ) -> str:
        try:
            outcome = await self.data.run_query(
                session_id=plan.run_id,
                sql=sql,
                binds=binds,
                results_dir=results,
                origin="run",
                allowed_tables=plan.workflow.read_objects,
            )
        except SqlRejected as error:
            raise StepFailed(f"The data service refused the query: {error}") from None
        except QueryFailed as error:
            raise StepFailed(f"The query failed: {error}") from None
        os.replace(outcome.result_path, target)
        with contextlib.suppress(OSError):
            results.rmdir()
        return outcome.query_id

    def _original_step(self, plan: Plan, step_id: str) -> dict[str, Any]:
        assert plan.original is not None
        found = next((s for s in plan.original["steps"] if s["step_id"] == step_id), None)
        if found is None or found["status"] != "succeeded":
            raise StepFailed("The original run has no kept result for this step.")
        return found

    def _copy_kept(self, plan: Plan, step_id: str, kept: dict[str, Any], target: Path) -> None:
        """Copy one of the original run's kept files, checked against its recorded sha256."""
        assert plan.original is not None
        source = kept_path(self.settings, plan.original, step_id, kept)
        copy_checked(source, target, kept["sha256"])

    # Built-in QC ---------------------------------------------------------

    async def _builtin_qc(
        self, plan: Plan, step: QcStep, qc: BuiltinQc, done: dict[str, dict[str, Output]]
    ) -> tuple[dict[str, Any], dict[str, Output]]:
        output = lookup_output(qc.file, done)
        checks = await asyncio.to_thread(builtin_qc, qc, output.path, plan.params)
        failed = [c for c in checks if c["status"] != "pass"]
        fields: dict[str, Any] = {
            "inputs": {"file": input_ref(output)},
            "result": {"status": "failed" if failed else "ok", "checks": checks},
        }
        if failed:
            fields["status"] = "failed"
            fields["message"] = "Failed: " + ", ".join(
                f"{c['id']} ({c['observed']})" if isinstance(c["observed"], int) else c["id"]
                for c in failed
            )
        return fields, {}

    # Containers ----------------------------------------------------------

    async def _container_step(
        self, plan: Plan, step: Step, step_dir: Path, done: dict[str, dict[str, Output]]
    ) -> tuple[dict[str, Any], dict[str, Output]]:
        assert plan.image is not None
        spec_dir, scratch, result_dir, out_dir = (
            step_dir / n for n in ("spec", "scratch", "result", "outputs")
        )
        for folder in (spec_dir, scratch, result_dir, out_dir):
            folder.mkdir()
        seed = step_seed(plan.seed, step.id)
        outputs = step_outputs(step, plan.pipelines)
        inputs: dict[str, Output] = {}
        for name, ref in step_inputs(step).items():
            inputs[name] = lookup_output(ref, done)
        binds = [Bind(plan.run_dir / "datalab", "/run/datalab"), Bind(spec_dir, "/run/step")]
        for name, output in inputs.items():
            binds.append(Bind(output.folder, f"/run/in/{name}"))
        spec_inputs = {
            name: {"path": f"/run/in/{name}/{o.file}", **spec_facts(o.facts)}
            for name, o in inputs.items()
        }
        fields: dict[str, Any] = {
            "seed": seed,
            "inputs": {name: input_ref(o) for name, o in inputs.items()},
        }
        env: dict[str, str] = {}
        if isinstance(step, PipelineStep):
            pipeline = plan.pipelines(step.pipeline)
            if pipeline is None:
                raise StepFailed(f"The pipeline {step.pipeline} isn't there any more.")
            script = pipeline.script
            extracts, queries = await self._extracts(plan, step, pipeline, step_dir)
            binds.append(Bind(step_dir / "extracts", f"/run/in/{ORACLE_INPUT}"))
            for obj, output in extracts.items():
                spec_inputs[obj] = {
                    "path": f"/run/in/{ORACLE_INPUT}/{output.file}",
                    **spec_facts(output.facts),
                }
                fields["inputs"][obj] = {**input_ref(output), "folder": "extracts"}
            fields["queries"] = queries
            library = await self._library(plan, step)
            binds.append(Bind(library, "/opt/ihs/lib"))
            env["R_LIBS"] = "/opt/ihs/lib"
            self._keep_pipeline(plan, pipeline)
        elif isinstance(step, RStep):
            script = step.r
        else:
            assert isinstance(step, QcStep) and isinstance(step.qc, CustomQc)
            script = step.qc.r
        binds += [
            Bind(scratch, "/run/out", readonly=False),
            Bind(result_dir, "/run/result", readonly=False),
        ]
        spec = {
            "step": step.id,
            "type": step_kind(step),
            "seed": seed,
            "params": plan.params,
            "inputs": spec_inputs,
            "outputs": {name: f"/run/out/{file}" for name, file in outputs.items()},
        }
        (spec_dir / "step.json").write_text(
            json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8"
        )
        (spec_dir / "script.R").write_text(script, encoding="utf-8", newline="")

        budget = plan.budget - folder_bytes(plan.run_dir)
        outcome = await self.sandbox.run(
            ContainerStep(
                run_id=plan.run_id,
                name=step.id,
                image=plan.image.digest,
                binds=binds,
                # The whole step folder: /run/out and /run/result are both writable.
                watch=step_dir,
                max_bytes=max(budget, 0) + folder_bytes(step_dir),
                env=env,
            )
        )
        (step_dir / "log.txt").write_bytes(outcome.log)
        result = read_result(result_dir / "result.json")
        collected, missing, dropped = await asyncio.to_thread(
            collect, scratch, out_dir, outputs, max(budget, 0)
        )
        await asyncio.to_thread(remove_quietly, scratch)
        fields.update(
            exit_code=outcome.exit_code,
            result=result,
            outputs={name: {"file": o.file, **o.facts} for name, o in collected.items()},
        )
        for output in collected.values():
            output.step = step.id
        ok = (
            outcome.exit_code == 0
            and result.get("status") == "ok"
            and not missing
            and not outcome.timed_out
            and not outcome.over_cap
        )
        if not ok:
            fields["status"] = "failed"
            fields["message"] = container_failure(outcome, result, missing)
        elif dropped:
            fields["message"] = f"{dropped} undeclared files were left behind."
        return fields, collected

    async def _extracts(
        self, plan: Plan, step: PipelineStep, pipeline: Pipeline, step_dir: Path
    ) -> tuple[dict[str, Output], list[dict[str, Any]]]:
        """Extract the pipeline's declared objects through the data service (or,
        for a Replay, copy the original's), into the step's extracts folder."""
        folder = step_dir / "extracts"
        folder.mkdir()
        extracts: dict[str, Output] = {}
        queries: list[dict[str, Any]] = []
        original = self._original_step(plan, step.id) if plan.mode == "replay" else None
        for entry in pipeline.spec.reads:
            file = f"{entry.object}.csv"
            target = folder / file
            sql = extract_sql(entry)
            binds = {n: bind_value(plan.params[_param_name(plan, n)]) for n in sql_binds(sql)}
            if original is not None:
                kept = original["inputs"].get(entry.object)
                if kept is None:
                    raise StepFailed("The original run has no kept extract for this step.")
                await asyncio.to_thread(self._copy_kept, plan, step.id, kept, target)
                previous = next(
                    (q for q in original["queries"] if q.get("object") == entry.object), {}
                )
                query_id = previous.get("query_id")
            else:
                query_id = await self._query(plan, sql, binds, step_dir / "query", target)
            facts = await asyncio.to_thread(file_facts, target)
            extracts[entry.object] = Output(step.id, entry.object, file, folder, facts)
            queries.append(
                {"object": entry.object, "query_id": query_id, "sql": sql, "binds": binds}
            )
        return extracts, queries

    def _keep_pipeline(self, plan: Plan, pipeline: Pipeline) -> None:
        """A copy of the pipeline's definition in the run folder, for Replay."""
        folder = plan.run_dir / "pipelines" / pipeline.name
        if folder.exists():
            return
        folder.mkdir(parents=True)
        (folder / "pipeline.yaml").write_text(
            pipeline.spec.model_dump_json(indent=2), encoding="utf-8"
        )
        (folder / "run.R").write_text(pipeline.script, encoding="utf-8", newline="")

    async def _library(self, plan: Plan, step: PipelineStep) -> Path:
        """The pipelines package, built once per source tree in the sandbox.

        The source is copied into the run folder first, so the build and the
        record are of exactly what was copied, and Replay can rebuild it.
        """
        assert plan.image is not None
        source_copy = plan.run_dir / "package-src"
        if plan.mode == "replay" and plan.original is not None:
            original_copy = self.settings.data_dir / plan.original["run_dir"] / "package-src"
            if not source_copy.exists():
                await asyncio.to_thread(copy_tree, original_copy, source_copy)
        elif not source_copy.exists():
            try:
                package = (plan.folder or self.folder).package()
            except SourceError as error:
                raise StepFailed(str(error)) from None
            await asyncio.to_thread(copy_tree, package.root, source_copy)
        tree = await asyncio.to_thread(tree_sha256, source_copy)
        name = package_name(source_copy)
        cache = self.cache_dir / "libs" / f"{tree[:32]}-{plan.image.digest.split(':')[-1][:12]}"
        if not (cache / "library.json").is_file():
            await self._build(plan, source_copy, name, tree, cache)
        info = json.loads((cache / "library.json").read_text(encoding="utf-8"))
        record = {
            "name": step.pipeline,
            "step": step.id,
            "package": name,
            "tree_sha256": tree,
            "library_sha256": info["library_sha256"],
            "library": cache.relative_to(self.settings.data_dir).as_posix(),
        }
        plan.libraries[step.id] = record
        self.store.update_run(plan.run_id, pipelines=list(plan.libraries.values()))
        return cache / "lib"

    async def _build(self, plan: Plan, source: Path, name: str, tree: str, cache: Path) -> None:
        assert plan.image is not None
        staging = cache.with_name(f"{cache.name}-{secrets.token_hex(3)}")
        (staging / "lib").mkdir(parents=True)
        outcome = await self.sandbox.run(
            ContainerStep(
                run_id=plan.run_id,
                name=f"build-{secrets.token_hex(2)}",
                image=plan.image.digest,
                binds=[
                    Bind(source, f"/run/src/{name}"),
                    Bind(staging / "lib", "/run/lib", readonly=False),
                ],
                watch=staging,
                max_bytes=2 * 1024**3,
                command=(
                    "sh", "-c",
                    f"cp -r /run/src/{name} /tmp/pkg && "
                    "R CMD INSTALL --no-docs --no-test-load --library=/run/lib /tmp/pkg",
                ),
            )
        )  # fmt: skip
        if outcome.exit_code != 0:
            (plan.run_dir / f"build-{name}.log").write_bytes(outcome.log)
            raise StepFailed(f"The {name} package didn't build (exit {outcome.exit_code}).")
        library_sha = await asyncio.to_thread(tree_sha256, staging / "lib")
        (staging / "library.json").write_text(
            json.dumps({"package": name, "tree_sha256": tree, "library_sha256": library_sha}),
            encoding="utf-8",
        )
        try:
            staging.rename(cache)
        except OSError:
            # Another run built it first; use theirs.
            await asyncio.to_thread(remove_quietly, staging)

    # Delivery ------------------------------------------------------------

    async def _deliver(
        self, plan: Plan, done: dict[str, dict[str, Output]], failed: str | None
    ) -> None:
        """Deliver the run's files (delivery.py), or record why it didn't."""
        await Delivery(
            self.settings, self.store, self.access_log, self.destinations, self.started_by
        ).deliver(plan, done, failed)

    # Record --------------------------------------------------------------

    def _write_record(self, plan: Plan) -> None:
        detail = self.detail(plan.run_id)
        if detail is None or not plan.run_dir.is_dir():
            return
        detail["record_version"] = 1
        with open(plan.run_dir / "record.json", "w", encoding="utf-8") as out:
            out.write(json.dumps(detail, indent=2, sort_keys=True, default=str) + "\n")


# ---------------------------------------------------------------- helpers


def _param_name(plan: Plan, bind: str) -> str:
    """The parameter a bind names (Oracle binds ignore case)."""
    for name in plan.params:
        if name.lower() == bind.lower():
            return name
    raise StepFailed(f"No parameter for :{bind}.")


def who_is_running() -> str:
    """The person's git name and email, as their commits have it, else their login."""
    parts = []
    for key in ("user.name", "user.email"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            done = subprocess.run(
                ["git", "config", "--global", "--get", key],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if done.returncode == 0 and done.stdout.strip():
                parts.append(done.stdout.strip())
    if len(parts) == 2:
        return f"{parts[0]} <{parts[1]}>"
    with contextlib.suppress(Exception):
        return getpass.getuser()
    return "unknown"
