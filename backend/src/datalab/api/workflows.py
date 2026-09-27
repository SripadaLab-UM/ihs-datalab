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

New files (Save as workflow in the SQL Playground, Turn this into a workflow
in a conversation): `POST /drafts` writes a draft from SQL and its binds
(workflows/drafts.py), which the person reviews, may edit (`POST /validate`
checks it again), and saves with `POST /saves`. Where the files are the
synced pipelines clone, saving is the Pipelines tab's Save & share (the
check, the package's tests, a commit as the person, pushed); otherwise the
file is written into the local folder, and isn't shared. Only the person
saves: no agent tool reaches these routes.
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

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from datalab import safeyaml
from datalab.api.pipelines import PipelineFindingOut
from datalab.config import Settings
from datalab.data.access_log import AccessLog, check_owner
from datalab.data.catalog import Catalog
from datalab.data.service import DataService
from datalab.data.sqlcheck import MAX_SQL_BYTES
from datalab.exports import DestinationStore
from datalab.pipelines.check import check as data_check
from datalab.pipelines.service import NotActionable, NotAvailable, NotFound, Pipelines
from datalab.repos.git import GitError
from datalab.repos.github import GitHubUnavailable, SignInNeeded
from datalab.sessions.containers import instance_of
from datalab.workflows.drafts import (
    MAX_QUERIES,
    DraftQuery,
    DraftRefused,
    draft_workflow,
    queries_from_log,
)
from datalab.workflows.model import (
    MAX_FILE_BYTES,
    Problem,
    Workflow,
    WorkflowInvalid,
    problem_positions,
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
    # A draft's SQL is checked against the catalog, as the Playground's is.
    catalog: Catalog | None = None
    # Save & share, when the files are the synced pipelines clone.
    pipelines: Pipelines | None = None


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


class DraftQueryIn(BaseModel):
    sql: str = Field(max_length=2 * MAX_SQL_BYTES)
    binds: dict[str, Scalar | None] = Field(default_factory=dict, max_length=100)


class DraftIn(BaseModel):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=500)
    # A destination key, for `deliver:`; none means no delivery.
    destination: str | None = Field(default=None, max_length=200)
    # The Playground's query (with the values it ran with)…
    queries: list[DraftQueryIn] = Field(default_factory=list, max_length=MAX_QUERIES)
    # …or queries from a conversation's Data accessed log, by id.
    conversation_id: str | None = Field(default=None, max_length=100)
    query_ids: list[str] = Field(default_factory=list, max_length=MAX_QUERIES)


class SaveTargetOut(BaseModel):
    """Where Save puts a new workflow file on this computer."""

    kind: Literal["share", "local", "unavailable"]
    folder: str | None
    message: str


class DraftOut(BaseModel):
    text: str
    valid: bool
    problems: list[ProblemOut]
    # What the person should know before saving (a default left out, and why).
    notes: list[str]
    target: SaveTargetOut
    # The Save & share check's findings on the file (possible participant data):
    # each must be confirmed before it's saved, wherever it's saved.
    findings: list[PipelineFindingOut]


class DraftCheckIn(BaseModel):
    text: str = Field(max_length=MAX_FILE_BYTES)


class DraftCheckOut(BaseModel):
    """A draft's text, checked again after an edit: the file check and the data check."""

    valid: bool
    problems: list[ProblemOut]
    findings: list[PipelineFindingOut]


class SaveIn(BaseModel):
    text: str = Field(max_length=MAX_FILE_BYTES)
    # Ids of the check's findings the person has confirmed aren't participant data.
    confirmed: list[str] = Field(default_factory=list, max_length=200)
    # Where the draft came from, for the commit message when it's shared.
    source: Literal["playground", "conversation"] = "playground"
    conversation_id: str | None = Field(default=None, max_length=100)


# already_there: the same file was saved by someone else meanwhile; nothing new was shared.
SaveState = Literal[
    "saving", "saved", "already_there", "check_failed", "tests_failed", "conflict", "failed"
]


class WorkflowSaveOut(BaseModel):
    id: str | None  # a Save & share's, to follow it with GET /saves/{id}
    state: SaveState
    shared: bool
    path: str  # the file, as the Workflows tab lists it
    message: str
    commit: str | None = None  # what was pushed, once shared
    findings: list[PipelineFindingOut] = []
    test: str | None = None  # the package's test run behind the outcome


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
    # For tests, and for the agent tools' check_workflow (app.py).
    router.runner = runner  # type: ignore[attr-defined]

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

    def save_target() -> SaveTargetOut:
        if folder.shared:
            share = services.pipelines
            if share is None or not share.available:
                why = share.unavailable if share else "Save & share isn't set up here."
                return SaveTargetOut(kind="unavailable", folder=None, message=why or "")
            return SaveTargetOut(
                kind="share",
                folder=None,
                message=(
                    "Save & share checks it, runs the pipelines package's tests, commits it as "
                    "you and pushes it to the lab's pipelines repo, as workflows/<name>.yaml."
                ),
            )
        if folder.waiting_for_sync:
            return SaveTargetOut(
                kind="unavailable",
                folder=None,
                message=(
                    "Workflows are shared through the lab's pipelines repo, which hasn't been "
                    "synced yet. Sync it in the Pipelines tab, then save."
                ),
            )
        where = str(folder.workflows_dir)
        if practice:
            why = "Practice DataLab keeps workflows on this computer"
        elif settings.workflows.folder:
            why = "Saved in the workflows folder set in settings.toml"
        else:
            why = "The lab's pipelines repo isn't set up here, so this is saved on this computer"
        return SaveTargetOut(
            kind="local",
            folder=where,
            message=f"{why}, in {where}. It isn't shared with the lab.",
        )

    @router.post("/drafts")
    def draft(body: DraftIn) -> DraftOut:
        """A workflow file drafted from SQL and its binds, checked. Nothing is saved."""
        try:
            if body.conversation_id is not None:
                if body.queries:
                    raise DraftRefused("Give queries, or a conversation's query ids, not both.")
                try:
                    records = services.access_log.for_session(body.conversation_id)
                except ValueError:
                    raise DraftRefused("That isn't a conversation.") from None
                queries = queries_from_log(records, body.query_ids)
            else:
                queries = [DraftQuery(q.sql, q.binds) for q in body.queries]
            made = draft_workflow(
                queries,
                name=body.name,
                description=body.description,
                destination=body.destination or None,
                allowed_schemas=runner.allowed_schemas,
                columns=services.catalog.column_index() if services.catalog else None,
            )
        except DraftRefused as error:
            raise HTTPException(422, str(error)) from error
        try:
            runner.check_text(made.text)
        except WorkflowInvalid as error:
            problems = _problems_out(error.problems, made.text)
        else:
            problems = []
        return DraftOut(
            text=made.text,
            valid=not problems,
            problems=problems,
            notes=made.notes,
            target=save_target(),
            findings=_findings(made.text),
        )

    @router.post("/drafts/check")
    def check_draft(body: DraftCheckIn) -> DraftCheckOut:
        """A draft's text checked again, as Save will check it."""
        try:
            runner.check_text(body.text)
        except WorkflowInvalid as error:
            problems = _problems_out(error.problems, body.text)
        else:
            problems = []
        return DraftCheckOut(valid=not problems, problems=problems, findings=_findings(body.text))

    @router.post("/saves", status_code=201)
    async def save(body: SaveIn) -> WorkflowSaveOut:
        """Save a new workflow file the person has reviewed: Save & share into
        the pipelines repo, or written into the local folder (see save_target)."""
        try:
            workflow = runner.check_text(body.text)
        except WorkflowInvalid as error:
            raise _unprocessable(error) from error
        target = save_target()
        if target.kind == "unavailable":
            raise HTTPException(409, target.message)
        if target.kind == "local":
            # The same data check as Save & share's, confirmed the same way.
            report = data_check({_repo_path(workflow.name): body.text.encode("utf-8")})
            blocking = report.blocking(body.confirmed)
            if blocking:
                return WorkflowSaveOut(
                    id=None,
                    state="check_failed",
                    shared=False,
                    path=_repo_path(workflow.name),
                    message=(
                        "The check found text that may be participant data. Confirm each one "
                        "isn't, or change the file, then save again."
                    ),
                    findings=[PipelineFindingOut(**f.to_dict()) for f in blocking],
                )
            try:
                path = folder.add(workflow.name, body.text)
            except FileExistsError as error:
                raise HTTPException(409, f"{error} Choose another name.") from error
            except (SourceError, OSError) as error:
                raise HTTPException(422, f"It couldn't be saved: {error}") from error
            return WorkflowSaveOut(
                id=None,
                state="saved",
                shared=False,
                path=path,
                message=f"Saved on this computer, in {folder.workflows_dir}. It isn't shared.",
            )
        assert services.pipelines is not None
        trailers: tuple[tuple[str, str], ...] = (("DataLab-Workflow-From", "SQL Playground"),)
        if body.source == "conversation":
            trailers = (("DataLab-Workflow-From", "a conversation's queries"),)
            if body.conversation_id:
                with contextlib.suppress(ValueError):
                    check_owner("conversation", body.conversation_id)
                    trailers += (("DataLab-Conversation", body.conversation_id),)
        try:
            job = await services.pipelines.share_workflow(
                _repo_path(workflow.name),
                body.text.encode("utf-8"),
                confirmed=body.confirmed,
                trailers=trailers,
            )
        except (NotActionable, NotAvailable) as error:
            raise HTTPException(409, str(error)) from error
        except SignInNeeded as error:
            raise HTTPException(403, str(error)) from error
        except (GitHubUnavailable, GitError) as error:
            raise HTTPException(502, str(error)) from error
        return _save_out(job)

    @router.get("/saves/{save_id}")
    def save_status(save_id: str) -> WorkflowSaveOut:
        """A Save & share as it goes (it runs the package's tests, so it takes a while)."""
        if services.pipelines is None:
            raise HTTPException(404, "No such save.")
        try:
            return _save_out(services.pipelines.workflow_save(save_id))
        except NotFound as error:
            raise HTTPException(404, str(error)) from error

    return router


# ----------------------------------------------------------------- helpers


def _problems_out(problems: list[Problem], text: str | None = None) -> list[ProblemOut]:
    out = []
    positions = problem_positions(text, [p.path for p in problems]) if text is not None else {}
    for p in problems:
        where = positions.get(p.path)
        line, column = where or (None, None)
        out.append(ProblemOut(path=p.path, message=p.message, line=line, column=column))
    return out


def _repo_path(name: str) -> str:
    """Where a new workflow file goes in the pipelines repo (and what the data check calls it)."""
    return f"workflows/{name}.yaml"


def _findings(text: str) -> list[PipelineFindingOut]:
    """The Save & share data check's findings on a draft (named as it would be saved)."""
    try:
        raw = safeyaml.load(text, max_bytes=MAX_FILE_BYTES)
    except (yaml.YAMLError, safeyaml.YamlRefused):
        raw = None
    name = raw.get("name") if isinstance(raw, dict) else None
    path = _repo_path(name if isinstance(name, str) and _KEY.fullmatch(name) else "draft")
    report = data_check({path: text.encode("utf-8")})
    return [PipelineFindingOut(**f.to_dict()) for f in report.findings]


def _save_out(job: Any) -> WorkflowSaveOut:
    return WorkflowSaveOut(
        id=job.id,
        state=job.state,
        shared=True,
        path=job.path,
        message=job.message,
        commit=job.commit,
        findings=[PipelineFindingOut(**f) for f in job.findings],
        test=job.test,
    )


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
