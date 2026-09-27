"""Pipelines (milestone 6): browsing and changing the lab's `ihs-pipelines` repo.

Each Data engineering conversation gets its own copy of the repo at
`/work/pipelines`, made once by a workspace seed. After each turn an
after-turn hook compares the copy in the turn's checkpoint with the
conversation's base and records what changed as a proposal, which the
Pipelines tab lists. See pipelines/service.py.

- The repo: `GET /status`, `POST /sync`, and `GET /files` and
  `GET /files/{path}` to browse GitHub's `main` as last synced. Signing in
  to GitHub is the Knowledge routes' (`/api/knowledge/sign-in`): there's one
  sign-in for both repos.
- Proposals: list and get; `POST …/tests` runs the package's tests on one
  in the background; `POST …/accept` (Save & share, also in the background:
  it runs the tests first if they haven't passed) and `POST …/reject`. The
  tab polls a proposal while it's being tested or saved.
- `GET /tests/{id}/log`: the end of a test run's log.
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
    repo: RepoState = "not configured"
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


class PipelineFindingOut(BaseModel):
    id: str
    path: str
    rule: str
    # data: may be participant data; blocks Save & share until confirmed.
    severity: Literal["error", "data", "warning"]
    message: str
    line: int | None = None


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
        head, text, size, too_large = await run(lambda: pipelines.read(path))
        return PipelineFileOut(path=path, head=head, size=size, text=text, too_large=too_large)

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
