"""Proposed edits: a conversation's `/work/kb` compared with what it came from.

After each turn DataLab compares the copy in that turn's checkpoint (never
the live folder) with the conversation's base: the commit the copy was made
from, moved on as proposals are saved or discarded. Whatever differs is one
proposal, which replaces any earlier one still open.

Some differences can't be proposed, and are listed with the reason instead:

- paths outside the knowledge base's layout, and the parts DataLab writes
  itself (`index.md`, `generated/`) or that change outside it (`.github/`);
- links, and anything that isn't a plain file;
- binary files, and files over the size limit (or too large to checkpoint).

`reviewed_by` and `reviewed_on` are never taken from the agent: DataLab
fills them in from the person who saves (check.py, `keep_review_fields`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from datalab.knowledge import check as kb
from datalab.repos.git import TreeEntry, safe_path

ProposalStatus = Literal[
    "open", "superseded", "withdrawn", "rejected", "saving", "saved",
    "conflict", "check_failed", "failed",
]  # fmt: skip
# A person may still act on these.
ACTIONABLE = ("open", "conflict", "check_failed", "failed")

MAX_DIFF_PER_FILE = 64 * 1024
MAX_DIFF_TOTAL = 256 * 1024


def copied(path: str) -> bool:
    """Whether a path of the repo goes into each conversation's copy: not the
    repo's automation, and not a lab skill named like one of DataLab's or
    Codex's own (it could stand in for it)."""
    return not path.startswith(".github/") and not kb.reserved_skill(path)


@dataclass(frozen=True)
class Change:
    path: str
    change: Literal["added", "modified", "deleted"]
    # The agent's file in the checkpoint store (None when deleted).
    sha256: str | None
    size: int
    # Changes to the fields only people set (status, reviewed_by, …).
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change": self.change,
            "sha256": self.sha256,
            "size": self.size,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Change:
        return cls(raw["path"], raw["change"], raw["sha256"], raw["size"], tuple(raw["flags"]))


@dataclass(frozen=True)
class Refusal:
    path: str
    reason: str


@dataclass(frozen=True)
class Workspace:
    """The copy at /work/kb as one checkpoint recorded it (paths relative to kb/)."""

    files: Mapping[str, tuple[str, int]]  # path -> (sha256, size)
    links: list[str]
    skipped: list[tuple[str, str]]  # path, why the checkpoint couldn't save it
    read: Callable[[str], bytes]  # by sha256


@dataclass(frozen=True)
class Base:
    """The tree the copy is compared against."""

    entries: Mapping[str, TreeEntry]
    read: Callable[[str], bytes]  # by path
    sha256: Callable[[TreeEntry], str]


def compare(workspace: Workspace, base: Base) -> tuple[list[Change], list[Refusal]]:
    """What the agent changed, and what it changed that can't be proposed."""
    refused = [
        Refusal(p, "it's a link; the knowledge base holds only plain files")
        for p in workspace.links
    ]
    refused += [Refusal(p, f"DataLab couldn't check it ({why})") for p, why in workspace.skipped]
    unreadable = {r.path for r in refused}
    if not workspace.files and not workspace.links:
        # The copy is gone or emptied: never propose deleting everything.
        return [], [Refusal("", "/work/kb was removed or emptied, so nothing is proposed")]
    changes: list[Change] = []
    for path, (digest, size) in sorted(workspace.files.items()):
        known = base.entries.get(path)
        same_size = known is not None and known.regular and known.size == size
        if same_size and known is not None and base.sha256(known) == digest:
            continue
        problem = kb.proposal_problem(path)
        if problem or not safe_path(path):
            refused.append(Refusal(path, problem or "its name can't be used on every computer"))
            continue
        if size > kb.size_limit(path):
            refused.append(Refusal(path, f"it's over the {kb.size_limit(path) // 1024} KB limit"))
            continue
        text = kb.as_text(workspace.read(digest))
        if text is None:
            refused.append(Refusal(path, "it isn't a text file"))
            continue
        old = _text(base.read(path)) if known is not None and known.regular else None
        if old is not None and kb.keep_review_fields(text, old) == old:
            continue  # only fields DataLab sets itself changed
        flags = tuple(kb.review_changes(old, text))
        changes.append(
            Change(path, "modified" if old is not None else "added", digest, size, flags)
        )
    for path, known in sorted(base.entries.items()):
        if path in workspace.files or not copied(path) or not known.regular:
            continue
        if path in unreadable or any(path.startswith(p.rstrip("/") + "/") for p in unreadable):
            continue  # hidden behind a link or unreadable folder: not a deletion
        problem = kb.proposal_problem(path)
        if problem:
            refused.append(Refusal(path, f"it was deleted, but {problem}"))
            continue
        changes.append(Change(path, "deleted", None, 0))
    return changes, _grouped(refused)


def _grouped(refused: list[Refusal], most: int = 10) -> list[Refusal]:
    """Many refusals in one folder (a `.git` the agent made, say) as one line."""
    by_folder: dict[str, list[Refusal]] = {}
    for refusal in refused:
        folder = refusal.path.split("/")[0] if "/" in refusal.path else ""
        by_folder.setdefault(folder, []).append(refusal)
    out: list[Refusal] = []
    for folder, group in by_folder.items():
        if folder and len(group) > most:
            reasons = sorted({r.reason for r in group})
            reason = reasons[0] if len(reasons) == 1 else "they can't be proposed"
            out.append(Refusal(f"{folder}/", f"{len(group)} files: {reason}"))
        else:
            out.extend(group)
    return out


def fingerprint(base: str, changes: list[Change], refused: list[Refusal]) -> str:
    raw = json.dumps(
        [
            base,
            [(c.path, c.change, c.sha256) for c in changes],
            [(r.path, r.reason) for r in refused],
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def unified_diff(path: str, old: str | None, new: str | None) -> str:
    before = _lines(old)
    after = _lines(new)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{path}" if old is not None else "/dev/null",
            tofile=f"b/{path}" if new is not None else "/dev/null",
        )
    )


def capped_diffs(diffs: list[str]) -> tuple[str, bool]:
    """The diffs joined, each and all within the size caps; whether cut."""
    out: list[str] = []
    total = 0
    truncated = False
    for diff in diffs:
        if len(diff) > MAX_DIFF_PER_FILE:
            diff = diff[:MAX_DIFF_PER_FILE].rsplit("\n", 1)[0] + "\n… (this file's diff is cut)\n"
            truncated = True
        if total + len(diff) > MAX_DIFF_TOTAL:
            out.append("… (more changes: open the proposal to see them all)\n")
            truncated = True
            break
        out.append(diff)
        total += len(diff)
    return "".join(out), truncated


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return added, removed


def _lines(text: str | None) -> list[str]:
    if text is None:
        return []
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def _text(content: bytes) -> str | None:
    return kb.as_text(content)


# Storage --------------------------------------------------------------------


@dataclass
class Proposal:
    id: str
    conversation_id: str
    created_at: str
    updated_at: str
    turn: int
    checkpoint: int
    base: str
    fingerprint: str
    status: ProposalStatus
    files: list[Change]
    refused: list[Refusal]
    # The person's changes: {"files": {path: text or None (leave it out)},
    # "resolutions": {path: text}, "resolved_against": commit}.
    edits: dict[str, Any] = field(default_factory=dict)
    # How the last Save & share went (state, message, findings, conflicts).
    result: dict[str, Any] = field(default_factory=dict)
    commit: str | None = None
    decided_by: str | None = None


class ProposalStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def add(
        self,
        conversation_id: str,
        *,
        turn: int,
        checkpoint: int,
        base: str,
        fingerprint: str,
        files: list[Change],
        refused: list[Refusal],
    ) -> Proposal:
        now = _now()
        proposal = Proposal(
            id=f"kp_{secrets.token_hex(8)}",
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
            turn=turn,
            checkpoint=checkpoint,
            base=base,
            fingerprint=fingerprint,
            status="open",
            files=files,
            refused=refused,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO kb_proposals (id, conversation_id, created_at, updated_at, turn, "
                "checkpoint, base, fingerprint, status, files_json, refused_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (
                    proposal.id, conversation_id, now, now, turn, checkpoint, base, fingerprint,
                    json.dumps([c.to_dict() for c in files]),
                    json.dumps([{"path": r.path, "reason": r.reason} for r in refused]),
                ),
            )  # fmt: skip
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM kb_proposals WHERE id = ?", (proposal_id,))
            found = row.fetchone()
        return _proposal(found) if found else None

    def list(self, conversation_id: str | None = None) -> list[Proposal]:
        query = "SELECT * FROM kb_proposals"
        args: tuple[str, ...] = ()
        if conversation_id is not None:
            query += " WHERE conversation_id = ?"
            args = (conversation_id,)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY created_at DESC, rowid DESC", args)
            return [_proposal(r) for r in rows.fetchall()]

    def latest(self, conversation_id: str) -> Proposal | None:
        found = self.list(conversation_id)
        return found[0] if found else None

    def update(self, proposal: Proposal, **changes: Any) -> Proposal:
        for key, value in changes.items():
            setattr(proposal, key, value)
        proposal.updated_at = _now()
        with self._lock:
            self._db.execute(
                "UPDATE kb_proposals SET status = ?, edits_json = ?, result_json = ?, "
                "commit_sha = ?, decided_by = ?, updated_at = ? WHERE id = ?",
                (
                    proposal.status,
                    json.dumps(proposal.edits),
                    json.dumps(proposal.result),
                    proposal.commit,
                    proposal.decided_by,
                    proposal.updated_at,
                    proposal.id,
                ),
            )
        return proposal

    def end_interrupted_saves(self) -> list[Proposal]:
        """A save cut off by DataLab stopping didn't finish: say so, so it can
        be tried again. (If its push had landed, trying again finds nothing
        left to save.) The proposals it ended."""
        result = json.dumps(
            {"state": "failed", "message": "DataLab stopped while saving. Try again."}
        )
        with self._lock:
            ids = [
                row[0]
                for row in self._db.execute("SELECT id FROM kb_proposals WHERE status = 'saving'")
            ]
            self._db.execute(
                "UPDATE kb_proposals SET status = 'failed', result_json = ?, updated_at = ? "
                "WHERE status = 'saving'",
                (result, _now()),
            )
        return [p for p in (self.get(i) for i in ids) if p is not None]

    # Each conversation's base ---------------------------------------------

    def base(self, conversation_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT base FROM kb_bases WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        return row[0] if row else None

    def set_base(self, conversation_id: str, base: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO kb_bases VALUES (?, ?, ?) ON CONFLICT (conversation_id) "
                "DO UPDATE SET base = excluded.base, updated_at = excluded.updated_at",
                (conversation_id, base, _now()),
            )

    # Sync state -------------------------------------------------------------

    def sync_state(self, repo: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM repo_sync WHERE repo = ?", (repo,)).fetchone()
        return dict(row) if row else {}

    def record_sync(self, repo: str, *, head: str | None = None, error: str | None = None) -> None:
        now = _now()
        with self._lock:
            if error is None:
                self._db.execute(
                    "INSERT INTO repo_sync (repo, head, synced_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (repo) DO UPDATE SET head = excluded.head, "
                    "synced_at = excluded.synced_at, error = NULL, error_at = NULL",
                    (repo, head, now),
                )
            else:
                self._db.execute(
                    "INSERT INTO repo_sync (repo, error, error_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (repo) DO UPDATE SET error = excluded.error, "
                    "error_at = excluded.error_at",
                    (repo, error, now),
                )


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(
        id=row["id"],
        conversation_id=row["conversation_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        turn=row["turn"],
        checkpoint=row["checkpoint"],
        base=row["base"],
        fingerprint=row["fingerprint"],
        status=row["status"],
        files=[Change.from_dict(c) for c in json.loads(row["files_json"])],
        refused=[Refusal(r["path"], r["reason"]) for r in json.loads(row["refused_json"])],
        edits=json.loads(row["edits_json"]),
        result=json.loads(row["result_json"]),
        commit=row["commit_sha"],
        decided_by=row["decided_by"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
