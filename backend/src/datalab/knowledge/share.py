"""Save & share: put a reviewed proposal on GitHub's `main`, exactly as it passed.

1. Check the change (the files as the person will share them, including
   their edits), with the participant-data scan. An error, or a data hit the
   person hasn't confirmed as a false positive, stops here.
2. Commit it on the conversation's base, as the person (their GitHub name
   and private address), with trailers naming the conversation and the
   proposal.
3. Fetch, and rebase that commit onto GitHub's `main`. Where someone else
   changed the same lines, stop with the files that conflict: nothing is
   pushed, and the person can resolve them (their text for each file,
   written against that version of `main`) and try again.
4. Fill in `reviewed_by`/`reviewed_on` on the reviewed pages it changes, and
   rewrite `index.md`, on the rebased commit. (Before the rebase, they would
   collide with the same lines written by earlier saves.)
5. Check that commit again, since others' changes are now in it.
6. Push exactly that commit, never forcing. If `main` moved on meanwhile,
   go back to 3 (a few times at most).
"""

from __future__ import annotations

import datetime
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from datalab.knowledge import check as kb
from datalab.repos.git import Clone, GitError, Identity
from datalab.repos.github import GitHubUnavailable, SignInNeeded

SaveState = Literal["saved", "nothing to save", "conflict", "check_failed", "failed"]
_ATTEMPTS = 3


@dataclass(frozen=True)
class SaveResult:
    state: SaveState
    message: str
    # What was pushed (saved), or what `main` was when it stopped.
    commit: str | None = None
    upstream: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    # True when the check failed only once others' changes were included.
    after_rebase: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "commit": self.commit,
            "upstream": self.upstream,
            "findings": self.findings,
            "conflicts": self.conflicts,
            "after_rebase": self.after_rebase,
        }


@dataclass(frozen=True)
class Share:
    """One Save & share: what's saved, by whom, from where."""

    base: str
    # The files as they'll be shared (None deletes), before DataLab stamps them.
    files: Mapping[str, bytes | None]
    author: Identity
    reviewer: str  # the GitHub login recorded in reviewed_by
    # Where it came from: a conversation (empty for a person's own edit in
    # the Knowledge tab), and the proposal's or the edit's id.
    conversation_id: str
    proposal_id: str
    confirmed: Collection[str] = ()
    # The person's text for files that conflicted, written against `resolved_against`.
    resolutions: Mapping[str, bytes] = field(default_factory=dict)
    resolved_against: str | None = None
    # A person's own edit of a page (the Knowledge tab's Edit page): if
    # anyone else changed one of its files on GitHub since `base`, stop with a
    # conflict instead of merging, so the person sees both versions first.
    strict: bool = False
    today: datetime.date = field(default_factory=datetime.date.today)


def tree_files(
    clone: Clone, commit: str, overlay: Mapping[str, bytes | None] | None = None
) -> tuple[dict[str, bytes], list[str]]:
    """A commit's regular files (with `overlay` applied), and its other paths."""
    entries = clone.ls_tree(commit)
    regular = {p: e for p, e in entries.items() if e.regular}
    blobs = clone.read_blobs(e.blob for e in regular.values())
    files = {p: blobs.get(e.blob, b"") for p, e in regular.items()}
    for path, content in (overlay or {}).items():
        if content is None:
            files.pop(path, None)
        else:
            files[path] = content
    return files, [p for p, e in entries.items() if not e.regular]


def _stamped(
    files: Mapping[str, bytes | None],
    share: Share,
    pages: Collection[str],
    reference: Mapping[str, bytes],
) -> dict[str, bytes | None]:
    """`files` with the review fields of `pages` as DataLab sets them: as in
    `reference` (the version they're saved over), whatever the text says
    (the agent's, a person's edit, or a conflict resolution), and then, on a
    reviewed page, who saved it and when."""
    out = dict(files)
    for path in pages:
        content = out.get(path)
        text = kb.as_text(content) if content is not None else None
        if text is None or kb.place(path) != "page":
            continue
        old = reference.get(path)
        text = kb.keep_review_fields(text, kb.as_text(old) if old is not None else None)
        out[path] = kb.stamp_review(text, share.reviewer, share.today).encode()
    return out


def prepare(clone: Clone, share: Share) -> tuple[dict[str, bytes], kb.Report]:
    """The knowledge base as it would be with this change on the
    conversation's base (stamped, with index.md), and its check."""
    base, _ = tree_files(clone, share.base)
    files, others = tree_files(clone, share.base, share.files)
    changed = [p for p, c in share.files.items() if c is not None]
    stamped = _stamped(files, share, changed, base)
    files = {p: c for p, c in stamped.items() if c is not None}
    report = kb.check(files, others=others, only=set(share.files))
    files["index.md"] = report.index.encode()
    return files, report


def save_and_share(clone: Clone, share: Share) -> SaveResult:
    try:
        with clone.lock:
            return _save(clone, share)
    except (GitError, SignInNeeded, GitHubUnavailable) as error:
        return SaveResult("failed", str(error))


def _save(clone: Clone, share: Share) -> SaveResult:
    _, report = prepare(clone, share)
    if report.blocking(share.confirmed):
        return _check_failed(report, share, after_rebase=False)
    message = _message(share)
    ours = clone.commit_files(share.base, share.files, message, share.author)
    for _ in range(_ATTEMPTS):
        clone.fetch()
        upstream = clone.remote_head()
        if upstream is None:
            return SaveResult("failed", "GitHub's knowledge base has no main branch.")
        moved = _changed_since(clone, share, upstream) if share.strict else []
        if moved:
            return SaveResult(
                "conflict",
                f"Someone else changed {', '.join(moved)} on GitHub since you started editing. "
                "Nothing was shared. Compare the versions, then save again.",
                upstream=upstream,
                conflicts=moved,
            )
        rebased = clone.rebase(
            ours,
            onto=upstream,
            old_base=share.base,
            committer=share.author,
            resolutions=_still_valid(clone, share, upstream),
        )
        if rebased.state == "conflict":
            names = ", ".join(rebased.conflicts)
            return SaveResult(
                "conflict",
                f"Someone else changed the same lines meanwhile ({names}). "
                "Nothing was shared. Compare the two versions and choose the text to keep.",
                upstream=upstream,
                conflicts=rebased.conflicts,
            )
        if rebased.state == "failed":
            # Nothing was shared; the proposal and its edits are kept.
            return SaveResult("failed", rebased.message or "The rebase failed.", upstream=upstream)
        if rebased.state == "empty" or rebased.commit is None:
            return SaveResult(
                "nothing to save",
                "These changes are already in the knowledge base.",
                commit=upstream,
                upstream=upstream,
            )
        candidate = _finish(clone, rebased.commit, upstream, share, message)
        # Checked again as it will be pushed: others' changes are in it now.
        files, others = tree_files(clone, candidate)
        again = kb.check(files, others=others, only=set(clone.changed_paths(upstream, candidate)))
        if again.blocking(share.confirmed):
            return _check_failed(again, share, after_rebase=upstream != share.base)
        pushed = clone.push(candidate)
        if pushed.state == "pushed":
            return SaveResult(
                "saved", "Saved and shared with the lab.", commit=candidate, upstream=upstream
            )
        if pushed.state == "failed":
            return SaveResult("failed", _push_message(pushed.message), upstream=upstream)
        # Rejected: main moved on while we checked. Fetch and go again.
    return SaveResult("failed", "GitHub's knowledge base kept changing. Try again in a moment.")


def _finish(clone: Clone, commit: str, upstream: str, share: Share, message: str) -> str:
    """The rebased commit, with the review stamps and a fresh index.md."""
    files, others = tree_files(clone, commit)
    theirs, _ = tree_files(clone, upstream)
    changed = [p for p, c in share.files.items() if c is not None and p in files]
    stamped = _stamped(files, share, changed, theirs)
    overlay = {p: c for p, c in stamped.items() if c is not None and c != files.get(p)}
    final = {p: c for p, c in {**files, **overlay}.items() if c is not None}
    index = kb.check(final, others=others, only=set()).index.encode()
    if files.get("index.md") != index:
        overlay["index.md"] = index
    if not overlay:
        return commit
    return clone.commit_files(upstream, dict(overlay), message, share.author, tree_of=commit)


def _changed_since(clone: Clone, share: Share, upstream: str) -> list[str]:
    """The files of `share` whose version on `upstream` isn't the one at `base`."""
    if upstream == share.base:
        return []
    now, then = clone.ls_tree(upstream), clone.ls_tree(share.base)
    return sorted(
        path
        for path in share.files
        if (now.get(path) and now[path].blob) != (then.get(path) and then[path].blob)
    )


def _still_valid(clone: Clone, share: Share, upstream: str) -> dict[str, bytes]:
    """Resolutions still fit only where `main`'s version of the file is the
    one the person resolved against."""
    if not share.resolutions or share.resolved_against is None:
        return {}
    if share.resolved_against == upstream:
        return dict(share.resolutions)
    now, then = clone.ls_tree(upstream), clone.ls_tree(share.resolved_against)
    return {
        path: text
        for path, text in share.resolutions.items()
        if (now.get(path) and now[path].blob) == (then.get(path) and then[path].blob)
    }


def _check_failed(report: kb.Report, share: Share, *, after_rebase: bool) -> SaveResult:
    blocking = report.blocking(share.confirmed)
    unconfirmed = [f for f in blocking if f.severity == "data"]
    if report.errors:
        message = "The check found problems to fix before this can be shared."
    else:
        message = (
            "The check found text that may be participant data. Confirm each one is "
            "a false positive, or edit it out, then save again."
        )
    if after_rebase:
        message = "With the changes others saved meanwhile, " + message[0].lower() + message[1:]
    shown = report.errors + unconfirmed + report.warnings
    return SaveResult(
        "check_failed",
        message,
        findings=[f.to_dict() for f in shown],
        after_rebase=after_rebase,
    )


def _message(share: Share) -> str:
    final = share.files
    changed = sorted(final)
    names = [p.removesuffix(".md") for p in changed]
    subject = f"Knowledge: {', '.join(names)}"
    if len(subject) > 72:
        subject = f"Knowledge: {len(changed)} files"
    body = "\n".join(f"- {'deleted' if final[p] is None else 'updated'} {p}" for p in changed)
    how = "Reviewed and saved" if share.conversation_id else "Edited and saved"
    trailers = f"DataLab-Conversation: {share.conversation_id}\n" if share.conversation_id else ""
    label = "DataLab-Edit" if share.proposal_id.startswith("ke_") else "DataLab-Proposal"
    return (
        f"{subject}\n\n"
        f"{how} in DataLab by {share.author.name} (@{share.reviewer}).\n\n"
        f"{body}\n\n"
        f"{trailers}"
        f"{label}: {share.proposal_id}\n"
    )


def _push_message(message: str) -> str:
    if "workflow" in message:
        return (
            "GitHub refused the push: DataLab's sign-in can't change the repo's "
            "automation (.github/workflows)."
        )
    if "403" in message or "denied" in message.lower():
        return "GitHub refused the push: your account can read the knowledge base but not write it."
    return message
