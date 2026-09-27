"""Workflows (milestone 6): workflow files, runs, and run records.

A run's SQL steps go through `DataService.run_query` with `origin="run"` and
the run's `run_…` id, so they share the data service's checks and appear in
the audit log attributed to the run; R, pipeline and custom QC steps run in
no-network containers (see workflows/runner.py). Run records are migration
0008.

Workflow files are read from the synced `ihs-pipelines` repo, `[workflows]
folder` in settings.toml, or `<data folder>/workflows-local/`
(`workflows_folder` in workflows/source.py). A run reads a snapshot of them,
taken when it starts.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.data.service import DataService
from datalab.exports import DestinationStore
from datalab.sessions.containers import instance_of
from datalab.workflows.model import (
    MAX_FILE_BYTES,
    Problem,
    Workflow,
    WorkflowInvalid,
    problem_position,
    step_inputs,
    step_kind,
    step_outputs,
)
from datalab.workflows.records import RunStore
from datalab.workflows.runner import ReplayNotExact, RunRefused, WorkflowRunner
from datalab.workflows.sandbox import DockerSandbox, Sandbox, StepLimits
from datalab.workflows.source import SourceError, workflows_folder

_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class WorkflowServices:
    """What the Workflows routes use, given by the app (app.py)."""

    settings: Settings  # `settings.workflows`, `settings.repos`; runs go under `data_dir`
    database: sqlite3.Connection  # for run records (migration 0008)
    data: DataService  # SQL steps
    access_log: AccessLog  # a run's queries: `for_origin("run", run_id)`
    # Where container steps run: Docker, unless a test gives another.
    sandbox: Sandbox | None = None


# ------------------------------------------------------------------ models

Scalar = bool | int | float | str
StepKind = Literal["sql", "r", "pipeline", "qc_builtin", "qc_custom"]
DeliveryStatus = Literal["none", "pending", "delivered", "skipped", "failed"]


class WorkflowsStatus(BaseModel):
    available: bool
    folder: str
    # Why the files come from that folder, when it isn't the expected one.
    message: str | None = None


class ProblemOut(BaseModel):
    path: str  # where in the file, such as steps[1].inputs.raw
    message: str
    # The same place as a 1-based line and column in the file's text, when
    # DataLab has the text and can find it (for the editor's marks).
    line: int | None = None
    column: int | None = None


class ParameterOut(BaseModel):
    name: str
    type: Literal["date", "string", "integer", "number", "boolean"]
    default: Scalar | None
    description: str


class StepSummary(BaseModel):
    id: str
    kind: StepKind
    description: str
    inputs: dict[str, str]
    outputs: dict[str, str]


class DeliverOut(BaseModel):
    destination: str
    folder: str
    files: list[str]


class WorkflowRunOut(BaseModel):
    id: str
    workflow_name: str
    workflow_path: str
    mode: Literal["run", "run_again", "replay"]
    of_run: str | None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    started_at: str
    finished_at: str | None
    started_by: str
    message: str | None
    delivery_status: DeliveryStatus
    delivery_message: str | None
    replay_exact: bool | None
    replay_notes: list[str]
    reproduced: bool | None


class WorkflowOut(BaseModel):
    path: str
    name: str | None
    description: str
    valid: bool
    problems: list[ProblemOut]
    parameters: list[ParameterOut]
    steps: list[StepSummary]
    reads: list[str]
    deliver: DeliverOut | None
    source: Literal["git", "file"] | None
    blob: str | None
    commit: str | None
    # The newest run of this file, in the list only.
    last_run: WorkflowRunOut | None = None


class WorkflowTextOut(BaseModel):
    """A workflow file's text, as it is in the folder now (read-only here)."""

    path: str
    text: str
    source: Literal["git", "file"]
    blob: str
    commit: str | None


class ValidateIn(BaseModel):
    # A file in the workflows folder, or a draft's text (the editor's).
    path: str | None = None
    text: str | None = Field(default=None, max_length=MAX_FILE_BYTES)


class ValidateOut(BaseModel):
    valid: bool
    problems: list[ProblemOut]
    workflow: WorkflowOut | None


class StartIn(BaseModel):
    path: str
    params: dict[str, Scalar] = Field(default_factory=dict)
    seed: int | None = Field(default=None, ge=0, lt=2**31)


class ReplayIn(BaseModel):
    # Run even though something can't be pinned (the reasons are recorded).
    allow_inexact: bool = False
    # Replays don't deliver unless asked to.
    deliver: bool = False


class ReplayCheckOut(BaseModel):
    exact: bool
    reasons: list[str]  # why it wouldn't be exact
    blocking: list[str]  # why it can't run at all


class StepOut(BaseModel):
    step_id: str
    position: int
    kind: StepKind
    status: Literal["pending", "running", "succeeded", "failed", "skipped", "cancelled"]
    started_at: str | None
    finished_at: str | None
    seed: int | None
    query_id: str | None
    sql_text: str | None
    binds: dict[str, Any] | None
    queries: list[dict[str, Any]]
    exit_code: int | None
    elapsed_ms: int | None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    result: dict[str, Any] | None
    message: str | None


class DeliveryOut(BaseModel):
    id: str
    destination_key: str
    destination_path: str
    folder: str
    files: list[dict[str, Any]]
    manifest_sha256: str
    delivered_at: str


class RunDetailOut(WorkflowRunOut):
    workflow_source: Literal["git", "file"]
    repo_commit: str | None
    workflow_blob: str
    image_ref: str
    image_digest: str
    image_platform: str
    host_platform: str
    r_packages_sha256: str
    runner_version: str
    runtime: dict[str, Any]
    params: dict[str, Any]
    seed: int
    reads: list[str]
    pipelines: list[dict[str, Any]]
    run_dir: str
    inputs_kept: bool
    steps: list[StepOut]
    deliveries: list[DeliveryOut]


class DeliveryStatusOut(BaseModel):
    status: DeliveryStatus
    message: str | None
    deliveries: list[DeliveryOut]


class DestinationKeyOut(BaseModel):
    key: str
    used_by: list[str]  # the workflow files that name it
    destination_id: str | None
    name: str | None
    path: str | None
    available: bool


class SetDestinationKey(BaseModel):
    destination_id: str


# ------------------------------------------------------------------ router


def build_workflows_router(services: WorkflowServices) -> APIRouter:
    settings = services.settings
    folder = workflows_folder(settings)
    destinations = DestinationStore(services.database)
    limits = settings.workflows
    sandbox = services.sandbox or DockerSandbox(
        profile=settings.profile,
        instance=instance_of(settings.data_dir),
        limits=StepLimits(
            timeout_seconds=limits.step_timeout_seconds,
            memory=limits.step_memory,
            cpus=limits.step_cpus,
            pids=limits.step_pids,
        ),
        cache_dir=settings.data_dir / "workflow-cache",
    )
    runner = WorkflowRunner(
        settings=settings,
        store=RunStore(services.database),
        data=services.data,
        access_log=services.access_log,
        sandbox=sandbox,
        folder=folder,
        destinations=destinations,
    )
    practice = settings.profile == "practice"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Before the database closes: stop every run and remove its containers.
        await runner.close()

    router = APIRouter(prefix="/api/workflows", tags=["workflows"], lifespan=lifespan)
    router.runner = runner  # type: ignore[attr-defined]  # for tests

    def run_or_404(run_id: str) -> dict[str, Any]:
        detail = runner.detail(run_id)
        if detail is None:
            raise HTTPException(404, "No such run.")
        return detail

    def describe(path: str) -> WorkflowOut:
        try:
            file = folder.read(path)
        except SourceError as error:
            return _invalid(path, _problems_out([Problem("", str(error))]), None)
        try:
            workflow = runner.check_text(file.text)
        except WorkflowInvalid as error:
            return _invalid(path, _problems_out(error.problems, file.text), file)
        return _workflow_out(path, workflow, runner, file)

    @router.get("/status")
    def status() -> WorkflowsStatus:
        return WorkflowsStatus(available=True, folder=str(folder.root), message=folder.note)

    @router.get("")
    def list_workflows() -> list[WorkflowOut]:
        out = []
        for path in folder.paths():
            described = describe(path)
            last = runner.store.list_runs(path, limit=1)
            if last:
                described.last_run = WorkflowRunOut.model_validate(_run_fields(last[0]))
            out.append(described)
        return out

    @router.get("/text")
    def workflow_text(path: str) -> WorkflowTextOut:
        """A workflow file's text, for the read-only view."""
        try:
            file = folder.read(path)
        except SourceError as error:
            raise HTTPException(404, str(error)) from error
        return WorkflowTextOut(
            path=path,
            text=file.text,
            source=file.source,  # type: ignore[arg-type]
            blob=file.blob,
            commit=file.commit,
        )

    @router.post("/validate")
    def validate(body: ValidateIn) -> ValidateOut:
        if body.path is not None and body.text is None:
            described = describe(body.path)
            return ValidateOut(
                valid=described.valid, problems=described.problems, workflow=described
            )
        if body.text is None or body.path is not None:
            raise HTTPException(422, "Give a path or the file's text.")
        try:
            workflow = runner.check_text(body.text)
        except WorkflowInvalid as error:
            problems = _problems_out(error.problems, body.text)
            return ValidateOut(valid=False, problems=problems, workflow=None)
        return ValidateOut(
            valid=True, problems=[], workflow=_workflow_out("", workflow, runner, None)
        )

    @router.get("/runs")
    def list_runs(path: str | None = None, limit: int = 100) -> list[WorkflowRunOut]:
        rows = runner.store.list_runs(path, limit=max(1, min(limit, 500)))
        return [WorkflowRunOut.model_validate(_run_fields(r)) for r in rows]

    @router.post("/runs", status_code=201)
    async def start(body: StartIn) -> WorkflowRunOut:
        try:
            run_id = await runner.start(body.path, body.params, seed=body.seed)
        except SourceError as error:
            raise HTTPException(404, str(error)) from error
        except WorkflowInvalid as error:
            raise _unprocessable(error) from error
        except RunRefused as error:
            raise HTTPException(409, str(error)) from error
        return WorkflowRunOut.model_validate(_run_fields(run_or_404(run_id)))

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> RunDetailOut:
        return RunDetailOut.model_validate(_run_fields(run_or_404(run_id)))

    @router.get("/runs/{run_id}/steps")
    def get_steps(run_id: str) -> list[StepOut]:
        return RunDetailOut.model_validate(_run_fields(run_or_404(run_id))).steps

    @router.post("/runs/{run_id}/stop", status_code=202)
    async def stop(run_id: str) -> WorkflowRunOut:
        run_or_404(run_id)
        try:
            await runner.stop(run_id)
        except RunRefused as error:
            raise HTTPException(409, str(error)) from error
        return WorkflowRunOut.model_validate(_run_fields(run_or_404(run_id)))

    @router.get("/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request) -> StreamingResponse:
        """Server-sent events: the run, with its steps, each time it changes, until it ends."""
        run_or_404(run_id)

        async def events() -> AsyncIterator[str]:
            async for snapshot in runner.watch(run_id):
                if await request.is_disconnected():
                    return
                if snapshot is None:
                    yield ": keep-alive\n\n"
                    continue
                payload = RunDetailOut.model_validate(_run_fields(snapshot)).model_dump_json()
                yield f"event: run\ndata: {payload}\n\n"
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/again", status_code=201)
    async def run_again(run_id: str) -> WorkflowRunOut:
        """The current workflow file afresh: today's data, the same parameters and seed."""
        run_or_404(run_id)
        try:
            new_id = await runner.run_again(run_id)
        except SourceError as error:
            raise HTTPException(404, f"The workflow file isn't there any more: {error}") from error
        except WorkflowInvalid as error:
            raise _unprocessable(error) from error
        except RunRefused as error:
            raise HTTPException(409, str(error)) from error
        return WorkflowRunOut.model_validate(_run_fields(run_or_404(new_id)))

    @router.get("/runs/{run_id}/replay")
    async def replay_check(run_id: str) -> ReplayCheckOut:
        """Whether a Replay would be exact, and if not, why."""
        run_or_404(run_id)
        check = await runner.replay_check(run_id)
        return ReplayCheckOut(exact=check.exact, reasons=check.reasons, blocking=check.blocking)

    @router.post("/runs/{run_id}/replay", status_code=201)
    async def replay(run_id: str, body: ReplayIn) -> WorkflowRunOut:
        """The original definition, extracts, image and seed, run again."""
        run_or_404(run_id)
        try:
            new_id = await runner.replay(
                run_id, allow_inexact=body.allow_inexact, deliver=body.deliver
            )
        except ReplayNotExact as error:
            raise HTTPException(
                409, {"message": "This Replay can't be exact.", "reasons": error.reasons}
            ) from error
        except WorkflowInvalid as error:
            raise _unprocessable(error) from error
        except RunRefused as error:
            raise HTTPException(409, str(error)) from error
        return WorkflowRunOut.model_validate(_run_fields(run_or_404(new_id)))

    @router.get("/runs/{run_id}/delivery")
    def delivery(run_id: str) -> DeliveryStatusOut:
        run = RunDetailOut.model_validate(_run_fields(run_or_404(run_id)))
        return DeliveryStatusOut(
            status=run.delivery_status, message=run.delivery_message, deliveries=run.deliveries
        )

    @router.get("/destinations")
    def destination_keys() -> list[DestinationKeyOut]:
        """The destination keys workflow files name, and the folder each maps to here."""
        used: dict[str, list[str]] = {}
        for path in folder.paths():
            with contextlib.suppress(WorkflowInvalid, SourceError):
                workflow = runner.check_text(folder.read(path).text)
                if workflow.deliver is not None:
                    used.setdefault(workflow.deliver.destination, []).append(path)
        mapped = {d.key: d for d in destinations.list() if d.key}
        out = []
        for key in sorted({*used, *mapped}):
            destination = mapped.get(key)
            if practice:
                name, where = "Practice exports", str(settings.data_dir / "practice-exports")
            else:
                name = destination.name if destination else None
                where = destination.path if destination else None
            out.append(
                DestinationKeyOut(
                    key=key,
                    used_by=used.get(key, []),
                    destination_id=destination.id if destination else None,
                    name=name,
                    path=where,
                    available=practice or bool(destination and destination.available),
                )
            )
        return out

    @router.put("/destinations/{key}", status_code=204)
    def set_destination_key(key: str, body: SetDestinationKey) -> None:
        """Map a destination key to one of this computer's export folders."""
        if practice:
            raise HTTPException(403, "Practice DataLab delivers only to its own practice folder.")
        if not _KEY.fullmatch(key):
            raise HTTPException(422, "A destination key is lower case letters, digits, - and _.")
        if not destinations.set_key(body.destination_id, key):
            raise HTTPException(404, "No such export folder.")

    return router


# ----------------------------------------------------------------- helpers


def _problems_out(problems: list[Problem], text: str | None = None) -> list[ProblemOut]:
    out = []
    for p in problems:
        where = problem_position(text, p.path) if text is not None else None
        line, column = where or (None, None)
        out.append(ProblemOut(path=p.path, message=p.message, line=line, column=column))
    return out


def _unprocessable(error: WorkflowInvalid) -> HTTPException:
    return HTTPException(
        422,
        {
            "message": "The workflow doesn't pass its checks.",
            "problems": [{"path": p.path, "message": p.message} for p in error.problems],
        },
    )


def _invalid(path: str, problems: list[ProblemOut], file: Any) -> WorkflowOut:
    return WorkflowOut(
        path=path,
        name=None,
        description="",
        valid=False,
        problems=problems,
        parameters=[],
        steps=[],
        reads=[],
        deliver=None,
        source=file.source if file else None,
        blob=file.blob if file else None,
        commit=file.commit if file else None,
    )


def _workflow_out(path: str, workflow: Workflow, runner: WorkflowRunner, file: Any) -> WorkflowOut:
    deliver = workflow.deliver
    return WorkflowOut(
        path=path,
        name=workflow.name,
        description=workflow.description,
        valid=True,
        problems=[],
        parameters=[
            ParameterOut(name=n, type=p.type, default=p.default, description=p.description)
            for n, p in workflow.parameters.items()
        ],
        steps=[
            StepSummary(
                id=s.id,
                kind=step_kind(s),  # type: ignore[arg-type]
                description=s.description,
                inputs=step_inputs(s),
                outputs=step_outputs(s, runner.folder.pipeline),
            )
            for s in workflow.steps
        ],
        reads=sorted(workflow.read_objects),
        deliver=DeliverOut(
            destination=deliver.destination, folder=deliver.folder, files=list(deliver.files)
        )
        if deliver
        else None,
        source=file.source if file else None,
        blob=file.blob if file else None,
        commit=file.commit if file else None,
    )


def _run_fields(run: dict[str, Any]) -> dict[str, Any]:
    out = dict(run)
    for key in ("replay_exact", "reproduced", "inputs_kept"):
        if out.get(key) is not None:
            out[key] = bool(out[key])
    out["replay_notes"] = out.get("replay_notes") or []
    if "steps" in out:
        out["steps"] = [
            {
                **s,
                "queries": s.get("queries") or [],
                "inputs": s.get("inputs") or {},
                "outputs": s.get("outputs") or {},
            }
            for s in out["steps"]
        ]
    return json.loads(json.dumps(out, default=str))
