"""Proposed changes: a conversation's `/work/pipelines` compared with what it came from.

As for the knowledge base (knowledge/proposals.py, whose comparison pieces
this reuses): after each turn the copy in that turn's checkpoint, never the
live folder, is compared with the conversation's base, and whatever differs
is one proposal, replacing any earlier one still open.

What can be proposed is narrower than what's in the copy:

- only files in `ihsDataR/` (the package) and `workflows/` (the workflow
  files). The rest of the repo (`AGENTS.md`, `reference/`) is there to read;
- never `.github/`: DataLab's GitHub sign-in may change a repo's contents but
  not its automation, and the copy leaves it out;
- plain text files only, under the size limit, with names every disk takes.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from datalab.knowledge import check as kb
from datalab.knowledge.proposals import Base, Change, Refusal, Workspace
from datalab.repos.git import name_problem, safe_path

# Where proposals may change the repo.
EDITABLE = ("ihsDataR/", "workflows/")
MAX_FILE_BYTES = 512 * 1024

ProposalStatus = Literal[
    "open", "superseded", "withdrawn", "rejected", "saving", "saved",
    "conflict", "check_failed", "tests_failed", "failed",
]  # fmt: skip
# A person may still act on these.
ACTIONABLE = ("open", "conflict", "check_failed", "tests_failed", "failed")
TestStatus = Literal["running", "passed", "failed", "error"]


def copied(path: str) -> bool:
    """Whether a path of the repo goes into each conversation's copy: all but
    its automation, and names not every disk takes (such as a `.gitignore`
    below the top level, which `Clone.copy_tree` leaves out too)."""
    return not path.startswith(".github/") and safe_path(path)


def proposal_problem(path: str) -> str | None:
    """Why a change to `path` can't be proposed, or None."""
    if path.startswith(".github/"):
        return (
            "DataLab can't change the repo's automation (.github/): its GitHub sign-in "
            "may change the repo's contents only"
        )
    if not path.startswith(EDITABLE):
        return "only changes to ihsDataR/ and workflows/ are proposed; the rest is there to read"
    return name_problem(path)


def compare(workspace: Workspace, base: Base) -> tuple[list[Change], list[Refusal]]:
    """What the agent changed, and what it changed that can't be proposed."""
    refused = [Refusal(p, "it's a link; the repo holds only plain files") for p in workspace.links]
    refused += [Refusal(p, f"DataLab couldn't check it ({why})") for p, why in workspace.skipped]
    unreadable = {r.path for r in refused}
    if not workspace.files and not workspace.links:
        return [], [Refusal("", "/work/pipelines was removed or emptied, so nothing is proposed")]
    changes: list[Change] = []
    for path, (digest, size) in sorted(workspace.files.items()):
        known = base.entries.get(path)
        same_size = known is not None and known.regular and known.size == size
        if same_size and known is not None and base.sha256(known) == digest:
            continue
        problem = proposal_problem(path)
        if problem:
            refused.append(Refusal(path, problem))
            continue
        if size > MAX_FILE_BYTES:
            refused.append(Refusal(path, f"it's over the {MAX_FILE_BYTES // 1024} KB limit"))
            continue
        if kb.as_text(workspace.read(digest)) is None:
            refused.append(Refusal(path, "it isn't a text file"))
            continue
        exists = known is not None and known.regular
        changes.append(Change(path, "modified" if exists else "added", digest, size))
    for path, known in sorted(base.entries.items()):
        if path in workspace.files or not copied(path) or not known.regular:
            continue
        if path in unreadable or any(path.startswith(p.rstrip("/") + "/") for p in unreadable):
            continue  # hidden behind a link or an unreadable folder: not a deletion
        problem = proposal_problem(path)
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


def data_findings(files: dict[str, bytes | None]) -> kb.Report:
    """The participant-data scan (the knowledge base's) over the changed files."""
    report = kb.Report()
    for path, content in sorted(files.items()):
        if content is None:
            continue
        report.findings += kb.name_findings(path)
        text = kb.as_text(content)
        if text is not None:
            report.findings += kb.data_findings(path, text)
    return report


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
    # The commit of the base with the agent's files (never pushed), and its tree.
    commit: str
    tree: str
    fingerprint: str
    status: ProposalStatus
    files: list[Change]
    refused: list[Refusal]
    # How the last Save & share went (state, message, findings, conflicts, test).
    result: dict[str, Any] = field(default_factory=dict)
    saved_commit: str | None = None
    decided_by: str | None = None


@dataclass
class TestRun:
    id: str
    tree: str
    proposal_id: str | None
    commit: str | None
    status: TestStatus
    started_at: str
    finished_at: str | None = None
    image_digest: str | None = None
    # {tests, passed, failed, skipped, errors, warnings, files: [...], failures: [...]}
    summary: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


class PipelineStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    # Proposals ----------------------------------------------------------------

    def add(
        self,
        conversation_id: str,
        *,
        turn: int,
        checkpoint: int,
        base: str,
        commit: str,
        tree: str,
        fingerprint: str,
        files: list[Change],
        refused: list[Refusal],
    ) -> Proposal:
        now = _now()
        proposal = Proposal(
            id=f"pp_{secrets.token_hex(8)}",
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
            turn=turn,
            checkpoint=checkpoint,
            base=base,
            commit=commit,
            tree=tree,
            fingerprint=fingerprint,
            status="open",
            files=files,
            refused=refused,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO pipeline_proposals (id, conversation_id, created_at, updated_at, "
                "turn, checkpoint, base, tree, fingerprint, status, files_json, refused_json, "
                "result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (
                    proposal.id, conversation_id, now, now, turn, checkpoint, base, tree,
                    fingerprint, json.dumps([c.to_dict() for c in files]),
                    json.dumps([{"path": r.path, "reason": r.reason} for r in refused]),
                    json.dumps({"proposal_commit": commit}),
                ),
            )  # fmt: skip
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            found = self._db.execute(
                "SELECT * FROM pipeline_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        return _proposal(found) if found else None

    def list(self, conversation_id: str | None = None) -> list[Proposal]:
        query = "SELECT * FROM pipeline_proposals"
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
                "UPDATE pipeline_proposals SET status = ?, result_json = ?, commit_sha = ?, "
                "decided_by = ?, updated_at = ? WHERE id = ?",
                (
                    proposal.status,
                    json.dumps({**proposal.result, "proposal_commit": proposal.commit}),
                    proposal.saved_commit,
                    proposal.decided_by,
                    proposal.updated_at,
                    proposal.id,
                ),
            )
        return proposal

    def end_interrupted(self) -> None:
        """A save or a test run cut off by DataLab stopping didn't finish: say so."""
        now = _now()
        with self._lock:
            rows = self._db.execute(
                "SELECT id, result_json FROM pipeline_proposals WHERE status = 'saving'"
            ).fetchall()
            for row in rows:
                result = {
                    **json.loads(row["result_json"]),
                    "state": "failed",
                    "message": "DataLab stopped while saving. Try again.",
                }
                self._db.execute(
                    "UPDATE pipeline_proposals SET status = 'failed', result_json = ?, "
                    "updated_at = ? WHERE id = ?",
                    (json.dumps(result), now, row["id"]),
                )
            self._db.execute(
                "UPDATE pipeline_tests SET status = 'error', finished_at = ?, "
                "message = 'DataLab stopped while the tests ran.' WHERE status = 'running'",
                (now,),
            )

    # Each conversation's base -------------------------------------------------

    def base(self, conversation_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT base FROM pipeline_bases WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        return row[0] if row else None

    def set_base(self, conversation_id: str, base: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO pipeline_bases VALUES (?, ?, ?) ON CONFLICT (conversation_id) "
                "DO UPDATE SET base = excluded.base, updated_at = excluded.updated_at",
                (conversation_id, base, _now()),
            )

    # Test runs ------------------------------------------------------------

    def add_test(self, tree: str, *, proposal_id: str | None, commit: str | None) -> TestRun:
        run = TestRun(f"pt_{secrets.token_hex(8)}", tree, proposal_id, commit, "running", _now())
        with self._lock:
            self._db.execute(
                "INSERT INTO pipeline_tests (id, tree, proposal_id, commit_sha, status, "
                "started_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (run.id, tree, proposal_id, commit, run.started_at),
            )
        return run

    def finish_test(
        self,
        run: TestRun,
        status: TestStatus,
        *,
        summary: dict[str, Any] | None = None,
        message: str | None = None,
        image_digest: str | None = None,
    ) -> TestRun:
        run.status, run.finished_at = status, _now()
        run.summary, run.message, run.image_digest = summary or {}, message, image_digest
        with self._lock:
            self._db.execute(
                "UPDATE pipeline_tests SET status = ?, finished_at = ?, summary_json = ?, "
                "message = ?, image_digest = ? WHERE id = ?",
                (status, run.finished_at, json.dumps(run.summary), message, image_digest, run.id),
            )
        return run

    def get_test(self, test_id: str) -> TestRun | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM pipeline_tests WHERE id = ?", (test_id,))
            found = row.fetchone()
        return _test(found) if found else None

    def latest_test(self, tree: str) -> TestRun | None:
        """The newest run of the tests on this tree."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM pipeline_tests WHERE tree = ? ORDER BY started_at DESC, rowid DESC",
                (tree,),
            ).fetchone()
        return _test(row) if row else None


def _proposal(row: sqlite3.Row) -> Proposal:
    result = json.loads(row["result_json"])
    commit = result.pop("proposal_commit", "")
    return Proposal(
        id=row["id"],
        conversation_id=row["conversation_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        turn=row["turn"],
        checkpoint=row["checkpoint"],
        base=row["base"],
        commit=commit,
        tree=row["tree"],
        fingerprint=row["fingerprint"],
        status=row["status"],
        files=[Change.from_dict(c) for c in json.loads(row["files_json"])],
        refused=[Refusal(r["path"], r["reason"]) for r in json.loads(row["refused_json"])],
        result=result,
        saved_commit=row["commit_sha"],
        decided_by=row["decided_by"],
    )


def _test(row: sqlite3.Row) -> TestRun:
    return TestRun(
        id=row["id"],
        tree=row["tree"],
        proposal_id=row["proposal_id"],
        commit=row["commit_sha"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        image_digest=row["image_digest"],
        summary=json.loads(row["summary_json"]),
        message=row["message"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
