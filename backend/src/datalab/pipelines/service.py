"""The pipelines repo's part of DataLab: the clone, each Data engineering or
Workflow authoring conversation's copy, its proposed changes, the package's
tests, and Save & share.

- **The clone.** `repos.pipelines` (such as SripadaLab-UM/ihs-pipelines),
  cloned into `<data folder>/repos/ihs-pipelines` and synced with GitHub's
  `main` from the Pipelines tab (repos/sync.py). The Workflows tab reads its
  workflow files from the same clone (workflows/source.py).
- **The copy.** A workspace seed copies `main` (as last synced) into each
  new Data engineering or Workflow authoring conversation's `/work/pipelines`,
  once, and records the commit. No `.git`, no credentials, no `.github/`: just the files.
- **Proposals.** After each turn, the copy in the turn's checkpoint is
  compared with the conversation's base (proposals.py). A difference becomes
  a proposal, listed in the Pipelines tab (not in the chat, for now), with
  a commit of the base plus the agent's files that is never pushed: its
  tree is what the tests run on.
- **Tests and Save & share.** The package's tests run on that tree in a
  no-network container (testing.py); Save & share pushes the change only
  once they've passed, and again on the rebased commit if others changed
  the package meanwhile (share.py). Either way, saved or discarded, the
  conversation's base then moves on to the proposal's commit, so the next
  proposal holds only what the agent changes next.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import threading
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datalab.config import Settings
from datalab.knowledge import check as kb
from datalab.knowledge.proposals import Base, Change, Workspace, fingerprint
from datalab.pipelines import share
from datalab.pipelines.check import Report, WorkflowCheck, check
from datalab.pipelines.proposals import (
    ACTIONABLE,
    PipelineStore,
    Proposal,
    TestRun,
    compare,
    copied,
    proposal_problem,
)
from datalab.pipelines.testing import PackageTests
from datalab.repos.git import GitError, Identity, TreeEntry
from datalab.repos.github import GitHubAuth, SignInNeeded
from datalab.repos.sync import RepoSync, SyncState
from datalab.sessions.checkpoints import Entry
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import Conversation, ConversationStore
from datalab.workflows.model import WorkflowInvalid, load_workflow
from datalab.workflows.sandbox import Sandbox
from datalab.workflows.source import pipelines_in

log = logging.getLogger(__name__)

SEED = "pipelines"
INTO = "pipelines"  # /work/pipelines
# Data engineering, and Workflow authoring (whose files are in workflows/).
MODES = ("engineering", "workflows")
REPO = "pipelines"  # its row in repo_sync
_REFS = "refs/datalab/pipelines"  # keeps each proposal's commit, per conversation
_DATALAB = Identity("DataLab", "datalab@localhost")
MAX_BROWSE_FILES = 5000
MAX_READ_BYTES = 1024 * 1024
_KEEP_WORKFLOW_SAVES = 50
UNAVAILABLE_NOTE = """\
# The lab's pipelines repo isn't here

DataLab couldn't copy the pipelines repo (ihsDataR and the workflow files)
into this conversation: it isn't signed in to GitHub yet, or hasn't
downloaded the repo. A new conversation will have it once it's set up
(Pipelines tab).
"""


class NotAvailable(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class NotActionable(RuntimeError):
    """The proposal can't be acted on now (already saved, replaced, being saved…)."""


@dataclass(frozen=True)
class FileView:
    change: Change
    before: str | None  # the base's text
    after: str | None  # the agent's
    binary: bool = False


@dataclass
class WorkflowSave:
    """One Save & share of a new workflow file, while DataLab runs."""

    id: str
    path: str  # in the repo: workflows/<name>.yaml
    state: str  # "saving", then share.SaveState ("already_there" for "nothing to save")
    message: str
    commit: str | None = None  # what was pushed, once saved
    findings: list[dict[str, Any]] = field(default_factory=list)
    test: str | None = None


class Pipelines:
    def __init__(
        self,
        settings: Settings,
        database,
        conversations: ConversationStore,
        sessions: SessionManager,
        *,
        auth: GitHubAuth | None,
        sandbox: Sandbox,
        remote: str | None = None,
    ) -> None:
        self._settings = settings
        repos = settings.repos
        if settings.profile == "practice":
            self.unavailable: str | None = "Practice DataLab doesn't use the lab's repositories."
        elif repos.pipelines is None:
            self.unavailable = (
                "The pipelines repository isn't set in settings.toml ([repos] pipelines)."
            )
        elif auth is None:
            self.unavailable = (
                "The lab's GitHub App isn't set in settings.toml ([repos] client_id)."
            )
        else:
            self.unavailable = None
        self.store = PipelineStore(database)
        self.repo: RepoSync | None = None
        if self.unavailable is None:
            assert auth is not None and repos.pipelines is not None
            self.repo = RepoSync(
                REPO,
                repos.pipelines,
                settings.data_dir,
                auth,
                SyncState(database),
                contact=repos.access_contact,
                remote=remote,
            )
        self.tests = PackageTests(
            sandbox=sandbox,
            image=settings.agent_image,
            store=self.store,
            folder=settings.data_dir / "pipeline-tests",
        )
        self._conversations = conversations
        self._sessions = sessions
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_lock = threading.Lock()
        self._sha256: dict[str, str] = {}  # blob id -> sha256 of its content
        # Test runs going now, by tree (one per tree), and saves, by proposal.
        self._testing: dict[str, asyncio.Task[TestRun]] = {}
        self._saving: dict[str, asyncio.Task[None]] = {}
        self._workflow_saves: dict[str, WorkflowSave] = {}
        if self.repo is not None:
            self.store.end_interrupted()
            self._prune_quietly()
            sessions.register_workspace_seed(SEED, self._seed, into=INTO, modes=MODES)
            sessions.register_after_turn(self._after_turn)

    @property
    def available(self) -> bool:
        return self.repo is not None

    def _repo(self) -> RepoSync:
        if self.repo is None:
            raise NotAvailable(self.unavailable)
        return self.repo

    # Status, sync, browsing ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        if self.repo is None:
            return {"available": False, "repo": "not configured", "message": self.unavailable}
        return self.repo.status()

    def sync(self) -> dict[str, Any]:
        repo = self._repo()
        if repo.sync() is not None:
            self._prune_quietly()
        return repo.status()

    def tree(self) -> tuple[str | None, list[tuple[str, int]], int]:
        """GitHub's `main` as last synced, its files (path, bytes), and how many more there are."""
        repo = self._repo()
        with repo.clone.lock:
            head = repo.clone.remote_head()
            if head is None:
                return None, [], 0
            entries = repo.clone.ls_tree(head)
        files = sorted((p, e.size) for p, e in entries.items() if e.regular)
        return head, files[:MAX_BROWSE_FILES], max(0, len(files) - MAX_BROWSE_FILES)

    def read(self, path: str) -> tuple[str, str | None, int, bool]:
        """A file at `main` as last synced: (head, text or None if it's binary
        or too large, bytes, too large)."""
        repo = self._repo()
        with repo.clone.lock:
            head = repo.clone.remote_head()
            if head is None:
                raise NotFound("The pipelines repo hasn't been downloaded yet.")
            entry = repo.clone.ls_tree(head).get(path)
            if entry is None or not entry.regular:
                raise NotFound(f"There's no file {path} in the pipelines repo.")
            if entry.size > MAX_READ_BYTES:
                return head, None, entry.size, True
            content = repo.clone.read_blobs([entry.blob]).get(entry.blob, b"")
        return head, kb.as_text(content), entry.size, False

    def prune(self) -> int:
        """Remove the refs of conversations that no longer exist (run at start and after a sync)."""
        repo = self.repo
        if repo is None or not repo.clone.exists():
            return 0
        with repo.clone.lock:
            refs = repo.clone.text("for-each-ref", "--format=%(refname)", _REFS)
            gone = [
                ref
                for ref in refs.splitlines()
                if self._conversations.get(ref.removeprefix(_REFS + "/").split("/")[0]) is None
            ]
            for ref in gone:
                repo.clone.git("update-ref", "-d", ref)
        return len(gone)

    def _prune_quietly(self) -> None:
        try:
            self.prune()
        except GitError as error:
            log.warning("couldn't prune the pipelines repo's old proposals: %s", error)

    # The copy in each conversation ----------------------------------------------

    def _seed(self, conversation: Conversation, staging: Path) -> str | None:
        repo = self._repo()
        with repo.clone.lock:
            head = repo.fresh_head()
            if head is None:
                (staging / "README.md").write_text(UNAVAILABLE_NOTE)
                return None
            repo.clone.copy_tree(head, staging, skip=lambda path: not copied(path))
        return head

    def base(self, conversation_id: str) -> str | None:
        """What the conversation's /work/pipelines is compared against now."""
        return self.store.base(conversation_id) or self._sessions.workspace_base(
            conversation_id, SEED
        )

    # Proposals --------------------------------------------------------------

    async def _after_turn(self, conversation_id: str, info: TurnInfo) -> None:
        if info.checkpoint is None:
            return
        await asyncio.to_thread(self.propose, conversation_id, info.turn, info.checkpoint)

    def propose(self, conversation_id: str, turn: int, checkpoint: int) -> Proposal | None:
        """Compare the copy in `checkpoint` with the base; record a new
        proposal if it differs from the last one."""
        repo = self._repo()
        with self._lock(conversation_id):
            base = self.base(conversation_id)
            if base is None:
                return None
            workspace = self._workspace(conversation_id, checkpoint)
            with repo.clone.lock:
                changes, refused = compare(workspace, self._base_view(base, workspace))
            actionable = [p for p in self.store.list(conversation_id) if p.status in ACTIONABLE]
            if not changes and not refused:
                for old in actionable:
                    self._set_status(old, "withdrawn", "The agent undid these changes.")
                return None
            mark = fingerprint(base, changes, refused)
            latest = self.store.latest(conversation_id)
            if latest is not None and latest.fingerprint == mark and latest.status != "withdrawn":
                return None  # nothing new since the last proposal
            files = {c.path: self._agent_bytes(conversation_id, c) for c in changes}
            with repo.clone.lock:
                commit = repo.clone.commit_files(
                    base,
                    files,
                    f"What {conversation_id}'s agent changed, after turn {turn}\n\nNever pushed.\n",
                    _DATALAB,
                )
                tree = repo.clone.text("rev-parse", f"{commit}^{{tree}}")
                repo.clone.set_ref(f"{_REFS}/{conversation_id}/{commit}", commit)
            for old in actionable:
                self._set_status(old, "superseded", "Replaced by a newer proposal.")
            return self.store.add(
                conversation_id,
                turn=turn,
                checkpoint=checkpoint,
                base=base,
                commit=commit,
                tree=tree,
                fingerprint=mark,
                files=changes,
                refused=refused,
            )

    def _workspace(self, conversation_id: str, number: int) -> Workspace:
        checkpoints = self._sessions.checkpoints(conversation_id)
        summary = checkpoints.get(number)
        links = checkpoints.links(number)
        prefix = INTO + "/"
        if summary is None or INTO in links:
            return Workspace({}, [], [], lambda _: b"")
        files = {
            rel[len(prefix) :]: (e.sha256, e.size)
            for rel, e in checkpoints.entries(number).items()
            if rel.startswith(prefix)
        }
        skipped = [
            (s.path[len(prefix) :], s.reason) for s in summary.skipped if s.path.startswith(prefix)
        ]

        def read(digest: str) -> bytes:
            fd = checkpoints.open_object(Entry("", digest, 0, 0, False))
            with os.fdopen(fd, "rb") as handle:
                return handle.read()

        copy_links = sorted(rel[len(prefix) :] for rel in links if rel.startswith(prefix))
        return Workspace(files, copy_links, skipped, read)

    def _base_view(self, base: str, workspace: Workspace) -> Base:
        clone = self._repo().clone
        entries = clone.ls_tree(base)
        wanted = [
            e.blob
            for path, (_, size) in workspace.files.items()
            if (e := entries.get(path)) is not None and e.size == size
            and e.blob not in self._sha256
        ]  # fmt: skip
        for blob, content in clone.read_blobs(wanted).items():
            self._sha256[blob] = hashlib.sha256(content).hexdigest()

        def read(path: str) -> bytes:
            entry = entries[path]
            return clone.read_blobs([entry.blob]).get(entry.blob, b"")

        def sha256(entry: TreeEntry) -> str:
            if entry.blob not in self._sha256:
                content = clone.read_blobs([entry.blob]).get(entry.blob, b"")
                self._sha256[entry.blob] = hashlib.sha256(content).hexdigest()
            return self._sha256[entry.blob]

        return Base(entries, read, sha256)

    def _agent_bytes(self, conversation_id: str, change: Change) -> bytes | None:
        if change.sha256 is None:
            return None
        checkpoints = self._sessions.checkpoints(conversation_id)
        fd = checkpoints.open_object(Entry("", change.sha256, 0, 0, False))
        with os.fdopen(fd, "rb") as handle:
            return handle.read()

    def files(self, proposal: Proposal) -> list[FileView]:
        """Each file of a proposal: the base's text and the agent's."""
        clone = self._repo().clone
        with clone.lock:
            entries = clone.ls_tree(proposal.base)
            blobs = clone.read_blobs(
                entries[c.path].blob for c in proposal.files if c.path in entries
            )
        views = []
        for change in proposal.files:
            entry = entries.get(change.path)
            raw_before = blobs.get(entry.blob, b"") if entry else None
            before = kb.as_text(raw_before) if raw_before is not None else None
            raw_after = self._agent_bytes(proposal.conversation_id, change)
            after = kb.as_text(raw_after) if raw_after is not None else None
            binary = (raw_before is not None and before is None) or (
                raw_after is not None and after is None
            )
            views.append(FileView(change, before, after, binary))
        return views

    def shared_files(self, proposal: Proposal) -> dict[str, bytes | None]:
        return {c.path: self._agent_bytes(proposal.conversation_id, c) for c in proposal.files}

    def preview(self, proposal: Proposal) -> Report:
        """The check, as Save & share would run it first."""
        return check(self.shared_files(proposal), self.workflow_check(proposal.commit))

    def workflow_check(self, commit: str) -> WorkflowCheck:
        """DataLab's workflow check, with pipelines as in `commit`'s tree and
        the real profile's small-cell rule: these files are shared with the
        lab, and run there."""
        clone = self._repo().clone
        entries: dict[str, TreeEntry] | None = None

        def read(path: str) -> bytes | None:
            nonlocal entries
            with clone.lock:
                if entries is None:
                    entries = clone.ls_tree(commit)
                entry = entries.get(path)
                if entry is None or not entry.regular:
                    return None
                return clone.read_blobs([entry.blob]).get(entry.blob)

        lookup = pipelines_in(read)
        oracle = self._settings.oracle

        def problems(text: str) -> list[str]:
            try:
                load_workflow(
                    text,
                    pipelines=lookup,
                    allowed_schemas=oracle.allowed_schemas if oracle else None,
                    require_small_cells=True,
                )
            except WorkflowInvalid as error:
                return [str(p) for p in error.problems]
            return []

        return problems

    def get(self, proposal_id: str) -> Proposal:
        proposal = self.store.get(proposal_id)
        if proposal is None:
            raise NotFound("No such proposal.")
        return proposal

    def latest_test(self, proposal: Proposal) -> TestRun | None:
        return self.store.latest_test(proposal.tree)

    # Tests ------------------------------------------------------------------

    async def start_tests(self, proposal_id: str) -> TestRun:
        """Run the package's tests on a proposal, in the background; the run, as it starts."""
        proposal = await asyncio.to_thread(self._actionable, proposal_id)
        if not proposal.files:
            raise NotActionable("There's nothing in this proposal to test.")
        running = self._testing.get(proposal.tree)
        if running is not None and not running.done():
            found = await asyncio.to_thread(self.store.latest_test, proposal.tree)
            assert found is not None
            return found
        run = await asyncio.to_thread(
            self.tests.begin, proposal.commit, proposal.tree, proposal_id=proposal.id
        )
        self._start_test(run)
        return run

    def _start_test(self, run: TestRun) -> asyncio.Task[TestRun]:
        task = asyncio.create_task(self.tests.execute(run, self._repo().clone))
        self._testing[run.tree] = task
        task.add_done_callback(lambda _: self._testing.pop(run.tree, None))
        return task

    async def _tested(self, commit: str, tree: str, proposal_id: str | None = None) -> TestRun:
        """A passing run on `tree` if there is one, the run going now, or a new run."""
        running = self._testing.get(tree)
        if running is not None and not running.done():
            return await running
        found = await asyncio.to_thread(self.store.latest_test, tree)
        if found is not None and found.status == "passed":
            return found
        run = await asyncio.to_thread(self.tests.begin, commit, tree, proposal_id=proposal_id)
        return await self._start_test(run)

    # Save & share, discard --------------------------------------------------

    async def accept(self, proposal_id: str, confirmed: list[str]) -> Proposal:
        """Start Save & share as the signed-in person; the proposal, now saving.
        It runs the tests first if they haven't passed on this change yet."""
        repo = self._repo()
        account = await asyncio.to_thread(repo.auth.account)
        if account is None:
            raise SignInNeeded("Sign in to GitHub to share changes.")

        def begin() -> tuple[Proposal, dict[str, bytes | None]]:
            with self._lock(self.get(proposal_id).conversation_id):
                proposal = self._actionable(proposal_id)
                files = self.shared_files(proposal)
                if not files:
                    raise NotActionable("There's nothing in this proposal to save.")
                return self.store.update(proposal, status="saving", result={}), files

        proposal, files = await asyncio.to_thread(begin)
        request = share.Share.of_proposal(
            proposal,
            files,
            Identity(account.display_name, account.email),
            account.login,
            confirmed,
            workflows=self.workflow_check,
        )
        task = asyncio.create_task(self._save(proposal, request))
        self._saving[proposal.id] = task
        task.add_done_callback(lambda _: self._saving.pop(proposal.id, None))
        return proposal

    async def _save(self, proposal: Proposal, request: share.Share) -> None:
        repo = self._repo()
        try:
            result = await share.save_and_share(
                repo.clone, request, lambda commit, tree: self._tested(commit, tree, proposal.id)
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.store.update,
                proposal,
                status="failed",
                result={"state": "failed", "message": "Saving was stopped. Try again."},
            )
            raise
        except Exception as error:
            log.exception("Save & share failed for %s", proposal.id)
            result = share.SaveResult("failed", f"Saving failed ({type(error).__name__}).")
        status = {
            "saved": "saved",
            "nothing to save": "saved",
            "conflict": "conflict",
            "check_failed": "check_failed",
            "tests_failed": "tests_failed",
            "failed": "failed",
        }[result.state]

        def finish() -> None:
            if status == "saved":
                self.store.set_base(proposal.conversation_id, proposal.commit)
                repo.saved(result.commit)
            self.store.update(
                proposal,
                status=status,
                result=result.to_dict(),
                saved_commit=result.commit if status == "saved" else None,
                decided_by=request.login,
            )

        await asyncio.to_thread(finish)

    # A workflow file saved from outside a conversation's copy -----------------

    async def share_workflow(
        self,
        path: str,
        content: bytes,
        *,
        confirmed: Collection[str] = (),
        trailers: tuple[tuple[str, str], ...] = (),
    ) -> WorkflowSave:
        """Start Save & share of one new workflow file (`workflows/<name>.yaml`),
        as the signed-in person; the save, as it starts. It's the same Save &
        share as a proposal's (share.py): the check, the package's tests on
        the change's tree, a commit as the person, rebased and pushed.

        Used by Save as workflow in the SQL Playground and Turn this into a
        workflow in a conversation (api/workflows.py), once the person has
        reviewed the file. A file already in the repo isn't replaced."""
        repo = self._repo()
        problem = proposal_problem(path)
        if problem or not path.startswith("workflows/"):
            raise NotActionable(f"{path} can't be saved here.")
        account = await asyncio.to_thread(repo.auth.account)
        if account is None:
            raise SignInNeeded("Sign in to GitHub to share workflows.")

        def begin() -> tuple[str, str, str]:
            with repo.clone.lock:
                base = repo.clone.remote_head()
                if base is None:
                    raise NotAvailable(
                        "The pipelines repo hasn't been downloaded yet: sync it (Pipelines tab)."
                    )
                stem = path.removeprefix("workflows/").rsplit(".", 1)[0].lower()
                taken = {f"workflows/{stem}.yaml", f"workflows/{stem}.yml"}
                clash = next((p for p in repo.clone.ls_tree(base) if p.lower() in taken), None)
                if clash is not None:
                    raise NotActionable(
                        f"{clash} is already there in the pipelines repo. Choose another name."
                    )
                commit = repo.clone.commit_files(
                    base, {path: content}, f"{path}, saved in DataLab\n\nNever pushed.\n", _DATALAB
                )
                tree = repo.clone.text("rev-parse", f"{commit}^{{tree}}")
            return base, commit, tree

        base, commit, tree = await asyncio.to_thread(begin)
        job = WorkflowSave(
            id=f"ws_{secrets.token_hex(8)}",
            path=path,
            state="saving",
            message="Checking, testing and sharing it.",
        )
        request = share.Share(
            base=base,
            commit=commit,
            tree=tree,
            files={path: content},
            author=Identity(account.display_name, account.email),
            login=account.login,
            confirmed=confirmed,
            trailers=trailers,
            subject="Workflows",
            workflows=self.workflow_check,
        )
        self._workflow_saves[job.id] = job
        while len(self._workflow_saves) > _KEEP_WORKFLOW_SAVES:
            del self._workflow_saves[next(iter(self._workflow_saves))]
        task = asyncio.create_task(self._save_workflow(job, request))
        self._saving[job.id] = task
        task.add_done_callback(lambda _: self._saving.pop(job.id, None))
        return job

    def workflow_save(self, save_id: str) -> WorkflowSave:
        found = self._workflow_saves.get(save_id)
        if found is None:
            raise NotFound("No such save. Saves are forgotten when DataLab restarts.")
        return found

    async def _save_workflow(self, job: WorkflowSave, request: share.Share) -> None:
        repo = self._repo()
        try:
            result = await share.save_and_share(
                repo.clone, request, lambda commit, tree: self._tested(commit, tree)
            )
        except asyncio.CancelledError:
            job.state, job.message = "failed", "Saving was stopped. Nothing was shared."
            raise
        except Exception as error:
            log.exception("Save & share failed for %s", job.path)
            result = share.SaveResult("failed", f"Saving failed ({type(error).__name__}).")
        if result.state in ("saved", "nothing to save"):
            await asyncio.to_thread(repo.saved, result.commit)
        job.state = "already_there" if result.state == "nothing to save" else result.state
        job.message = result.message
        job.commit = result.commit if job.state == "saved" else None
        job.findings = result.findings
        job.test = result.test
        if result.state == "nothing to save":
            # Someone else saved the same file meanwhile: it's theirs, not this save's.
            job.message = (
                f"{job.path} was already in the pipelines repo, exactly as this file: someone "
                "saved it meanwhile. Nothing new was shared."
            )
        elif result.state == "conflict":
            job.message = (
                f"Someone else saved {job.path} meanwhile. Nothing was shared: choose another "
                "name and save again."
            )
        elif result.state == "check_failed" and not any(
            f["severity"] == "error" for f in result.findings
        ):
            job.message = (
                "The check found text that may be participant data. Confirm each one isn't, "
                "or change the file, then save again."
            )

    def reject(self, proposal_id: str) -> Proposal:
        """Discard the proposal: nothing is shared, and the conversation's next
        proposal holds only what the agent changes after this."""
        proposal = self.get(proposal_id)
        with self._lock(proposal.conversation_id):
            proposal = self._actionable(proposal_id)
            self.store.set_base(proposal.conversation_id, proposal.commit)
            repo = self._repo()
            account = repo.auth.status().account
            return self.store.update(
                proposal,
                status="rejected",
                result={"state": "rejected", "message": "Discarded. Nothing was shared."},
                decided_by=account.login if account else None,
            )

    async def wait(self) -> None:
        """Until every test run and save going now has finished (for tests and shutdown)."""
        pending = [*self._testing.values(), *self._saving.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _actionable(self, proposal_id: str) -> Proposal:
        proposal = self.get(proposal_id)
        if proposal.status not in ACTIONABLE:
            raise NotActionable(f"This proposal is {proposal.status.replace('_', ' ')}.")
        if proposal.base != self.base(proposal.conversation_id):
            raise NotActionable("This proposal is out of date.")
        return proposal

    def _set_status(self, proposal: Proposal, status: str, message: str) -> None:
        self.store.update(proposal, status=status, result={"state": status, "message": message})

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_lock:
            return self._locks[key]
