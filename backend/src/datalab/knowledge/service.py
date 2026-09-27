"""The knowledge base's part of DataLab: the clone, each conversation's copy,
its proposed edits, and Save & share.

- **The copy.** A workspace seed copies GitHub's `main` (as last synced) into
  each new conversation's `/work/kb`, once, and records the commit. The copy
  has no `.git` and no credentials: just the files.
- **Proposals.** After each turn, the copy in the turn's checkpoint is
  compared with the conversation's base (proposals.py). A difference becomes
  a `kb_proposal` event in the conversation's log, with the diff.
- **Saving, discarding.** Save & share (share.py) pushes the person's version.
  Either way, the conversation's base then moves on: a commit of the base
  plus the agent's files as they were, kept under `refs/datalab/kb-bases/`
  in the clone. So the next proposal holds only what the agent changes
  next, and saving it replays only that onto GitHub's `main`, keeping the
  person's edits and everyone else's changes.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datalab.config import Settings
from datalab.knowledge import check as kb
from datalab.knowledge import share
from datalab.knowledge.proposals import (
    ACTIONABLE,
    Base,
    Change,
    Proposal,
    ProposalStore,
    Workspace,
    capped_diffs,
    compare,
    copied,
    diff_stats,
    fingerprint,
    unified_diff,
)
from datalab.repos.git import Clone, GitError, Identity, TreeEntry
from datalab.repos.github import (
    GitHubAuth,
    GitHubUnavailable,
    SignInNeeded,
    access_message,
)
from datalab.sessions.checkpoints import Entry
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import Conversation, ConversationStore

log = logging.getLogger(__name__)

SEED = "knowledge"
REPO = "knowledge"  # its row in repo_sync
_BASE_REFS = "refs/datalab/kb-bases"
# How old the clone may be before a new conversation's copy syncs it first,
# and how long that sync may take before the copy is made from what's there.
_STALE_SECONDS = 10 * 60
_SEED_SYNC_SECONDS = 15
# Who makes the conversation-base commits (never pushed).
_DATALAB = Identity("DataLab", "datalab@localhost")
UNAVAILABLE_NOTE = """\
# The lab knowledge base isn't here

DataLab couldn't copy the lab knowledge base into this conversation: it isn't
signed in to GitHub yet, or hasn't downloaded the knowledge base. Carry on
without it. A new conversation will have it once it's set up (Knowledge tab).
"""


class NotAvailable(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class NotActionable(RuntimeError):
    """The proposal can't be changed now (already saved, replaced, …)."""


@dataclass(frozen=True)
class FileView:
    change: Change
    before: str | None  # the base's text
    agent: str | None  # the agent's text (review fields as in the base)
    after: str | None  # what would be shared: the person's edit, else the agent's
    edited: bool
    left_out: bool


class Knowledge:
    def __init__(
        self,
        settings: Settings,
        database,
        conversations: ConversationStore,
        sessions: SessionManager,
        *,
        auth: GitHubAuth | None = None,
        remote: str | None = None,
    ) -> None:
        repos = settings.repos
        self.repo = repos.knowledge
        self._contact = repos.access_contact
        if settings.profile == "practice":
            self.unavailable: str | None = "Practice DataLab doesn't use the lab's repositories."
        elif repos.knowledge is None:
            self.unavailable = "The knowledge base's repository isn't set in settings.toml."
        elif repos.client_id is None and auth is None:
            self.unavailable = (
                "The lab's GitHub App isn't set in settings.toml ([repos] client_id)."
            )
        else:
            self.unavailable = None
        self.auth = auth or (GitHubAuth(repos.client_id) if repos.client_id else None)
        name = (repos.knowledge or "/ihs-knowledge").split("/")[1]
        self.clone = Clone(
            settings.data_dir / "repos" / name,
            remote or f"https://github.com/{repos.knowledge}.git",
            before_network=self._fresh_token,
            # Only tests give another remote (a local bare repo).
            allow_local=remote is not None,
        )
        self.store = ProposalStore(database)
        self._conversations = conversations
        self._sessions = sessions
        self._problem: tuple[str, str] | None = None  # state, message from the last sync
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_lock = threading.Lock()
        self._sha256: dict[str, str] = {}  # blob id -> sha256 of its content
        if self.unavailable is None:
            self.store.end_interrupted_saves()
            self._prune_quietly()
            sessions.register_workspace_seed(SEED, self._seed, into="kb")
            sessions.register_after_turn(self._after_turn)

    @property
    def available(self) -> bool:
        return self.unavailable is None

    # Status and sync --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        if self.unavailable is not None or self.auth is None:
            return {"available": False, "repo": "not configured", "message": self.unavailable}
        sign_in = self.auth.status()
        synced = self.store.sync_state(REPO)
        out: dict[str, Any] = {
            "available": True,
            "name": self.repo,
            "signed_in": sign_in.state == "signed in",
            "account": sign_in.account,
            "head": synced.get("head"),
            "last_sync": synced.get("synced_at"),
            "last_error": synced.get("error"),
            "ahead": 0,
            "behind": 0,
            "message": None,
        }
        if sign_in.state != "signed in":
            return {**out, "repo": "signed out", "message": "Sign in to GitHub to use it."}
        if self._problem is not None:
            state, message = self._problem
            if state in ("no access", "signed out") or not self.clone.exists():
                return {**out, "repo": state, "message": message}
        if not self.clone.exists():
            return {**out, "repo": "not cloned", "message": "Press Sync to download it."}
        try:
            ahead, behind = self.clone.ahead_behind()
        except GitError as error:
            return {**out, "repo": "sync failed", "message": str(error)}
        out.update(ahead=ahead, behind=behind)
        if self._problem is not None:
            return {**out, "repo": "sync failed", "message": self._problem[1]}
        state = "diverged" if ahead else "behind" if behind else "in sync"
        return {**out, "repo": state}

    def sync(self) -> dict[str, Any]:
        if self.unavailable is not None or self.auth is None:
            raise NotAvailable(self.unavailable)
        try:
            head = self.clone.sync()
        except SignInNeeded as error:
            self._problem = ("signed out", str(error))
        except (GitError, GitHubUnavailable) as error:
            self._problem = self._why_not(str(error))
            self.store.record_sync(REPO, error=self._problem[1])
        else:
            self._problem = None
            self.store.record_sync(REPO, head=head)
            self._prune_quietly()
        return self.status()

    def prune_bases(self) -> int:
        """Remove the base refs of conversations that no longer exist, and the
        commits only they kept (there's no hook for a conversation being
        deleted, so this runs when DataLab starts and after each sync). How
        many were removed."""
        if not self.clone.exists():
            return 0
        with self.clone.lock:
            refs = self.clone.text("for-each-ref", "--format=%(refname)", _BASE_REFS)
            gone = [
                ref
                for ref in refs.splitlines()
                if self._conversations.get(ref.rsplit("/", 1)[-1]) is None
            ]
            for ref in gone:
                self.clone.git("update-ref", "-d", ref)
            if gone:
                self.clone.git("gc", "--quiet", "--prune=now")
        return len(gone)

    def _prune_quietly(self) -> None:
        try:
            self.prune_bases()
        except GitError as error:
            log.warning("couldn't prune the knowledge base's old bases: %s", error)

    def _why_not(self, failure: str) -> tuple[str, str]:
        """Missing access looks like any other failure to git: GitHub's API
        tells them apart (a 404 for a repository the person can't see)."""
        assert self.auth is not None and self.repo is not None
        try:
            access = self.auth.repo_access(self.repo)
        except (SignInNeeded, GitHubUnavailable):
            access = None
        if access == "none":
            account = self.auth.status().account
            return "no access", access_message(
                self.repo, account.login if account else None, self._contact
            )
        return "sync failed", failure

    def _fresh_token(self) -> None:
        if self.auth is None:
            raise SignInNeeded("Sign in to GitHub first.")
        self.auth.token_for_git()

    # The copy in each conversation ------------------------------------------

    def _seed(self, conversation: Conversation, staging: Path) -> str | None:
        with self.clone.lock:
            if self.clone.exists() and self._stale() and self.auth and self.auth.signed_in():
                try:
                    self.store.record_sync(REPO, head=self.clone.sync(timeout=_SEED_SYNC_SECONDS))
                except (GitError, SignInNeeded, GitHubUnavailable) as error:
                    log.warning("couldn't sync the knowledge base before copying it: %s", error)
            head = self.clone.remote_head()
            if head is None:
                (staging / "README.md").write_text(UNAVAILABLE_NOTE)
                return None
            self.clone.copy_tree(head, staging, skip=lambda path: not copied(path))
        return head

    def _stale(self) -> bool:
        synced = self.store.sync_state(REPO).get("synced_at")
        if not synced:
            return True
        age = datetime.datetime.now(datetime.UTC) - datetime.datetime.fromisoformat(synced)
        return age.total_seconds() > _STALE_SECONDS

    def base(self, conversation_id: str) -> str | None:
        """What the conversation's /work/kb is compared against now."""
        return self.store.base(conversation_id) or self._sessions.workspace_base(
            conversation_id, SEED
        )

    # Proposals --------------------------------------------------------------

    async def _after_turn(self, conversation_id: str, info: TurnInfo) -> None:
        if info.checkpoint is None:
            return
        await asyncio.to_thread(self.propose, conversation_id, info.turn, info.checkpoint)

    def propose(self, conversation_id: str, turn: int, checkpoint: int) -> Proposal | None:
        """Compare the copy in `checkpoint` with the base; record and announce
        a new proposal if it differs from the last one."""
        with self._lock(conversation_id):
            base = self.base(conversation_id)
            if base is None:
                return None
            workspace = self._workspace(conversation_id, checkpoint)
            with self.clone.lock:
                changes, refused = compare(workspace, self._base_view(base, workspace))
            actionable = [p for p in self.store.list(conversation_id) if p.status in ACTIONABLE]
            if not changes and not refused:
                for old in actionable:
                    self._set_status(old, "withdrawn", "The agent undid these edits.")
                return None
            mark = fingerprint(base, changes, refused)
            latest = self.store.latest(conversation_id)
            if latest is not None and latest.fingerprint == mark and latest.status != "withdrawn":
                return None  # nothing new since the last proposal
            for old in actionable:
                self._set_status(old, "superseded", "Replaced by a newer proposal.")
            proposal = self.store.add(
                conversation_id,
                turn=turn,
                checkpoint=checkpoint,
                base=base,
                fingerprint=mark,
                files=changes,
                refused=refused,
            )
            self._conversations.append(conversation_id, "kb_proposal", self._event(proposal))
            return proposal

    def _workspace(self, conversation_id: str, number: int) -> Workspace:
        checkpoints = self._sessions.checkpoints(conversation_id)
        summary = checkpoints.get(number)
        links = checkpoints.links(number)
        if summary is None or "kb" in links:
            return Workspace({}, [], [], lambda _: b"")
        files = {
            rel[3:]: (e.sha256, e.size)
            for rel, e in checkpoints.entries(number).items()
            if rel.startswith("kb/")
        }
        skipped = [(s.path[3:], s.reason) for s in summary.skipped if s.path.startswith("kb/")]

        def read(digest: str) -> bytes:
            fd = checkpoints.open_object(Entry("", digest, 0, 0, False))
            with os.fdopen(fd, "rb") as handle:
                return handle.read()

        kb_links = sorted(rel[3:] for rel in links if rel.startswith("kb/"))
        return Workspace(files, kb_links, skipped, read)

    def _base_view(self, base: str, workspace: Workspace | None = None) -> Base:
        entries = self.clone.ls_tree(base)
        if workspace is not None:
            # Hash, in one go, the base files that might be unchanged.
            wanted = [
                e.blob
                for path, (_, size) in workspace.files.items()
                if (e := entries.get(path)) is not None and e.size == size
                and e.blob not in self._sha256
            ]  # fmt: skip
            for blob, content in self.clone.read_blobs(wanted).items():
                self._sha256[blob] = hashlib.sha256(content).hexdigest()

        def read(path: str) -> bytes:
            entry = entries[path]
            return self.clone.read_blobs([entry.blob]).get(entry.blob, b"")

        def sha256(entry: TreeEntry) -> str:
            if entry.blob not in self._sha256:
                content = self.clone.read_blobs([entry.blob]).get(entry.blob, b"")
                self._sha256[entry.blob] = hashlib.sha256(content).hexdigest()
            return self._sha256[entry.blob]

        return Base(entries, read, sha256)

    def files(self, proposal: Proposal) -> list[FileView]:
        """Each file of a proposal: the base's text, the agent's, and what
        would be shared."""
        checkpoints = self._sessions.checkpoints(proposal.conversation_id)
        with self.clone.lock:
            entries = self.clone.ls_tree(proposal.base)
            blobs = self.clone.read_blobs(
                entries[c.path].blob for c in proposal.files if c.path in entries
            )
        edits: dict[str, str | None] = proposal.edits.get("files", {})
        views = []
        for change in proposal.files:
            entry = entries.get(change.path)
            before = kb.as_text(blobs.get(entry.blob, b"")) if entry else None
            agent = None
            if change.sha256 is not None:
                fd = checkpoints.open_object(Entry("", change.sha256, 0, 0, False))
                with os.fdopen(fd, "rb") as handle:
                    raw = kb.as_text(handle.read())
                agent = kb.keep_review_fields(raw, before) if raw is not None else None
            left_out = change.path in edits and edits[change.path] is None
            edited = change.path in edits and edits[change.path] is not None
            after = before if left_out else edits[change.path] if edited else agent
            views.append(FileView(change, before, agent, after, edited, left_out))
        return views

    def _shared_files(self, views: list[FileView]) -> dict[str, bytes | None]:
        return {
            v.change.path: None if v.after is None else v.after.encode()
            for v in views
            if not v.left_out and v.after != v.before
        }

    def preview(self, proposal: Proposal) -> kb.Report:
        """The check, as Save & share would run it first."""
        views = self.files(proposal)
        account = self.auth.status().account if self.auth else None
        with self.clone.lock:
            _, report = share.prepare(
                self.clone,
                share.Share(
                    base=proposal.base,
                    files=self._shared_files(views),
                    author=_DATALAB,
                    reviewer=account.login if account else "you",
                    conversation_id=proposal.conversation_id,
                    proposal_id=proposal.id,
                ),
            )
        return report

    def _event(self, proposal: Proposal) -> dict[str, Any]:
        views = self.files(proposal)
        diffs = [unified_diff(v.change.path, v.before, v.after) for v in views]
        diff, truncated = capped_diffs(diffs)
        report = self.preview(proposal) if views else kb.Report()
        return {
            "id": proposal.id,
            "base": proposal.base,
            "turn": proposal.turn,
            "checkpoint": proposal.checkpoint,
            "files": [
                {
                    "path": v.change.path,
                    "change": v.change.change,
                    "added": diff_stats(d)[0],
                    "removed": diff_stats(d)[1],
                    "flags": list(v.change.flags),
                }
                for v, d in zip(views, diffs, strict=True)
            ],
            "diff": diff,
            "truncated": truncated,
            "refused": [{"path": r.path, "reason": r.reason} for r in proposal.refused],
            "check": {
                "errors": len(report.errors),
                "data": len(report.data),
                "warnings": len(report.warnings),
            },
        }

    def get(self, proposal_id: str) -> Proposal:
        proposal = self.store.get(proposal_id)
        if proposal is None:
            raise NotFound("No such proposal.")
        return proposal

    def edit(self, proposal_id: str, files: dict[str, str | None], reset: list[str]) -> Proposal:
        """The person's own text for some files (None: leave the file out).
        For a file that conflicted, the text is their resolution, against the
        version of `main` it conflicted with."""
        proposal = self.get(proposal_id)
        with self._lock(proposal.conversation_id):
            proposal = self._actionable(proposal_id)
            paths = {c.path for c in proposal.files}
            unknown = sorted(p for p in {*files, *reset} if p not in paths)
            if unknown:
                raise NotActionable(f"Not in this proposal: {', '.join(unknown)}.")
            edits = {
                "files": dict(proposal.edits.get("files", {})),
                "resolutions": dict(proposal.edits.get("resolutions", {})),
                "resolved_against": proposal.edits.get("resolved_against"),
            }
            conflicts = set(proposal.result.get("conflicts") or [])
            for path in reset:
                edits["files"].pop(path, None)
                edits["resolutions"].pop(path, None)
            for path, text in files.items():
                if text is not None and ("\0" in text or len(text.encode()) > kb.size_limit(path)):
                    raise NotActionable(
                        f"{path} must be text under {kb.size_limit(path) // 1024} KB."
                    )
                if proposal.status == "conflict" and path in conflicts and text is not None:
                    edits["resolutions"][path] = text
                    edits["resolved_against"] = proposal.result.get("upstream")
                    edits["files"][path] = text
                else:
                    edits["files"][path] = text
            return self.store.update(proposal, edits=edits)

    def accept(self, proposal_id: str, confirmed: list[str]) -> Proposal:
        """Save & share the proposal as the signed-in person."""
        proposal = self.get(proposal_id)
        with self._lock(proposal.conversation_id):
            proposal = self._actionable(proposal_id)
            if self.auth is None:
                raise NotAvailable(self.unavailable)
            account = self.auth.account()
            if account is None:
                raise SignInNeeded("Sign in to GitHub to share changes.")
            views = self.files(proposal)
            files = self._shared_files(views)
            if not files:
                raise NotActionable("There's nothing in this proposal to save.")
            self.store.update(proposal, status="saving")
            resolutions = {p: t.encode() for p, t in proposal.edits.get("resolutions", {}).items()}
            request = share.Share(
                base=proposal.base,
                files=files,
                author=Identity(account.display_name, account.email),
                reviewer=account.login,
                conversation_id=proposal.conversation_id,
                proposal_id=proposal.id,
                confirmed=confirmed,
                resolutions=resolutions,
                resolved_against=proposal.edits.get("resolved_against"),
            )
            try:
                result = share.save_and_share(self.clone, request)
            except Exception as error:
                log.exception("Save & share failed for %s", proposal.id)
                result = share.SaveResult("failed", f"Saving failed ({type(error).__name__}).")
            status = {
                "saved": "saved",
                "nothing to save": "saved",
                "conflict": "conflict",
                "check_failed": "check_failed",
                "failed": "failed",
            }[result.state]
            if status == "saved":
                try:
                    self._settle(proposal)
                except (GitError, OSError):
                    # Shared anyway; the next proposal may repeat these edits,
                    # and saving it again finds nothing new.
                    log.exception("couldn't move %s's base on", proposal.conversation_id)
                self.store.record_sync(REPO, head=result.commit)
                try:
                    self.clone.fast_forward()
                except GitError as error:
                    log.warning("couldn't fast-forward the knowledge base clone: %s", error)
            proposal = self.store.update(
                proposal,
                status=status,
                result=result.to_dict(),
                commit=result.commit if status == "saved" else None,
                decided_by=account.login,
            )
            self._announce(proposal)
            return proposal

    def reject(self, proposal_id: str) -> Proposal:
        """Discard the proposal: nothing is shared, and the conversation's
        next proposal holds only what the agent changes after this."""
        proposal = self.get(proposal_id)
        with self._lock(proposal.conversation_id):
            proposal = self._actionable(proposal_id)
            self._settle(proposal)
            account = self.auth.status().account if self.auth else None
            proposal = self.store.update(
                proposal,
                status="rejected",
                result={"state": "rejected", "message": "Discarded. Nothing was shared."},
                decided_by=account.login if account else None,
            )
            self._announce(proposal)
            return proposal

    def _settle(self, proposal: Proposal) -> None:
        """Move the conversation's base on: its old base, plus the agent's
        files as this proposal had them."""
        views = self.files(proposal)
        with self.clone.lock:
            checkpoints = self._sessions.checkpoints(proposal.conversation_id)
            agent: dict[str, bytes | None] = {}
            for view in views:
                if view.change.sha256 is None:
                    agent[view.change.path] = None
                elif view.agent is not None:
                    # With the review fields as they were: the agent's own
                    # never become part of any base (or a resolution's text).
                    agent[view.change.path] = view.agent.encode()
                else:
                    fd = checkpoints.open_object(Entry("", view.change.sha256, 0, 0, False))
                    with os.fdopen(fd, "rb") as handle:
                        agent[view.change.path] = handle.read()
            base = self.clone.commit_files(
                proposal.base,
                agent,
                f"What {proposal.conversation_id}'s copy is compared against\n\n"
                f"After proposal {proposal.id}. Never pushed.\n",
                _DATALAB,
            )
            self.clone.set_ref(f"{_BASE_REFS}/{proposal.conversation_id}", base)
        self.store.set_base(proposal.conversation_id, base)

    def _actionable(self, proposal_id: str) -> Proposal:
        proposal = self.get(proposal_id)
        if proposal.status not in ACTIONABLE:
            raise NotActionable(f"This proposal is {proposal.status.replace('_', ' ')}.")
        if proposal.base != self.base(proposal.conversation_id):
            raise NotActionable("This proposal is out of date.")
        return proposal

    def _set_status(self, proposal: Proposal, status: str, message: str) -> None:
        self.store.update(proposal, status=status, result={"state": status, "message": message})
        self._announce(proposal)

    def _announce(self, proposal: Proposal) -> None:
        self._conversations.append(
            proposal.conversation_id,
            "kb_proposal_updated",
            {
                "id": proposal.id,
                "status": proposal.status,
                "message": proposal.result.get("message"),
                "commit": proposal.commit,
            },
        )

    def _lock(self, conversation_id: str) -> threading.Lock:
        with self._locks_lock:
            return self._locks[conversation_id]
