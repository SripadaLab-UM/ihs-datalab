"""Pipelines (milestone 6): browsing and changing the lab's `ihs-pipelines` repo.

Each Data engineering or Workflow authoring conversation gets its own copy of
the repo at `/work/pipelines`, made once by a workspace seed. After each turn an
after-turn hook compares the copy in the turn's checkpoint with the
conversation's base and records what changed as a proposal, which the
Pipelines tab lists. See pipelines/service.py.

- The repo: `GET /status`, `POST /sync`, and `GET /files` and
  `GET /files/{path}` to browse GitHub's `main` as last synced. Signing in
  to GitHub is `/api/github` (api/github.py): one sign-in for both repos.
- Proposals: list and get; `POST …/tests` runs the package's tests on one
  in the background; `POST …/accept` (Save & share, also in the background:
  it runs the tests first if they haven't passed) and `POST …/reject`. The
  tab polls a proposal while it's being tested or saved.
- `GET /tests/{id}/log`: the end of a test run's log.
- A person's own edits (Edit manually, New file, and Edit before accepting:
  pipelines/edits.py): `POST /edits` starts one (or finds the file's open
  one), `POST /proposals/{id}/edit` turns a proposal into one, `PUT
  /edits/{id}` keeps the draft on this computer, `POST …/check` runs the
  proposal's check on the editor's text, `POST …/tests` the package's
  tests, `POST …/reapply` moves it onto GitHub's newer version, `POST
  …/share` is Save & share (in the background, as a proposal's), and `POST
  …/discard`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.config import Settings
from datalab.knowledge.proposals import unified_diff
from datalab.pipelines.edits import (
    Edit,
    EditNotFound,
    EditRefused,
    editable_problem,
    source_note,
)
from datalab.pipelines.proposals import Proposal, TestRun
from datalab.pipelines.service import NotActionable, NotAvailable, NotFound, Pipelines
from datalab.repos.git import GitError, safe_path
from datalab.repos.github import Account, GitHubAuth, GitHubUnavailable, SignInNeeded
from datalab.sessions.containers import instance_of
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.workflows.sandbox import DockerSandbox, Sandbox, StepLimits


@dataclass(frozen=True)
class PipelineServices:
    """What the Pipelines routes use, given by the app (app.py)."""

    settings: Settings  # `settings.repos`; the clone and test logs live under `data_dir`
    database: sqlite3.Connection  # for this area's own tables (migration 0010)
    conversations: ConversationStore
    sessions: SessionManager  # the workspace seed and after-turn hook
    # The one GitHub sign-in, shared with the knowledge base (only one object
    # may refresh its tokens). None when the GitHub App isn't configured.
    auth: GitHubAuth | None
    # Where the package's tests run: Docker, unless a test gives another.
    sandbox: Sandbox | None = None
    # Tests only: a remote other than GitHub's.
    remote: str | None = None


RepoState = Literal[
    "not configured", "signed out", "no access", "not cloned",
    "in sync", "behind", "diverged", "sync failed",
]  # fmt: skip


class PipelineAccountOut(BaseModel):
    login: str
    name: str


class PipelinesStatus(BaseModel):
    available: bool
    # Practice DataLab: no lab repos, so nothing here is available.
    practice: bool = False
    repo: RepoState
    name: str | None = None
    signed_in: bool = False
    account: PipelineAccountOut | None = None
    # GitHub's main as last synced.
    head: str | None = None
    last_sync: str | None = None
    last_error: str | None = None
    ahead: int = 0
    behind: int = 0
    message: str | None = None


class PipelineTreeFile(BaseModel):
    path: str
    size: int


class PipelineTreeOut(BaseModel):
    head: str | None
    files: list[PipelineTreeFile]
    more_files: int = 0


class PipelineFileOut(BaseModel):
    path: str
    head: str
    size: int
    # None when it's binary or too large to show.
    text: str | None
    too_large: bool = False
    # Whether a person can change it in DataLab (Edit manually); if not, why
    # and where to change it instead.
    editable: bool = False
    source_note: str | None = None
    # The open edit of it on this computer, if there is one.
    draft: str | None = None


class PipelineFindingOut(BaseModel):
    id: str
    path: str
    rule: str
    # error: can't be shared. data: may be participant data. code: runs on
    # install or load, on everyone's computer. data and code block Save &
    # share until the person confirms each one.
    severity: Literal["error", "data", "code"]
    message: str
    line: int | None = None
    # The line it's about, for the person to judge (at most 200 characters).
    text: str = ""


class PipelineRefusedOut(BaseModel):
    path: str
    reason: str


class PipelineSaveResultOut(BaseModel):
    state: str
    message: str
    commit: str | None = None
    upstream: str | None = None
    findings: list[PipelineFindingOut] = []
    conflicts: list[str] = []
    after_rebase: bool = False
    test: str | None = None
    # A proposal replaced by the person's edit of it (Edit before accepting).
    edit: str | None = None


class PipelineTestFileOut(BaseModel):
    file: str
    tests: int
    failed: int
    skipped: int
    errors: int


class PipelineTestFailureOut(BaseModel):
    file: str
    test: str
    kind: Literal["failure", "error"]


class PipelineTestOut(BaseModel):
    id: str
    status: Literal["running", "passed", "failed", "error"]
    started_at: str
    finished_at: str | None = None
    commit: str | None = None
    message: str | None = None
    tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: int = 0
    files: list[PipelineTestFileOut] = []
    more_files: int = 0
    failures: list[PipelineTestFailureOut] = []


ProposalState = Literal[
    "open", "superseded", "withdrawn", "rejected", "saving", "saved",
    "conflict", "check_failed", "tests_failed", "failed",
]  # fmt: skip


class PipelineFileChange(BaseModel):
    path: str
    change: Literal["added", "modified", "deleted"]


class PipelineProposalOut(BaseModel):
    id: str
    conversation_id: str
    conversation_title: str | None = None
    status: ProposalState
    created_at: str
    updated_at: str
    turn: int
    base: str
    files: list[PipelineFileChange]
    refused: list[PipelineRefusedOut]
    result: PipelineSaveResultOut | None = None
    commit: str | None = None  # what was pushed, once saved
    decided_by: str | None = None
    # The newest run of the tests on this change.
    test: PipelineTestOut | None = None


class PipelineFileDiff(PipelineFileChange):
    before: str | None
    after: str | None
    binary: bool
    diff: str


class PipelineProposalDetail(BaseModel):
    proposal: PipelineProposalOut
    files: list[PipelineFileDiff]
    # The check, as Save & share would run it first.
    findings: list[PipelineFindingOut]


class PipelineAcceptIn(BaseModel):
    # Ids of data findings the person has checked and confirmed aren't participant data.
    confirmed: list[str] = []


EditState = Literal[
    "draft", "saving", "saved", "conflict", "check_failed", "tests_failed", "failed", "discarded"
]  # fmt: skip


class PipelineEditOriginOut(BaseModel):
    # Edit before accepting: the assistant's proposal it began from.
    proposal_id: str
    conversation_id: str
    conversation_title: str | None = None


class PipelineEditFileOut(BaseModel):
    path: str
    # The person's text; None: the edit deletes the file.
    text: str | None
    new: bool
    # At the edit's base ("where you started"); None: not there.
    before: str | None
    diff: str
    # Changed on GitHub's main (as last synced) since the edit's base: a
    # conflict to resolve before Save & share. `theirs` is the version now.
    upstream_changed: bool = False
    theirs: str | None = None
    theirs_state: Literal["text", "deleted", "not text"] = "text"
    # Why it can't be changed by hand (a file roxygen2 writes), if so.
    source_note: str | None = None


class PipelineEditOut(BaseModel):
    id: str
    status: EditState
    base: str
    # GitHub's main as last synced.
    head: str | None
    created_at: str
    # Also the draft's version: pass it back when keeping or sharing.
    updated_at: str
    files: list[PipelineEditFileOut]
    origin: PipelineEditOriginOut | None = None
    result: PipelineSaveResultOut | None = None
    commit: str | None = None  # what was pushed, once saved
    decided_by: str | None = None
    # The newest run of the tests on the kept draft.
    test: PipelineTestOut | None = None
    # The check on the kept draft, as Save & share runs it first.
    findings: list[PipelineFindingOut]
    upstream_changed: bool = False


class PipelineEditSummaryOut(BaseModel):
    id: str
    status: EditState
    paths: list[str]
    created_at: str
    updated_at: str
    from_proposal: str | None = None


class PipelineStartEditIn(BaseModel):
    path: str
    # A file that isn't in the repo yet.
    new: bool = False


class PipelineKeepEditIn(BaseModel):
    # The edit's files as the person has them (None: deleted). A file left
    # out is left out of the edit.
    files: dict[str, str | None]
    # The draft's updated_at as the person opened it.
    version: str


class PipelineCheckEditIn(BaseModel):
    # The file in the editor, and its text now; the kept draft if not given.
    path: str | None = None
    text: str | None = None


class PipelineEditCheckOut(BaseModel):
    findings: list[PipelineFindingOut]


class PipelineShareEditIn(BaseModel):
    confirmed: list[str] = []
    version: str


class PipelineReapplyIn(BaseModel):
    version: str
    # Texts the person wrote to keep, by file; the rest are merged.
    resolutions: dict[str, str] = {}


class PipelineReapplyOut(BaseModel):
    edit: PipelineEditOut
    # The files that didn't merge cleanly: their text with the overlapping
    # lines marked, to resolve (the edit itself is unchanged).
    merged: dict[str, str] = {}


class PipelineTestLogOut(BaseModel):
    id: str
    text: str
    truncated: bool


def build_pipelines_router(services: PipelineServices) -> APIRouter:
    router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
    settings = services.settings
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
    pipelines = Pipelines(
        settings,
        services.database,
        services.conversations,
        services.sessions,
        auth=services.auth,
        sandbox=sandbox,
        remote=services.remote,
    )
    conversations = services.conversations

    async def run[T](work: Callable[[], T]) -> T:
        # git and GitHub: never on the event loop.
        return await guarded(asyncio.to_thread(work))

    async def guarded[T](work: Awaitable[T]) -> T:
        try:
            return await work
        except NotAvailable as error:
            raise HTTPException(409, str(error) or "The pipelines repo isn't set up.") from None
        except NotFound as error:
            raise HTTPException(404, str(error)) from None
        except NotActionable as error:
            raise HTTPException(409, str(error)) from None
        except SignInNeeded as error:
            # Not 401: that's the browser's own DataLab session.
            raise HTTPException(403, str(error)) from None
        except GitHubUnavailable as error:
            raise HTTPException(502, str(error)) from None
        except GitError as error:
            raise HTTPException(502, str(error)) from None
        except EditRefused as error:
            raise HTTPException(409, str(error)) from None
        except EditNotFound as error:
            raise HTTPException(404, str(error)) from None

    def summary(proposal: Proposal) -> PipelineProposalOut:
        made = conversations.get(proposal.conversation_id)
        return _summary(proposal, made.title if made else None, pipelines.latest_test(proposal))

    def detail(proposal: Proposal) -> PipelineProposalDetail:
        views = pipelines.files(proposal)
        report = pipelines.preview(proposal) if views else None
        return PipelineProposalDetail(
            proposal=summary(proposal),
            files=[
                PipelineFileDiff(
                    path=v.change.path,
                    change=v.change.change,
                    before=v.before,
                    after=v.after,
                    binary=v.binary,
                    diff="" if v.binary else unified_diff(v.change.path, v.before, v.after),
                )
                for v in views
            ],
            findings=[
                PipelineFindingOut(**f.to_dict()) for f in (report.findings if report else [])
            ],
        )

    @router.get("/status")
    async def status() -> PipelinesStatus:
        return PipelinesStatus(**_plain(await run(pipelines.status)))

    @router.post("/sync")
    async def sync() -> PipelinesStatus:
        return PipelinesStatus(**_plain(await run(pipelines.sync)))

    @router.get("/files")
    async def files() -> PipelineTreeOut:
        head, found, more = await run(pipelines.tree)
        return PipelineTreeOut(
            head=head, files=[PipelineTreeFile(path=p, size=s) for p, s in found], more_files=more
        )

    @router.get("/files/{path:path}")
    async def file(path: str) -> PipelineFileOut:
        if not safe_path(path):
            raise HTTPException(404, "No such file.")

        def read() -> PipelineFileOut:
            head, text, size, too_large = pipelines.read(path)
            note = source_note(path, text)
            open_edit = pipelines.edits.store.open_for(path)
            return PipelineFileOut(
                path=path,
                head=head,
                size=size,
                text=text,
                too_large=too_large,
                editable=text is not None and note is None and editable_problem(path) is None,
                source_note=note,
                draft=open_edit.id if open_edit else None,
            )

        return await run(read)

    @router.get("/proposals")
    async def proposals(conversation_id: str | None = None) -> list[PipelineProposalOut]:
        def listed() -> list[PipelineProposalOut]:
            return [summary(p) for p in pipelines.store.list(conversation_id)]

        return await run(listed)

    @router.get("/proposals/{proposal_id}")
    async def proposal(proposal_id: str) -> PipelineProposalDetail:
        return await run(lambda: detail(pipelines.get(proposal_id)))

    @router.post("/proposals/{proposal_id}/tests")
    async def test(proposal_id: str) -> PipelineProposalDetail:
        await guarded(pipelines.start_tests(proposal_id))
        return await run(lambda: detail(pipelines.get(proposal_id)))

    @router.post("/proposals/{proposal_id}/accept")
    async def accept(proposal_id: str, body: PipelineAcceptIn) -> PipelineProposalDetail:
        await guarded(pipelines.accept(proposal_id, body.confirmed))
        return await run(lambda: detail(pipelines.get(proposal_id)))

    @router.post("/proposals/{proposal_id}/reject")
    async def reject(proposal_id: str) -> PipelineProposalDetail:
        return await run(lambda: detail(pipelines.reject(proposal_id)))

    # A person's own edits ------------------------------------------------------

    edits = pipelines.edits

    def edit_detail(edit: Edit) -> PipelineEditOut:
        upstream = edits.upstream(edit)
        origin = None
        if edit.origin.get("proposal_id"):
            made = conversations.get(str(edit.origin.get("conversation_id")))
            origin = PipelineEditOriginOut(
                proposal_id=str(edit.origin["proposal_id"]),
                conversation_id=str(edit.origin.get("conversation_id") or ""),
                conversation_title=made.title if made else None,
            )
        report = edits.check(edit) if edit.status not in ("saved", "discarded") else None
        files = []
        for path, file in edit.files.items():
            up = upstream[path]
            files.append(
                PipelineEditFileOut(
                    path=path,
                    text=file.text,
                    new=file.new,
                    before=up.before,
                    diff=unified_diff(path, up.before, file.text),
                    upstream_changed=up.changed,
                    theirs=up.theirs if up.changed else None,
                    theirs_state=up.theirs_state,
                    source_note=source_note(path, up.before if up.before is not None else file.text)
                    if path.startswith("ihsDataR/")
                    else None,
                )
            )
        return PipelineEditOut(
            id=edit.id,
            status=edit.status,
            base=edit.base,
            head=edits.head(),
            created_at=edit.created_at,
            updated_at=edit.updated_at,
            files=files,
            origin=origin,
            result=PipelineSaveResultOut(**edit.result) if edit.result.get("message") else None,
            commit=edit.saved_commit,
            decided_by=edit.decided_by,
            test=_test(edits.latest_test(edit)),
            findings=[PipelineFindingOut(**f.to_dict()) for f in report.findings] if report else [],
            upstream_changed=any(u.changed for u in upstream.values()),
        )

    @router.get("/edits")
    async def open_edits() -> list[PipelineEditSummaryOut]:
        def listed() -> list[PipelineEditSummaryOut]:
            pipelines._repo()
            return [
                PipelineEditSummaryOut(
                    id=e.id,
                    status=e.status,
                    paths=list(e.files),
                    created_at=e.created_at,
                    updated_at=e.updated_at,
                    from_proposal=e.origin.get("proposal_id"),
                )
                for e in edits.store.list_recent()
            ]

        return await run(listed)

    @router.post("/edits")
    async def start_edit(body: PipelineStartEditIn) -> PipelineEditOut:
        return await run(lambda: edit_detail(edits.start(body.path, new=body.new)))

    @router.post("/proposals/{proposal_id}/edit")
    async def edit_proposal(proposal_id: str) -> PipelineEditOut:
        return await run(lambda: edit_detail(edits.from_proposal(proposal_id)))

    @router.get("/edits/{edit_id}")
    async def get_edit(edit_id: str) -> PipelineEditOut:
        return await run(lambda: edit_detail(edits.get(edit_id)))

    @router.put("/edits/{edit_id}")
    async def keep_edit(edit_id: str, body: PipelineKeepEditIn) -> PipelineEditOut:
        return await run(lambda: edit_detail(edits.keep(edit_id, body.files, body.version)))

    @router.post("/edits/{edit_id}/check")
    async def check_edit(edit_id: str, body: PipelineCheckEditIn) -> PipelineEditCheckOut:
        def work() -> PipelineEditCheckOut:
            report = edits.check(edits.get(edit_id), body.path, body.text)
            return PipelineEditCheckOut(
                findings=[PipelineFindingOut(**f.to_dict()) for f in report.findings]
            )

        return await run(work)

    @router.post("/edits/{edit_id}/tests")
    async def test_edit(edit_id: str) -> PipelineEditOut:
        await guarded(edits.start_tests(edit_id))
        return await run(lambda: edit_detail(edits.get(edit_id)))

    @router.post("/edits/{edit_id}/share")
    async def share_edit(edit_id: str, body: PipelineShareEditIn) -> PipelineEditOut:
        await guarded(edits.share(edit_id, body.confirmed, body.version))
        return await run(lambda: edit_detail(edits.get(edit_id)))

    @router.post("/edits/{edit_id}/reapply")
    async def reapply_edit(edit_id: str, body: PipelineReapplyIn) -> PipelineReapplyOut:
        def work() -> PipelineReapplyOut:
            edit, merged = edits.reapply(edit_id, body.version, body.resolutions)
            return PipelineReapplyOut(edit=edit_detail(edit), merged=merged)

        return await run(work)

    @router.post("/edits/{edit_id}/discard")
    async def discard_edit(edit_id: str) -> PipelineEditOut:
        return await run(lambda: edit_detail(edits.discard(edit_id)))

    @router.get("/tests/{test_id}/log")
    async def test_log(test_id: str) -> PipelineTestLogOut:
        def tail() -> PipelineTestLogOut:
            if pipelines.store.get_test(test_id) is None:
                raise NotFound("No such test run.")
            text, truncated = pipelines.tests.log_tail(test_id)
            return PipelineTestLogOut(id=test_id, text=text, truncated=truncated)

        return await run(tail)

    router.pipelines = pipelines  # type: ignore[attr-defined]  # for tests
    return router


def _plain(status: dict[str, Any]) -> dict[str, Any]:
    account = status.get("account")
    if isinstance(account, Account):
        status = {**status, "account": {"login": account.login, "name": account.name}}
    return status


def _test(run: TestRun | None) -> PipelineTestOut | None:
    if run is None:
        return None
    s = run.summary
    return PipelineTestOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        commit=run.commit,
        message=run.message,
        tests=s.get("tests", 0),
        passed=s.get("passed", 0),
        failed=s.get("failed", 0),
        skipped=s.get("skipped", 0),
        errors=s.get("errors", 0),
        warnings=s.get("warnings", 0),
        files=[PipelineTestFileOut(**f) for f in s.get("files", [])],
        more_files=s.get("more_files", 0),
        failures=[PipelineTestFailureOut(**f) for f in s.get("failures", [])],
    )


def _summary(proposal: Proposal, title: str | None, test: TestRun | None) -> PipelineProposalOut:
    return PipelineProposalOut(
        id=proposal.id,
        conversation_id=proposal.conversation_id,
        conversation_title=title,
        status=proposal.status,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        turn=proposal.turn,
        base=proposal.base,
        files=[PipelineFileChange(path=c.path, change=c.change) for c in proposal.files],
        refused=[PipelineRefusedOut(path=r.path, reason=r.reason) for r in proposal.refused],
        result=PipelineSaveResultOut(**proposal.result) if proposal.result.get("message") else None,
        commit=proposal.saved_commit,
        decided_by=proposal.decided_by,
        test=_test(test),
    )
