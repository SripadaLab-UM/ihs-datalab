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
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import getpass
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from datalab import __version__, exports
from datalab.config import Settings, default_data_dir
from datalab.data.access_log import AccessLog
from datalab.data.oracle import QueryFailed
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from datalab.exports import DestinationStore, ExportError, ExportSource
from datalab.sessions.inputs import NotAttachable, check_attachable
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
    load_pipeline_file,
    load_workflow,
    resolve_params,
    resolve_ref,
    sql_binds,
    step_inputs,
    step_kind,
    step_outputs,
)
from datalab.workflows.qc import builtin_qc
from datalab.workflows.records import FINISHED, RunStore, now
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

log = logging.getLogger(__name__)

MAX_RESULT_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 500
MAX_MESSAGES = 50
WRAPPER_NAME = "run_step.R"


class RunRefused(RuntimeError):
    """A run can't start (the message says why): nothing was recorded."""


class ReplayNotExact(RuntimeError):
    """A Replay can't pin everything; `reasons` say what. It may still run on request."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class ReplayCheck:
    exact: bool
    reasons: list[str]  # why it wouldn't be exact
    blocking: list[str]  # why it can't run at all


@dataclass
class _Output:
    step: str
    name: str
    file: str
    folder: Path
    facts: dict[str, Any]

    @property
    def path(self) -> Path:
        return self.folder / self.file


@dataclass
class _Plan:
    run_id: str
    mode: str
    workflow: Workflow
    file: WorkflowFile
    params: dict[str, Scalar]
    seed: int
    image: ImageFacts | None
    host_platform: str
    pipelines: PipelineLookup
    deliver: bool = True
    original: dict[str, Any] | None = None
    set_id: str | None = None
    replay_exact: bool | None = None
    replay_notes: list[str] = field(default_factory=list)
    run_dir: Path = Path()
    budget: int = 0
    libraries: dict[str, dict[str, Any]] = field(default_factory=dict)


def wrapper_bytes() -> bytes:
    return resources.files(__package__).joinpath(WRAPPER_NAME).read_bytes()


def wrapper_sha256() -> str:
    return hashlib.sha256(wrapper_bytes()).hexdigest()


def runner_version() -> str:
    return f"datalab {__version__}; {WRAPPER_NAME} sha256:{wrapper_sha256()}"


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
        self._changed = asyncio.Event()
        # Runs a previous DataLab left going can't be resumed.
        self.store.mark_interrupted()

    @property
    def allowed_schemas(self) -> frozenset[str] | None:
        return self.settings.oracle.allowed_schemas if self.settings.oracle else None

    # --------------------------------------------------------- workflows

    def load(self, path: str) -> tuple[WorkflowFile, Workflow]:
        """A workflow file from the folder, checked. Raises WorkflowInvalid or SourceError."""
        file = self.folder.read(path)
        return file, self.check_text(file.text)

    def check_text(self, text: str, pipelines: PipelineLookup | None = None) -> Workflow:
        lookup = pipelines or self.folder.pipeline
        try:
            return load_workflow(text, pipelines=lookup, allowed_schemas=self.allowed_schemas)
        except WorkflowInvalid as error:
            raise WorkflowInvalid(self._explain_pipelines(error.problems, text)) from None

    def _explain_pipelines(self, problems: list[Problem], text: str) -> list[Problem]:
        out = []
        for problem in problems:
            out.append(problem)
            if problem.message.startswith("There's no pipeline "):
                name = problem.message.removeprefix("There's no pipeline ").strip("'.")
                for why in self.folder.pipeline_problems(name):
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
        file, workflow = self.load(path)
        values = resolve_params(workflow, params or {})
        plan = await self._plan(
            mode="run",
            workflow=workflow,
            file=file,
            params=values,
            seed=secrets.randbelow(2**31) if seed is None else seed,
            pipelines=self.folder.pipeline,
            set_id=set_id,
        )
        return self._launch(plan)

    async def run_again(self, run_id: str) -> str:
        """The current workflow file afresh: new extracts, the original parameters and seed."""
        original = self._original(run_id)
        file, workflow = self.load(original["workflow_path"])
        kept = {k: v for k, v in original["params"].items() if k in workflow.parameters}
        values = resolve_params(workflow, kept)
        plan = await self._plan(
            mode="run_again",
            workflow=workflow,
            file=file,
            params=values,
            seed=original["seed"],
            pipelines=self.folder.pipeline,
            original=original,
        )
        return self._launch(plan)

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
        check = await self._replay_check(original)
        if check.blocking:
            raise RunRefused(" ".join(check.blocking))
        if check.reasons and not allow_inexact:
            raise ReplayNotExact(check.reasons)
        pipelines = self._kept_pipelines(original)
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
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=30)
        return True

    async def close(self) -> None:
        """Stop every run (DataLab is shutting down)."""
        for run_id in list(self._tasks):
            await self.stop(run_id)

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
    ) -> _Plan:
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
        return _Plan(
            run_id=new_run_id(),
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
        )

    async def _image_or_none(self, ref: str) -> ImageFacts | None:
        try:
            return await self.sandbox.image(ref)
        except SandboxError:
            return None

    def _launch(self, plan: _Plan) -> str:
        plan.run_dir = self.runs_dir / plan.run_id
        plan.run_dir.mkdir(parents=True)  # always new: never an old run's folder
        workflow = plan.workflow
        image = plan.image
        pipelines = []
        for step in workflow.steps:
            if isinstance(step, PipelineStep):
                pipelines.append({"name": step.pipeline, "step": step.id})
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
        )
        task = asyncio.create_task(self._run(plan), name=f"workflow {plan.run_id}")
        self._tasks[plan.run_id] = task
        task.add_done_callback(lambda _: self._notify())
        self._notify()
        return plan.run_id

    # ----------------------------------------------------------- running

    async def _run(self, plan: _Plan) -> None:
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

    def _finish_cancelled(self, plan: _Plan) -> None:
        self._skip_rest(plan.run_id, "cancelled")
        self.store.update_run(
            plan.run_id,
            status="cancelled",
            finished_at=now(),
            message="Stopped.",
            delivery_status="skipped" if plan.workflow.deliver else "none",
            delivery_message="The run was stopped." if plan.workflow.deliver else None,
        )

    def _skip_rest(self, run_id: str, running_as: str) -> None:
        for step in self.store.steps(run_id):
            if step["status"] == "running":
                self.store.update_step(
                    run_id, step["step_id"], status=running_as, finished_at=now()
                )
            elif step["status"] == "pending":
                self.store.update_step(run_id, step["step_id"], status="skipped")

    async def _execute(self, plan: _Plan) -> None:
        run_dir = plan.run_dir
        (run_dir / "workflow.yaml").write_text(plan.file.text, encoding="utf-8", newline="")
        (run_dir / "datalab").mkdir()
        (run_dir / "datalab" / WRAPPER_NAME).write_bytes(wrapper_bytes())
        plan.budget = self.settings.workflows.max_run_bytes
        done: dict[str, dict[str, _Output]] = {}
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
                    raise _StepFailed(problem)
                fields, outputs = await self._step(plan, step, step_dir, done)
                status = fields.pop("status", "succeeded")
            except _StepFailed as failure:
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
            same, notes = self._compare(plan.original, plan.run_id)
            self.store.update_run(
                plan.run_id, reproduced=same, replay_notes=[*plan.replay_notes, *notes]
            )
        await self._deliver(plan, done, failed)
        self.store.update_run(plan.run_id, finished_at=now())

    def _disk_problem(self, plan: _Plan) -> str | None:
        free = shutil.disk_usage(plan.run_dir).free
        if free < self.settings.limits.min_free_disk_bytes:
            return f"Only {free // 1024**2} MB of disk is free; free some space and run it again."
        used = folder_bytes(plan.run_dir)
        if used > plan.budget:
            return f"The run folder is over its cap ({plan.budget // 1024**2} MB)."
        return None

    async def _step(
        self, plan: _Plan, step: Step, step_dir: Path, done: dict[str, dict[str, _Output]]
    ) -> tuple[dict[str, Any], dict[str, _Output]]:
        if isinstance(step, SqlStep):
            return await self._sql_step(plan, step, step_dir)
        if isinstance(step, QcStep) and isinstance(step.qc, BuiltinQc):
            return await self._builtin_qc(plan, step, step.qc, done)
        return await self._container_step(plan, step, step_dir, done)

    # SQL -------------------------------------------------------------

    async def _sql_step(
        self, plan: _Plan, step: SqlStep, step_dir: Path
    ) -> tuple[dict[str, Any], dict[str, _Output]]:
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
        output = _Output(step.id, "final", step.output, out_dir, facts)
        fields["outputs"] = {"final": {"file": step.output, **facts}}
        return fields, {"final": output}

    async def _query(
        self, plan: _Plan, sql: str, binds: dict[str, Any], results: Path, target: Path
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
            raise _StepFailed(f"The data service refused the query: {error}") from None
        except QueryFailed as error:
            raise _StepFailed(f"The query failed: {error}") from None
        os.replace(outcome.result_path, target)
        with contextlib.suppress(OSError):
            results.rmdir()
        return outcome.query_id

    def _original_step(self, plan: _Plan, step_id: str) -> dict[str, Any]:
        assert plan.original is not None
        found = next((s for s in plan.original["steps"] if s["step_id"] == step_id), None)
        if found is None or found["status"] != "succeeded":
            raise _StepFailed("The original run has no kept result for this step.")
        return found

    def _copy_kept(self, plan: _Plan, step_id: str, kept: dict[str, Any], target: Path) -> None:
        """Copy one of the original run's kept files, checked against its recorded sha256."""
        assert plan.original is not None
        source = self._kept_path(plan.original, step_id, kept)
        _copy_checked(source, target, kept["sha256"])

    def _kept_path(self, original: dict[str, Any], step_id: str, kept: dict[str, Any]) -> Path:
        folder = kept.get("folder", "outputs")
        return (
            self.settings.data_dir / original["run_dir"] / "steps" / step_id / folder / kept["file"]
        )

    # Built-in QC ---------------------------------------------------------

    async def _builtin_qc(
        self, plan: _Plan, step: QcStep, qc: BuiltinQc, done: dict[str, dict[str, _Output]]
    ) -> tuple[dict[str, Any], dict[str, _Output]]:
        output = _lookup(qc.file, done)
        checks = await asyncio.to_thread(builtin_qc, qc, output.path, plan.params)
        failed = [c for c in checks if c["status"] != "pass"]
        fields: dict[str, Any] = {
            "inputs": {"file": _input_ref(output)},
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
        self, plan: _Plan, step: Step, step_dir: Path, done: dict[str, dict[str, _Output]]
    ) -> tuple[dict[str, Any], dict[str, _Output]]:
        assert plan.image is not None
        spec_dir, scratch, result_dir, out_dir = (
            step_dir / n for n in ("spec", "scratch", "result", "outputs")
        )
        for folder in (spec_dir, scratch, result_dir, out_dir):
            folder.mkdir()
        seed = step_seed(plan.seed, step.id)
        outputs = step_outputs(step, plan.pipelines)
        inputs: dict[str, _Output] = {}
        for name, ref in step_inputs(step).items():
            inputs[name] = _lookup(ref, done)
        binds = [Bind(plan.run_dir / "datalab", "/run/datalab"), Bind(spec_dir, "/run/step")]
        for name, output in inputs.items():
            binds.append(Bind(output.folder, f"/run/in/{name}"))
        spec_inputs = {
            name: {"path": f"/run/in/{name}/{o.file}", **_spec_facts(o.facts)}
            for name, o in inputs.items()
        }
        fields: dict[str, Any] = {
            "seed": seed,
            "inputs": {name: _input_ref(o) for name, o in inputs.items()},
        }
        env: dict[str, str] = {}
        if isinstance(step, PipelineStep):
            pipeline = plan.pipelines(step.pipeline)
            if pipeline is None:
                raise _StepFailed(f"The pipeline {step.pipeline} isn't there any more.")
            script = pipeline.script
            extracts, queries = await self._extracts(plan, step, pipeline, step_dir)
            binds.append(Bind(step_dir / "extracts", f"/run/in/{ORACLE_INPUT}"))
            for obj, output in extracts.items():
                spec_inputs[obj] = {
                    "path": f"/run/in/{ORACLE_INPUT}/{output.file}",
                    **_spec_facts(output.facts),
                }
                fields["inputs"][obj] = {**_input_ref(output), "folder": "extracts"}
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
        (spec_dir / "step.json").write_text(json.dumps(spec, indent=2, sort_keys=True))
        (spec_dir / "script.R").write_text(script, encoding="utf-8", newline="")

        budget = plan.budget - folder_bytes(plan.run_dir)
        outcome = await self.sandbox.run(
            ContainerStep(
                run_id=plan.run_id,
                name=step.id,
                image=plan.image.digest,
                binds=binds,
                watch=scratch,
                max_bytes=max(budget, 0),
                env=env,
            )
        )
        (step_dir / "log.txt").write_bytes(outcome.log)
        result = _read_result(result_dir / "result.json")
        collected, missing, dropped = await asyncio.to_thread(
            _collect, scratch, out_dir, outputs, max(budget, 0)
        )
        await asyncio.to_thread(_remove_quietly, scratch)
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
            fields["message"] = _container_failure(outcome, result, missing)
        elif dropped:
            fields["message"] = f"{dropped} undeclared files were left behind."
        return fields, collected

    async def _extracts(
        self, plan: _Plan, step: PipelineStep, pipeline: Pipeline, step_dir: Path
    ) -> tuple[dict[str, _Output], list[dict[str, Any]]]:
        """Extract the pipeline's declared objects through the data service (or,
        for a Replay, copy the original's), into the step's extracts folder."""
        folder = step_dir / "extracts"
        folder.mkdir()
        extracts: dict[str, _Output] = {}
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
                    raise _StepFailed("The original run has no kept extract for this step.")
                await asyncio.to_thread(self._copy_kept, plan, step.id, kept, target)
                previous = next(
                    (q for q in original["queries"] if q.get("object") == entry.object), {}
                )
                query_id = previous.get("query_id")
            else:
                query_id = await self._query(plan, sql, binds, step_dir / "query", target)
            facts = await asyncio.to_thread(file_facts, target)
            extracts[entry.object] = _Output(step.id, entry.object, file, folder, facts)
            queries.append(
                {"object": entry.object, "query_id": query_id, "sql": sql, "binds": binds}
            )
        return extracts, queries

    def _keep_pipeline(self, plan: _Plan, pipeline: Pipeline) -> None:
        """A copy of the pipeline's definition in the run folder, for Replay."""
        folder = plan.run_dir / "pipelines" / pipeline.name
        if folder.exists():
            return
        folder.mkdir(parents=True)
        (folder / "pipeline.yaml").write_text(
            pipeline.spec.model_dump_json(indent=2), encoding="utf-8"
        )
        (folder / "run.R").write_text(pipeline.script, encoding="utf-8", newline="")

    def _kept_pipelines(self, original: dict[str, Any]) -> PipelineLookup:
        root = self.settings.data_dir / original["run_dir"] / "pipelines"

        def lookup(name: str) -> Pipeline | None:
            folder = root / name
            try:
                spec = load_pipeline_file((folder / "pipeline.yaml").read_text())
                return Pipeline(name, spec, (folder / "run.R").read_text())
            except (OSError, WorkflowInvalid):
                return None

        return lookup

    async def _library(self, plan: _Plan, step: PipelineStep) -> Path:
        """The pipelines package, built once per source tree in the sandbox.

        The source is copied into the run folder first, so the build and the
        record are of exactly what was copied, and Replay can rebuild it.
        """
        assert plan.image is not None
        source_copy = plan.run_dir / "package-src"
        if plan.mode == "replay" and plan.original is not None:
            original_copy = self.settings.data_dir / plan.original["run_dir"] / "package-src"
            if not source_copy.exists():
                await asyncio.to_thread(_copy_tree, original_copy, source_copy)
        elif not source_copy.exists():
            try:
                package = self.folder.package()
            except SourceError as error:
                raise _StepFailed(str(error)) from None
            await asyncio.to_thread(_copy_tree, package.root, source_copy)
        tree = await asyncio.to_thread(tree_sha256, source_copy)
        name = _package_name(source_copy)
        cache = self.cache_dir / "libs" / f"{tree[:32]}-{plan.image.digest.split(':')[-1][:12]}"
        if not (cache / "library.json").is_file():
            await self._build(plan, source_copy, name, tree, cache)
        info = json.loads((cache / "library.json").read_text())
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

    async def _build(self, plan: _Plan, source: Path, name: str, tree: str, cache: Path) -> None:
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
            raise _StepFailed(f"The {name} package didn't build (exit {outcome.exit_code}).")
        library_sha = await asyncio.to_thread(tree_sha256, staging / "lib")
        (staging / "library.json").write_text(
            json.dumps({"package": name, "tree_sha256": tree, "library_sha256": library_sha})
        )
        try:
            staging.rename(cache)
        except OSError:
            # Another run built it first; use theirs.
            await asyncio.to_thread(_remove_quietly, staging)

    # Delivery ------------------------------------------------------------

    async def _deliver(
        self, plan: _Plan, done: dict[str, dict[str, _Output]], failed: str | None
    ) -> None:
        spec = plan.workflow.deliver
        if spec is None:
            return
        if failed:
            self.store.update_run(
                plan.run_id,
                delivery_status="skipped",
                delivery_message="A step failed, so nothing was delivered.",
            )
            return
        if not plan.deliver:
            self.store.update_run(
                plan.run_id,
                delivery_status="skipped",
                delivery_message="Replays don't deliver unless asked to.",
            )
            return
        try:
            destination_id, folder = self._destination(spec.destination)
            target = folder / exports.safe_name(spec.folder)
            target.mkdir(exist_ok=True)
            if os.path.realpath(target) != os.path.join(os.path.realpath(folder), target.name):
                raise ExportError("The delivery folder points somewhere else.")
            sources = []
            chosen = []
            for ref in spec.files:
                output = _lookup(ref, done)
                chosen.append(output)
                sources.append(
                    ExportSource(
                        open=lambda p=output.path: os.open(
                            p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        ),
                        path=output.file,
                        container_path=f"{plan.run_id}/{output.step}/{output.file}",
                    )
                )
            about = self._manifest_about(plan, chosen)
            result = await asyncio.to_thread(
                exports.export,
                target,
                title=plan.workflow.name,
                tag=plan.run_id,
                sources=sources,
                about=about,
            )
        except ExportError as error:
            self.store.update_run(
                plan.run_id, delivery_status="failed", delivery_message=str(error)
            )
            return
        except OSError as error:
            log.warning("Delivery for %s failed: %s", plan.run_id, error)
            self.store.update_run(
                plan.run_id,
                delivery_status="failed",
                delivery_message="DataLab couldn't write to the export folder.",
            )
            return
        manifest = result.folder / exports.MANIFEST
        written = json.loads(manifest.read_text())["files"]
        self.store.add_delivery(
            {
                "id": f"dl_{secrets.token_hex(6)}",
                "run_id": plan.run_id,
                "destination_key": spec.destination,
                "destination_id": destination_id,
                "destination_path": str(folder),
                "folder": str(result.folder),
                "files": [{k: f[k] for k in ("path", "bytes", "sha256")} for f in written],
                "manifest_sha256": sha256_file(manifest),
                "delivered_at": now(),
            }
        )
        self.access_log.record_export(
            session_id=plan.run_id,
            destination=spec.destination,
            files=len(result.files),
            contains_study_data=self.settings.profile == "real",
        )
        self.store.update_run(
            plan.run_id,
            delivery_status="delivered",
            delivery_message=f"{len(result.files)} files delivered.",
        )

    def _destination(self, key: str) -> tuple[str | None, Path]:
        """The folder a destination key names on this computer.

        The practice profile delivers only to its own practice folder, so
        nothing from it can end up somewhere real.
        """
        if self.settings.profile == "practice":
            folder = self.settings.data_dir / "practice-exports"
            folder.mkdir(parents=True, exist_ok=True)
            return None, folder
        destination = self.destinations.by_key(key)
        if destination is None:
            raise ExportError(
                f"No export folder is set for {key!r} on this computer. Choose one in Settings."
            )
        folder = Path(destination.path)
        if os.path.realpath(folder) != str(folder):
            raise ExportError("That export folder now points somewhere else. Add it again.")
        protected = [self.settings.data_dir, default_data_dir("real"), default_data_dir("practice")]
        try:
            check_attachable(folder, protected=protected)
        except NotAttachable as error:
            raise ExportError(f"DataLab can't deliver there any more: {error}") from error
        return destination.id, folder

    def _manifest_about(self, plan: _Plan, chosen: list[_Output]) -> dict[str, Any]:
        run = self.store.get_run(plan.run_id) or {}
        steps = self.store.steps(plan.run_id)
        return {
            "profile": self.settings.profile,
            "contains_study_data": self.settings.profile == "real",
            "workflow": {
                "name": plan.workflow.name,
                "path": plan.file.path,
                "source": plan.file.source,
                "blob": plan.file.blob,
                "commit": plan.file.commit,
            },
            "run": {
                "id": plan.run_id,
                "mode": plan.mode,
                "of_run": run.get("of_run"),
                "params": plan.params,
                "seed": plan.seed,
                "image": run.get("image_digest"),
                "started_by": self.started_by,
            },
            "qc": [
                {
                    "step": s["step_id"],
                    "status": s["status"],
                    "checks": [
                        {
                            "id": c.get("id"),
                            "status": c.get("status"),
                            "observed": c.get("observed"),
                        }
                        for c in ((s.get("result") or {}).get("checks") or [])
                    ],
                }
                for s in steps
                if s["kind"].startswith("qc")
            ],
            "outputs": [
                {"step": o.step, "file": o.file, "sha256": o.facts.get("sha256")} for o in chosen
            ],
        }

    # Replay --------------------------------------------------------------

    async def _replay_check(self, original: dict[str, Any]) -> ReplayCheck:
        reasons: list[str] = []
        blocking: list[str] = []
        if original["status"] != "succeeded":
            blocking.append("Only a run that finished can be replayed.")
        if not original["inputs_kept"]:
            blocking.append("This run's extracted inputs have been removed.")
        for step in original["steps"]:
            if step["kind"] == "sql" and step["status"] == "succeeded":
                kept = step["outputs"]["final"]
                if not _matches(self._kept_path(original, step["step_id"], kept), kept["sha256"]):
                    blocking.append(
                        f"The kept extract for {step['step_id']} is missing or changed."
                    )
            if step["kind"] == "pipeline" and step["status"] == "succeeded":
                for obj, kept in step["inputs"].items():
                    if kept.get("folder") == "extracts" and not _matches(
                        self._kept_path(original, step["step_id"], kept), kept["sha256"]
                    ):
                        blocking.append(f"The kept extract {obj} for {step['step_id']} is missing.")
        text_blob = original["workflow_text"].encode()
        if original["workflow_source"] == "file" and original["workflow_blob"] != (
            "sha256:" + hashlib.sha256(text_blob).hexdigest()
        ):
            blocking.append("The kept workflow file doesn't match its record.")
        uses_containers = any(
            s["kind"] in ("r", "pipeline", "qc_custom") for s in original["steps"]
        )
        if uses_containers:
            image = await self._image_or_none(original["image_digest"])
            if image is None:
                reasons.append(
                    f"The agent image this run used ({original['image_digest'][:19]}…) isn't on "
                    "this computer, so the current image would be used."
                )
            try:
                host = await self.sandbox.host_platform()
            except SandboxError:
                blocking.append("Docker isn't running.")
                host = original["host_platform"]
            if host != original["host_platform"]:
                reasons.append(
                    f"This computer runs Docker on {host}; the run was on "
                    f"{original['host_platform']}. Results can differ in the last digits."
                )
            if not original["runner_version"].endswith(f"sha256:{wrapper_sha256()}"):
                reasons.append("DataLab's step wrapper has changed since this run.")
            for pipeline in original["pipelines"]:
                library = self.settings.data_dir / pipeline.get("library", "-") / "library.json"
                with contextlib.suppress(OSError, ValueError):
                    if json.loads(library.read_text())["library_sha256"] == pipeline.get(
                        "library_sha256"
                    ):
                        continue
                reasons.append(
                    f"The {pipeline['name']} pipeline's library would be rebuilt from the kept "
                    "source, so its build may differ."
                )
        if not original["runner_version"].startswith(f"datalab {__version__};"):
            reasons.append("DataLab's version has changed since this run; its checks may differ.")
        return ReplayCheck(exact=not reasons and not blocking, reasons=reasons, blocking=blocking)

    def _compare(self, original: dict[str, Any], run_id: str) -> tuple[bool, list[str]]:
        """Whether every output and every check matched the original's, and what didn't."""
        mine = {s["step_id"]: s for s in self.store.steps(run_id)}
        notes = []
        for old in original["steps"]:
            new = mine.get(old["step_id"], {})
            for name, out in (old.get("outputs") or {}).items():
                theirs = (new.get("outputs") or {}).get(name) or {}
                if theirs.get("sha256") != out.get("sha256"):
                    notes.append(
                        f"Step {old['step_id']}'s {out['file']} differs from the original."
                    )
            old_checks = (old.get("result") or {}).get("checks")
            if old_checks is not None and old_checks != (new.get("result") or {}).get("checks"):
                notes.append(f"Step {old['step_id']}'s checks differ from the original.")
            if old["status"] != new.get("status"):
                notes.append(
                    f"Step {old['step_id']} {new.get('status')}; it {old['status']} before."
                )
        return not notes, notes

    # Record --------------------------------------------------------------

    def _write_record(self, plan: _Plan) -> None:
        detail = self.detail(plan.run_id)
        if detail is None or not plan.run_dir.is_dir():
            return
        detail["record_version"] = 1
        with open(plan.run_dir / "record.json", "w", encoding="utf-8") as out:
            out.write(json.dumps(detail, indent=2, sort_keys=True, default=str) + "\n")


class _StepFailed(Exception):
    def __init__(self, message: str, fields: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}


# ---------------------------------------------------------------- helpers


def _param_name(plan: _Plan, bind: str) -> str:
    """The parameter a bind names (Oracle binds ignore case)."""
    for name in plan.params:
        if name.lower() == bind.lower():
            return name
    raise _StepFailed(f"No parameter for :{bind}.")


def _lookup(ref: str, done: Mapping[str, Mapping[str, _Output]]) -> _Output:
    names = {step: dict.fromkeys(outputs, "") for step, outputs in done.items()}
    resolved = resolve_ref(ref, names)
    if isinstance(resolved, str):
        raise _StepFailed(resolved)
    return done[resolved[0]][resolved[1]]


def _input_ref(output: _Output) -> dict[str, Any]:
    return {
        "step": output.step,
        "output": output.name,
        "file": output.file,
        "sha256": output.facts.get("sha256"),
    }


def _spec_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    return {k: facts[k] for k in ("sha256", "bytes", "rows", "columns") if k in facts}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_facts(path: Path) -> dict[str, Any]:
    """Checksum and size, and for a CSV its row count and header. No values."""
    facts: dict[str, Any] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if path.suffix.lower() == ".csv":
        with (
            contextlib.suppress(UnicodeDecodeError, csv.Error),
            path.open(newline="", encoding="utf-8") as handle,
        ):
            reader = csv.reader(handle)
            header = next(reader, [])
            facts["rows"] = sum(1 for _ in reader)
            facts["columns"] = header
    return facts


def _matches(path: Path, sha256: str) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and sha256_file(path) == sha256
    except OSError:
        return False


def _copy_checked(source: Path, target: Path, sha256: str) -> None:
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as reader, open(target, "xb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    if digest.hexdigest() != sha256:
        target.unlink(missing_ok=True)
        raise _StepFailed(f"The kept input {source.name} doesn't match its recorded checksum.")


def _read_result(path: Path) -> dict[str, Any]:
    """result.json, capped and checked; its messages are the step's, shown as data."""
    failed: dict[str, Any] = {"status": "failed", "messages": [], "checks": []}
    try:
        info = os.lstat(path)
    except OSError:
        return {**failed, "messages": [{"level": "error", "text": "The step wrote no result."}]}
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
        return {
            **failed,
            "messages": [{"level": "error", "text": "The step's result was refused."}],
        }
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            result = json.loads(handle.read(MAX_RESULT_BYTES))
    except (OSError, ValueError):
        return {**failed, "messages": [{"level": "error", "text": "The step's result isn't JSON."}]}
    if not isinstance(result, dict):
        return failed
    result = dict(result)
    raw_messages, raw_checks = result.get("messages"), result.get("checks")
    messages: list[Any] = raw_messages if isinstance(raw_messages, list) else []
    checks: list[Any] = raw_checks if isinstance(raw_checks, list) else []
    return {
        "status": result.get("status") if result.get("status") in ("ok", "failed") else "failed",
        "counts": result.get("counts") if isinstance(result.get("counts"), dict) else {},
        "messages": [_capped_message(m) for m in messages[:MAX_MESSAGES]],
        "checks": [_capped_check(c) for c in checks[:100] if isinstance(c, dict)],
        "r_version": str(result.get("r_version", ""))[:100],
        "rng_kind": result.get("rng_kind") if isinstance(result.get("rng_kind"), list) else [],
    }


def _capped_message(message: Any) -> dict[str, str]:
    if not isinstance(message, dict):
        return {"level": "info", "text": str(message)[:MAX_MESSAGE_CHARS]}
    return {
        "level": str(message.get("level", "info"))[:10],
        "text": str(message.get("text", ""))[:MAX_MESSAGE_CHARS],
    }


def _capped_check(check: dict[str, Any]) -> dict[str, Any]:
    def small(value: Any) -> Any:
        return value if isinstance(value, int | float | bool) or value is None else str(value)[:200]

    return {
        "id": str(check.get("id", ""))[:100],
        "status": "pass" if check.get("status") == "pass" else "fail",
        "observed": small(check.get("observed")),
        "expected": small(check.get("expected")),
        "message": str(check.get("message", ""))[:MAX_MESSAGE_CHARS],
    }


def _container_failure(outcome: Any, result: dict[str, Any], missing: list[str]) -> str:
    if outcome.timed_out:
        return "The step ran past its time limit and was stopped."
    if outcome.over_cap:
        return "The step wrote more than the run's disk cap and was stopped."
    if outcome.exit_code == 3 or any(c["status"] == "fail" for c in result.get("checks", [])):
        failed = [c["id"] for c in result.get("checks", []) if c["status"] == "fail"]
        return f"Failed checks: {', '.join(failed)}."
    errors = [m["text"] for m in result.get("messages", []) if m.get("level") == "error"]
    if errors:
        return f"The R code stopped with an error: {errors[-1]}"
    if missing:
        return f"The step didn't write {', '.join(missing)}."
    return f"The step exited with code {outcome.exit_code}."


def _collect(
    scratch: Path, out_dir: Path, outputs: Mapping[str, str], budget: int
) -> tuple[dict[str, _Output], list[str], int]:
    """Copy the declared outputs, regular files only, never links; count the rest."""
    collected: dict[str, _Output] = {}
    missing: list[str] = []
    for name, file in outputs.items():
        source = scratch / file
        try:
            info = os.lstat(source)
        except OSError:
            missing.append(file)
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > budget:
            missing.append(file)
            continue
        budget -= info.st_size
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as reader, open(out_dir / file, "xb") as writer:
            shutil.copyfileobj(reader, writer)
        collected[name] = _Output("", name, file, out_dir, file_facts(out_dir / file))
    try:
        present = set(os.listdir(scratch))
    except OSError:
        present = set()
    return collected, missing, len(present - set(outputs.values()))


def _copy_tree(source: Path, target: Path) -> None:
    """Copy a source tree; links and dot-files aren't copied."""
    target.mkdir(parents=True)
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and not (Path(folder) / d).is_symlink()
        )
        relative = Path(folder).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            path = Path(folder) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            shutil.copyfile(path, target / relative / name, follow_symlinks=False)


def _package_name(source: Path) -> str:
    text = (source / "DESCRIPTION").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^Package:\s*([A-Za-z][A-Za-z0-9.]*)\s*$", text, re.MULTILINE)
    if not match:
        raise _StepFailed("The package's DESCRIPTION doesn't name it.")
    return match.group(1)


def _remove_quietly(path: Path) -> None:
    """Remove a folder, retrying briefly (Windows: a scanner may hold a file open)."""
    for _ in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            continue


def who_is_running() -> str:
    """The person's git name and email, as their commits have it, else their login."""
    parts = []
    for key in ("user.name", "user.email"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            done = subprocess.run(
                ["git", "config", "--global", "--get", key],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if done.returncode == 0 and done.stdout.strip():
                parts.append(done.stdout.strip())
    if len(parts) == 2:
        return f"{parts[0]} <{parts[1]}>"
    with contextlib.suppress(Exception):
        return getpass.getuser()
    return "unknown"
