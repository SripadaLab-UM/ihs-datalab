"""Save & share a pipeline change: put it on GitHub's `main`, exactly as it passed.

As for the knowledge base (knowledge/share.py), with the package's tests:

1. Check the change (check.py): nothing outside `ihsDataR/` and
   `workflows/`, nothing in `.github/` (the sign-in's Contents permission
   can't change it), the participant-data scan, and code that would run on
   install or load. An error, or a finding the person hasn't confirmed,
   stops here.
2. The tests must have passed on the change's own tree (Save & share runs
   them first if they haven't run yet).
3. Commit it on its base, as the person, with trailers naming where it came
   from (a conversation's proposal, or a workflow saved from the Playground
   or a conversation's queries: api/workflows.py) and the test run.
4. Fetch, and rebase that commit onto GitHub's `main`. Where someone else
   changed the same lines, stop: nothing is pushed.
5. Check the rebased commit again. If its package (the `ihsDataR` tree) isn't
   the one the tests passed on, run them again on it: the tests must pass
   on what's pushed, and its message then names that run.
6. Push exactly that commit, never forcing. If `main` moved on meanwhile,
   go back to 4 (a few times at most).

git runs on worker threads, and never with the clone's lock held while the
tests run (they can take minutes), so syncing and new conversations' copies
aren't held up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from datalab.pipelines.check import Report, check
from datalab.pipelines.proposals import Proposal, TestRun
from datalab.repos.git import Clone, GitError, Identity
from datalab.repos.github import GitHubUnavailable, SignInNeeded
from datalab.workflows.source import PACKAGE

SaveState = Literal[
    "saved", "nothing to save", "conflict", "check_failed", "tests_failed", "failed"
]  # fmt: skip
_ATTEMPTS = 3

# Runs the tests on (commit, tree), or gives the passing run already on that tree.
Tester = Callable[[str, str], Awaitable[TestRun]]


@dataclass(frozen=True)
class SaveResult:
    state: SaveState
    message: str
    commit: str | None = None
    upstream: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    after_rebase: bool = False
    # The test run behind this outcome: the one that passed on what was pushed,
    # or the one that failed.
    test: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "commit": self.commit,
            "upstream": self.upstream,
            "findings": self.findings,
            "conflicts": self.conflicts,
            "after_rebase": self.after_rebase,
            "test": self.test,
        }


@dataclass(frozen=True)
class Share:
    """A change to share: `commit` is `base` with `files` written (never
    pushed); `tree` is its tree, what the tests run on."""

    base: str
    commit: str
    tree: str
    # The files as they'll be shared (None deletes).
    files: dict[str, bytes | None]
    author: Identity
    login: str
    confirmed: Collection[str] = ()
    # The commit message's last lines: where the change came from.
    trailers: tuple[tuple[str, str], ...] = ()
    # The commit message's subject starts with this.
    subject: str = "Pipelines"

    @classmethod
    def of_proposal(
        cls,
        proposal: Proposal,
        files: dict[str, bytes | None],
        author: Identity,
        login: str,
        confirmed: Collection[str] = (),
    ) -> Share:
        """A Data engineering conversation's proposal."""
        return cls(
            base=proposal.base,
            commit=proposal.commit,
            tree=proposal.tree,
            files=files,
            author=author,
            login=login,
            confirmed=confirmed,
            trailers=(
                ("DataLab-Conversation", proposal.conversation_id),
                ("DataLab-Proposal", proposal.id),
            ),
        )


async def save_and_share(clone: Clone, share: Share, tester: Tester) -> SaveResult:
    try:
        return await _save(clone, share, tester)
    except (GitError, SignInNeeded, GitHubUnavailable) as error:
        return SaveResult("failed", str(error))


async def _save(clone: Clone, share: Share, tester: Tester) -> SaveResult:
    report = check(share.files)
    if report.blocking(share.confirmed):
        return _check_failed(report, share.confirmed, after_rebase=False)
    first = await tester(share.commit, share.tree)
    if first.status != "passed":
        return _tests_failed(first, after_rebase=False)
    # The change's own tree, as the person's commit.
    ours = await asyncio.to_thread(
        clone.commit_files, share.base, share.files, _message(share, first), share.author
    )
    tested_package = await asyncio.to_thread(_package_tree, clone, share.commit)
    for _ in range(_ATTEMPTS):
        await asyncio.to_thread(clone.fetch)
        upstream = await asyncio.to_thread(clone.remote_head)
        if upstream is None:
            return SaveResult("failed", "GitHub's pipelines repo has no main branch.")
        rebased = await asyncio.to_thread(
            clone.rebase, ours, onto=upstream, old_base=share.base, committer=share.author
        )
        if rebased.state == "conflict":
            names = ", ".join(rebased.conflicts)
            return SaveResult(
                "conflict",
                f"Someone else changed the same lines meanwhile ({names}). Nothing was "
                "shared. Discard this change, and ask the agent in a new conversation to "
                "make it again on the latest version.",
                upstream=upstream,
                conflicts=rebased.conflicts,
            )
        if rebased.state == "failed":
            return SaveResult("failed", rebased.message or "The rebase failed.", upstream=upstream)
        if rebased.state == "empty" or rebased.commit is None:
            return SaveResult(
                "nothing to save",
                "These changes are already in the pipelines repo.",
                commit=upstream,
                upstream=upstream,
            )
        candidate = rebased.commit
        # Checked again as it will be pushed: what it changes on top of main.
        changed = await asyncio.to_thread(clone.changed_paths, upstream, candidate)
        contents = await asyncio.to_thread(_contents, clone, candidate, changed)
        again = check(contents)
        if again.blocking(share.confirmed):
            return _check_failed(again, share.confirmed, after_rebase=upstream != share.base)
        # The package as it will be pushed, if others' changes made it another
        # one than the tests passed on: tested again, and the message says so.
        tested = first
        if await asyncio.to_thread(_package_tree, clone, candidate) != tested_package:
            tree = await asyncio.to_thread(clone.text, "rev-parse", f"{candidate}^{{tree}}")
            tested = await tester(candidate, tree)
            if tested.status != "passed":
                return _tests_failed(tested, after_rebase=True)
            if tested.id != first.id:
                candidate = await asyncio.to_thread(
                    clone.commit_files,
                    upstream,
                    {},
                    _message(share, tested),
                    share.author,
                    tree_of=candidate,
                )
        pushed = await asyncio.to_thread(clone.push, candidate)
        if pushed.state == "pushed":
            return SaveResult(
                "saved",
                "Saved and shared with the lab.",
                commit=candidate,
                upstream=upstream,
                test=tested.id,
            )
        if pushed.state == "failed":
            return SaveResult("failed", _push_message(pushed.message), upstream=upstream)
        # Rejected: main moved on while we checked or tested. Fetch and go again.
    return SaveResult("failed", "GitHub's pipelines repo kept changing. Try again in a moment.")


def _package_tree(clone: Clone, commit: str) -> str | None:
    """The id of the package's tree in `commit`: the same id, the same files."""
    done = clone.git("rev-parse", "--verify", "-q", f"{commit}:{PACKAGE}", check=False)
    if done.returncode != 0:
        return None
    return done.stdout.decode().strip() or None


def _contents(clone: Clone, commit: str, paths: list[str]) -> dict[str, bytes | None]:
    entries = clone.ls_tree(commit)
    blobs = clone.read_blobs(entries[p].blob for p in paths if p in entries)
    return {p: blobs.get(entries[p].blob, b"") if p in entries else None for p in paths}


def _check_failed(report: Report, confirmed: Collection[str], *, after_rebase: bool) -> SaveResult:
    blocking = report.blocking(confirmed)
    if report.errors:
        message = "The check found problems to fix before this can be shared."
    else:
        message = (
            "The check found things for you to confirm: text that may be participant "
            "data, or code that would run on everyone's computer. Confirm each one, or "
            "ask the agent to change it, then save again."
        )
    if after_rebase:
        message = "With the changes others saved meanwhile, " + message[0].lower() + message[1:]
    return SaveResult(
        "check_failed",
        message,
        findings=[f.to_dict() for f in blocking],
        after_rebase=after_rebase,
    )


def _tests_failed(run: TestRun, *, after_rebase: bool) -> SaveResult:
    message = run.message or "The tests didn't pass."
    if after_rebase:
        message = f"With the changes others saved meanwhile, the tests didn't pass: {message}"
    else:
        message = f"The tests didn't pass, so nothing was shared: {message}"
    return SaveResult("tests_failed", message, after_rebase=after_rebase, test=run.id)


def _message(share: Share, tested: TestRun) -> str:
    changed = sorted(share.files)
    subject = f"{share.subject}: {', '.join(p.split('/')[-1] for p in changed)}"
    if len(subject) > 72:
        subject = f"{share.subject}: {len(changed)} files"
    body = "\n".join(f"- {'deleted' if share.files[p] is None else 'updated'} {p}" for p in changed)
    passed = tested.summary.get("tests", 0)
    return (
        f"{subject}\n\n"
        f"Reviewed and saved in DataLab by {share.author.name} (@{share.login}).\n"
        f"The package's {passed} tests passed on this change.\n\n"
        f"{body}\n\n"
        + "".join(f"{key}: {value}\n" for key, value in share.trailers)
        + f"DataLab-Tests: {tested.id}\n"
    )


def _push_message(message: str) -> str:
    if "workflow" in message:
        return (
            "GitHub refused the push: DataLab's sign-in can't change the repo's "
            "automation (.github/workflows)."
        )
    if "403" in message or "denied" in message.lower():
        return "GitHub refused the push: your account can read the pipelines repo but not write it."
    return message
