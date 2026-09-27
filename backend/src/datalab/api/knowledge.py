"""Knowledge (milestone 5): the lab knowledge base, its proposed edits, and Save & share.

Each conversation gets an editable copy of the knowledge base at `/work/kb`,
made once by a workspace seed, which records the commit it came from. After
each turn an after-turn hook diffs the copy in the turn's checkpoint against
the conversation's base and proposes the edits (a `kb_proposal` event, then
`kb_proposal_updated` as it's edited, saved, or discarded). See
docs/KNOWLEDGE_BASE.md, "How edits happen", and knowledge/service.py.

- Signing in to GitHub is `/api/github` (api/github.py): one sign-in for
  both lab repos. After signing in, `POST /sync` downloads the repo.
- Proposals: list and get; `PUT …/edits` for the person's own text;
  `POST …/accept` (Save & share) and `POST …/reject`.
- Reading (the Knowledge tab): `GET /pages` lists the pages, lab skills, and
  top files of GitHub's main as last synced, `GET /pages/{path}` reads one,
  and `GET /history` gives its latest commits. Read-only, from the clone's
  objects, and only paths in the knowledge base's layout.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from datalab.config import Settings
from datalab.knowledge import check as kb
from datalab.knowledge.proposals import Proposal, unified_diff
from datalab.knowledge.service import (
    MAX_HISTORY,
    Knowledge,
    NotActionable,
    NotAvailable,
    NotFound,
    text_digest,
)
from datalab.repos.github import Account, GitHubAuth, GitHubUnavailable, SignInNeeded
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore


@dataclass(frozen=True)
class KnowledgeServices:
    """What the Knowledge routes use, given by the app (app.py)."""

    settings: Settings  # `settings.repos`; the clone lives under `settings.data_dir`
    database: sqlite3.Connection  # for this area's own tables (migration 0007)
    conversations: ConversationStore  # proposed-edit cards are conversation events
    sessions: SessionManager  # the workspace seed and after-turn hook
    # The app's one GitHub sign-in, shared with the pipelines repo (only one
    # object may refresh its tokens); made here from `[repos] client_id` if
    # not given.
    auth: GitHubAuth | None = None
    # Tests only: a remote other than GitHub's.
    remote: str | None = None


class AccountOut(BaseModel):
    login: str
    name: str


RepoState = Literal[
    "not configured", "signed out", "no access", "not cloned",
    "in sync", "behind", "diverged", "sync failed",
]  # fmt: skip


class KnowledgeStatus(BaseModel):
    available: bool
    # The local copy of the knowledge-base repo.
    repo: RepoState
    name: str | None = None
    signed_in: bool = False
    account: AccountOut | None = None
    # GitHub's main as last synced.
    head: str | None = None
    last_sync: str | None = None
    last_error: str | None = None
    ahead: int = 0
    behind: int = 0
    message: str | None = None


class FindingOut(BaseModel):
    id: str
    path: str
    rule: str
    # data: may be participant data; blocks Save & share until confirmed.
    severity: Literal["error", "data", "warning"]
    message: str
    line: int | None = None


class RefusedOut(BaseModel):
    path: str
    reason: str


class SaveResultOut(BaseModel):
    state: str
    message: str
    commit: str | None = None
    upstream: str | None = None
    findings: list[FindingOut] = []
    conflicts: list[str] = []
    after_rebase: bool = False


class FileSummary(BaseModel):
    path: str
    change: Literal["added", "modified", "deleted"]
    # Changes to the fields only people set (status, reviewed_by, …).
    flags: list[str]


ProposalState = Literal[
    "open", "superseded", "withdrawn", "rejected", "saving", "saved",
    "conflict", "check_failed", "failed",
]  # fmt: skip


class ProposalOut(BaseModel):
    id: str
    conversation_id: str
    status: ProposalState
    created_at: str
    updated_at: str
    turn: int
    base: str
    files: list[FileSummary]
    refused: list[RefusedOut]
    result: SaveResultOut | None = None
    commit: str | None = None
    decided_by: str | None = None


class FileDetail(FileSummary):
    before: str | None
    # The agent's text, and what would be shared (the person's edit, or the agent's).
    agent: str | None
    after: str | None
    edited: bool
    left_out: bool
    diff: str
    # What Save & share would commit for it, as a digest: sent back with
    # accept, so what's saved is what the person saw.
    after_sha256: str | None = None
    # For a file that conflicted: the text on GitHub's main now ("deleted" if
    # GitHub no longer has it, "not text" if it isn't UTF-8 text), and whether
    # the person has written what it should say against that version.
    conflict: bool = False
    theirs: str | None = None
    theirs_state: Literal["text", "deleted", "not text"] | None = None
    resolved: bool = False


class ProposalDetail(BaseModel):
    proposal: ProposalOut
    files: list[FileDetail]
    # The check, as Save & share would run it first.
    findings: list[FindingOut]


class EditsIn(BaseModel):
    # Path -> the person's text, or null to leave that file out.
    files: dict[str, str | None] = {}
    # Paths to go back to the agent's version.
    reset: list[str] = []


class AcceptIn(BaseModel):
    # Ids of data findings the person has checked and confirmed aren't
    # participant data.
    confirmed: list[str] = []
    # What the person saw: each file's after_sha256, and the ids of the
    # check's findings. Save & share refuses if either has changed since.
    seen: dict[str, str | None]
    findings: list[str]


class KbEntryOut(BaseModel):
    path: str
    # top, page, skill, skill_file, or generated.
    place: str
    size: int
    title: str
    summary: str
    status: str | None
    kind: str | None


class KbPagesOut(BaseModel):
    # The commit they're from: GitHub's main as last synced (None before the first sync).
    head: str | None
    pages: list[KbEntryOut]


class KbPageOut(BaseModel):
    path: str
    place: str
    head: str
    text: str
    front_matter: dict[str, Any] | None
    body: str


class KbCommitOut(BaseModel):
    commit: str
    author: str
    date: str
    subject: str
    paths: list[str]
    changed: int


def build_knowledge_router(services: KnowledgeServices) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
    knowledge = Knowledge(
        services.settings,
        services.database,
        services.conversations,
        services.sessions,
        auth=services.auth,
        remote=services.remote,
    )

    async def run[T](work: Callable[[], T]) -> T:
        # git and GitHub: never on the event loop.
        try:
            return await asyncio.to_thread(work)
        except NotAvailable as error:
            raise HTTPException(409, str(error) or "The knowledge base isn't set up.") from None
        except NotFound as error:
            raise HTTPException(404, str(error)) from None
        except NotActionable as error:
            raise HTTPException(409, str(error)) from None
        except SignInNeeded as error:
            # Not 401: that's the browser's own DataLab session.
            raise HTTPException(403, str(error)) from None
        except GitHubUnavailable as error:
            raise HTTPException(502, str(error)) from None

    @router.get("/status")
    async def status() -> KnowledgeStatus:
        return KnowledgeStatus(**_plain(await run(knowledge.status)))

    @router.post("/sync")
    async def sync() -> KnowledgeStatus:
        return KnowledgeStatus(**_plain(await run(knowledge.sync)))

    @router.get("/pages")
    async def pages() -> KbPagesOut:
        head, found = await run(knowledge.pages)
        return KbPagesOut(head=head, pages=[KbEntryOut(**vars(e)) for e in found])

    @router.get("/pages/{path:path}")
    async def page(path: str) -> KbPageOut:
        return KbPageOut(**vars(await run(lambda: knowledge.page(path))))

    @router.get("/history")
    async def history(limit: int = Query(20, ge=1, le=MAX_HISTORY)) -> list[KbCommitOut]:
        return [KbCommitOut(**vars(c)) for c in await run(lambda: knowledge.history(limit))]

    @router.get("/proposals")
    async def proposals(conversation_id: str | None = None) -> list[ProposalOut]:
        found = await run(lambda: knowledge.store.list(conversation_id))
        return [_summary(p) for p in found]

    @router.get("/proposals/{proposal_id}")
    async def proposal(proposal_id: str) -> ProposalDetail:
        return await run(lambda: _detail(knowledge, knowledge.get(proposal_id)))

    @router.put("/proposals/{proposal_id}/edits")
    async def edit(proposal_id: str, body: EditsIn) -> ProposalDetail:
        return await run(
            lambda: _detail(knowledge, knowledge.edit(proposal_id, body.files, body.reset))
        )

    @router.post("/proposals/{proposal_id}/accept")
    async def accept(proposal_id: str, body: AcceptIn) -> ProposalDetail:
        return await run(
            lambda: _detail(
                knowledge,
                knowledge.accept(proposal_id, body.confirmed, body.seen, body.findings),
            )
        )

    @router.post("/proposals/{proposal_id}/reject")
    async def reject(proposal_id: str) -> ProposalDetail:
        return await run(lambda: _detail(knowledge, knowledge.reject(proposal_id)))

    router.knowledge = knowledge  # type: ignore[attr-defined]  # for tests
    return router


def _plain(status: dict[str, Any]) -> dict[str, Any]:
    account = status.get("account")
    if isinstance(account, Account):
        status = {**status, "account": {"login": account.login, "name": account.name}}
    return status


def _summary(proposal: Proposal) -> ProposalOut:
    return ProposalOut(
        id=proposal.id,
        conversation_id=proposal.conversation_id,
        status=proposal.status,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        turn=proposal.turn,
        base=proposal.base,
        files=[
            FileSummary(path=c.path, change=c.change, flags=list(c.flags)) for c in proposal.files
        ],
        refused=[RefusedOut(path=r.path, reason=r.reason) for r in proposal.refused],
        result=SaveResultOut(**proposal.result) if proposal.result.get("message") else None,
        commit=proposal.commit,
        decided_by=proposal.decided_by,
    )


def _detail(knowledge: Knowledge, proposal: Proposal) -> ProposalDetail:
    views = knowledge.files(proposal)
    conflicts = set(proposal.result.get("conflicts") or [])
    upstream = proposal.result.get("upstream")
    resolutions = proposal.edits.get("resolutions") or {}
    resolved_against = proposal.edits.get("resolved_against")
    files = []
    for view in views:
        path = view.change.path
        theirs, theirs_state = None, None
        if path in conflicts and upstream:
            with knowledge.clone.lock:
                content = knowledge.clone.show(upstream, path)
            theirs = kb.as_text(content) if content is not None else None
            theirs_state = (
                "deleted" if content is None else "text" if theirs is not None else "not text"
            )
        files.append(
            FileDetail(
                path=path,
                change=view.change.change,
                flags=list(view.change.flags),
                before=view.before,
                agent=view.agent,
                after=view.after,
                edited=view.edited,
                left_out=view.left_out,
                diff=unified_diff(path, view.before, view.after),
                after_sha256=text_digest(view.after),
                conflict=path in conflicts,
                theirs=theirs,
                theirs_state=theirs_state,
                # Written against the version of main it conflicted with.
                resolved=path in resolutions
                and upstream is not None
                and resolved_against == upstream,
            )
        )
    report = knowledge.preview(proposal) if views else None
    return ProposalDetail(
        proposal=_summary(proposal),
        files=files,
        findings=[FindingOut(**f.to_dict()) for f in (report.findings if report else [])],
    )
