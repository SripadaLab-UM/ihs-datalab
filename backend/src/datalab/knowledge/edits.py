"""A person's own edits of knowledge-base pages: the Knowledge tab's Edit page.

No agent is involved. The rules are the proposals' (proposals.py), for a
person rather than an agent:

- **What can be edited.** Exactly what an agent's edit could propose
  (`kb.proposal_problem`): pages, lab skills, and the top files. Not
  `index.md` or `generated/`, which DataLab writes; `kb.edit_source` says
  where those come from and how to change them.
- **Where it starts.** GitHub's `main` as last synced: the edit's base, its
  "where you started".
- **Keep as a draft.** The text is kept in DataLab's database, on this
  computer, until the person shares or discards it. Nothing goes to GitHub.
  One edit of a page at a time: opening the page again finds it.
- **The check** is Save & share's own (share.prepare): the page as it would
  be committed, with its review fields as DataLab sets them.
- **Status.** A person may change a page's status (unlike an agent:
  `keep_status` isn't applied). `reviewed_by` and `reviewed_on` are never
  taken from the text: DataLab stamps them from the person saving.
- **Save & share** is share.save_and_share, as for a proposal, but strict:
  if the page changed on GitHub since the edit began, it stops with a
  conflict instead of merging. The person sees their edit, the version now
  on GitHub, and where they started, and chooses: reapply their edit on the
  new version (a three-way merge; overlapping lines they resolve
  themselves), or write the text to keep. Nothing is ever overwritten
  silently.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from datalab.knowledge import check as kb
from datalab.knowledge import share
from datalab.knowledge.proposals import copied, unified_diff
from datalab.repos.git import GitError, Identity, safe_path
from datalab.repos.github import SignInNeeded
from datalab.textcheck import lone_surrogate, size_text

log = logging.getLogger(__name__)

EditStatus = Literal["draft", "saving", "saved", "conflict", "check_failed", "failed", "discarded"]
# A person may still act on these.
OPEN: tuple[EditStatus, ...] = ("draft", "conflict", "check_failed", "failed")
# What the Edit page edits: the places a person's edit can be saved to.
EDITABLE_PLACES = ("top", "page", "skill", "skill_file")
_DATALAB = Identity("DataLab", "datalab@localhost")


class EditRefused(RuntimeError):
    """The edit can't be made or changed now. The message is for the person."""


class EditConflict(EditRefused):
    """Another window changed the draft since this one read it."""


@dataclass
class Edit:
    id: str
    path: str
    base: str
    new_page: bool
    text: str
    status: EditStatus
    created_at: str
    updated_at: str
    origin: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    commit: str | None = None
    decided_by: str | None = None


@dataclass(frozen=True)
class Upstream:
    """The page on GitHub's main as last synced, against the edit's base."""

    head: str | None
    # The text at the edit's base (None: a new page), and now (None: not there).
    before: str | None
    theirs: str | None
    theirs_state: Literal["text", "deleted", "not text"]
    changed: bool


@dataclass(frozen=True)
class Checked:
    report: kb.Report
    # The page as Save & share would commit it (before bringing in others'
    # changes): the review fields as DataLab sets them.
    shared: str
    diff: str
    notes: list[str]


class EditStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def add(self, path: str, base: str, text: str, *, new_page: bool, origin: dict) -> Edit:
        now = _now()
        edit = Edit(
            id=f"ke_{secrets.token_hex(8)}",
            path=path,
            base=base,
            new_page=new_page,
            text=text,
            status="draft",
            created_at=now,
            updated_at=now,
            origin=origin,
        )
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO kb_edits (id, path, base, new_page, text, status, origin_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                    (edit.id, path, base, int(new_page), text, json.dumps(origin), now, now),
                )
            except sqlite3.IntegrityError:
                raise EditRefused(f"There's already an edit of {path} on this computer.") from None
        return edit

    def get(self, edit_id: str) -> Edit | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM kb_edits WHERE id = ?", (edit_id,)).fetchone()
        return _edit(row) if row else None

    def open_for(self, path: str) -> Edit | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM kb_edits WHERE path = ? AND status NOT IN ('saved', 'discarded')",
                (path,),
            ).fetchone()
        return _edit(row) if row else None

    def list_open(self) -> list[Edit]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM kb_edits WHERE status NOT IN ('saved', 'discarded') "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [_edit(r) for r in rows]

    def update(self, edit: Edit, **changes: Any) -> Edit:
        for key, value in changes.items():
            setattr(edit, key, value)
        edit.updated_at = _now()
        with self._lock:
            self._db.execute(
                "UPDATE kb_edits SET base = ?, new_page = ?, text = ?, status = ?, "
                "result_json = ?, commit_sha = ?, decided_by = ?, updated_at = ? WHERE id = ?",
                (
                    edit.base,
                    int(edit.new_page),
                    edit.text,
                    edit.status,
                    json.dumps(edit.result),
                    edit.commit,
                    edit.decided_by,
                    edit.updated_at,
                    edit.id,
                ),
            )
        return edit

    def end_interrupted_saves(self) -> int:
        with self._lock:
            done = self._db.execute(
                "UPDATE kb_edits SET status = 'failed', result_json = ?, updated_at = ? "
                "WHERE status = 'saving'",
                (
                    json.dumps(
                        {"state": "failed", "message": "DataLab stopped while saving. Try again."}
                    ),
                    _now(),
                ),
            )
        return done.rowcount


class PageEdits:
    """The Edit page's work, on the Knowledge service's clone and sign-in."""

    def __init__(self, knowledge: Any, database: sqlite3.Connection) -> None:
        # knowledge: service.Knowledge (not imported: it imports this module).
        self._knowledge = knowledge
        self.store = EditStore(database)
        self._lock = threading.Lock()
        if knowledge.available:
            self.store.end_interrupted_saves()

    @property
    def clone(self):
        return self._knowledge.clone

    # Starting, reading ---------------------------------------------------------

    def start(
        self,
        path: str,
        *,
        new: bool = False,
        text: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> Edit:
        """The open edit of `path`, or a new one from the page as last synced.
        `new`: a page that isn't in the knowledge base yet. `text`: the draft
        to start with (a suggested update), else the page as it is."""
        self._knowledge.require_available()
        if problem := editable_problem(path):
            raise EditRefused(problem)
        with self._lock:
            found = self.store.open_for(path)
            if found is not None:
                if text is not None:
                    raise EditRefused(
                        f"There's already an edit of {path} on this computer. Open it in the "
                        "Knowledge tab and add this there, or save or discard it first."
                    )
                return found
            with self.clone.lock:
                head = self.clone.remote_head()
                if head is None:
                    raise EditRefused("Sync the knowledge base first (Knowledge tab, Sync).")
                content = self.clone.show(head, path)
            current = kb.as_text(content) if content is not None else None
            if content is not None and current is None:
                raise EditRefused(f"{path} isn't a text file.")
            if new and current is not None:
                raise EditRefused(f"There's already a page at {path}: open it and edit it.")
            if not new and current is None:
                raise EditRefused(f"There's no {path} in the knowledge base as last synced.")
            start = text if text is not None else current if current is not None else template(path)
            _check_text(path, start)
            return self.store.add(path, head, start, new_page=current is None, origin=origin or {})

    def from_suggestion(self, conversation_id: str, suggestion: dict[str, Any]) -> Edit:
        """A suggested update, accepted: an edit of its page with the text
        added as a section (or a new draft page holding it). Nothing is
        shared until the person saves it."""
        self._knowledge.require_available()
        path = suggestion["page"]
        with self.clone.lock:
            head = self.clone.remote_head()
            content = self.clone.show(head, path) if head else None
        current = kb.as_text(content) if content is not None else None
        title, body = suggestion["title"], suggestion["text"]
        if current is not None:
            draft = with_section(current, title, body)
        else:
            draft = template(path, summary=title, body=f"## {title}\n\n{body.strip()}")
        origin = {
            "conversation_id": conversation_id,
            "suggestion_id": suggestion["id"],
            "title": title,
            "reason": suggestion.get("reason", ""),
            "evidence": suggestion.get("evidence", []),
        }
        return self.start(path, new=current is None, text=draft, origin=origin)

    def get(self, edit_id: str) -> Edit:
        edit = self.store.get(edit_id)
        if edit is None:
            raise LookupError("No such edit.")
        return edit

    def upstream(self, edit: Edit) -> Upstream:
        with self.clone.lock:
            head = self.clone.remote_head()
            then = self.clone.ls_tree(edit.base).get(edit.path)
            now = self.clone.ls_tree(head).get(edit.path) if head else None
            blobs = self.clone.read_blobs(e.blob for e in (then, now) if e is not None)
        before = kb.as_text(blobs.get(then.blob, b"")) if then is not None else None
        raw = blobs.get(now.blob) if now is not None else None
        theirs = kb.as_text(raw) if raw is not None else None
        state = "deleted" if raw is None else "text" if theirs is not None else "not text"
        changed = (then and then.blob) != (now and now.blob)
        return Upstream(head, before, theirs, state, changed)

    def check(self, edit: Edit, text: str | None = None) -> Checked:
        """The check, as Save & share runs it first, on `text` (else the draft)."""
        text = edit.text if text is None else text
        _check_text(edit.path, text)
        before = self.upstream(edit).before
        account = self._knowledge.signed_in_account()
        with self.clone.lock:
            files, report = share.prepare(
                self.clone,
                share.Share(
                    base=edit.base,
                    files={edit.path: text.encode()},
                    author=_DATALAB,
                    reviewer=account.login if account else "you",
                    conversation_id="",
                    proposal_id=edit.id,
                ),
            )
        shared = kb.as_text(files.get(edit.path, b"")) or ""
        return Checked(
            report, shared, unified_diff(edit.path, before, shared), review_notes(before, text)
        )

    # Changing --------------------------------------------------------------------

    def keep(self, edit_id: str, text: str, version: str | None) -> Edit:
        """Keep as a draft on this computer. Never GitHub."""
        with self._lock:
            edit = self._open(edit_id, version)
            _check_text(edit.path, text)
            return self.store.update(edit, text=text, status="draft", result={})

    def discard(self, edit_id: str) -> Edit:
        with self._lock:
            edit = self._open(edit_id, None)
            return self.store.update(
                edit, status="discarded", result={"state": "discarded", "message": "Discarded."}
            )

    def reapply(
        self, edit_id: str, version: str | None, resolution: str | None = None
    ) -> tuple[Edit, str | None]:
        """Move the edit onto the page as on GitHub now. With `resolution`,
        that text is the new draft; without, the person's changes are merged
        in (a three-way merge). Returns the edit, and the merge with its
        overlapping lines marked if it didn't merge cleanly (the edit is then
        unchanged)."""
        with self._lock:
            edit = self._open(edit_id, version)
            upstream = self.upstream(edit)
            if upstream.head is None:
                raise EditRefused("Sync the knowledge base first.")
            if not upstream.changed:
                return edit, None
            if resolution is None:
                if upstream.theirs_state != "text":
                    raise EditRefused(
                        f"{edit.path} was deleted on GitHub since, or isn't text there: write the "
                        "version to keep instead."
                        if upstream.theirs_state == "deleted"
                        else f"{edit.path} isn't text on GitHub any more."
                    )
                merged, clean = merge3(
                    self.clone, edit.text, upstream.before or "", upstream.theirs or ""
                )
                if not clean:
                    return edit, merged
                resolution = merged
            _check_text(edit.path, resolution)
            edit = self.store.update(
                edit,
                base=upstream.head,
                text=resolution,
                new_page=upstream.theirs is None,
                status="draft",
                result={},
            )
            return edit, None

    def share(
        self,
        edit_id: str,
        confirmed: list[str],
        seen: str | None,
        findings_seen: list[str],
    ) -> Edit:
        """Save & share the draft as the signed-in person, only if it's what
        they saw: `seen` is the draft's text_digest, `findings_seen` the ids
        of the check's findings they were shown."""
        with self._lock:
            edit = self._open(edit_id, None)
            auth = self._knowledge.auth
            account = auth.account() if auth is not None else None
            if account is None:
                raise SignInNeeded("Sign in to GitHub to share changes.")
            if seen != _digest(edit.text):
                raise EditConflict(
                    "This draft changed since you looked at it (in another window?). Nothing was "
                    "shared: check it again, then save."
                )
            checked = self.check(edit)
            if set(findings_seen) != {f.id for f in checked.report.findings}:
                raise EditConflict(
                    "The check's findings changed since you looked at them. Nothing was shared: "
                    "check them again, then save."
                )
            if not edit.new_page and edit.text == self.upstream(edit).before:
                raise EditRefused("There's no change to share yet.")
            edit = self.store.update(edit, status="saving")
            request = share.Share(
                base=edit.base,
                files={edit.path: edit.text.encode()},
                author=Identity(account.display_name, account.email),
                reviewer=account.login,
                conversation_id=str(edit.origin.get("conversation_id") or ""),
                proposal_id=edit.id,
                confirmed=confirmed,
                strict=True,
            )
            try:
                result = share.save_and_share(self.clone, request)
            except Exception as error:
                log.exception("Save & share failed for %s", edit.id)
                result = share.SaveResult("failed", f"Saving failed ({type(error).__name__}).")
            status: EditStatus = {
                "saved": "saved",
                "nothing to save": "saved",
                "conflict": "conflict",
                "check_failed": "check_failed",
                "failed": "failed",
            }[result.state]  # type: ignore[assignment]
            if status == "saved":
                self._knowledge.shared(result.commit)
            return self.store.update(
                edit,
                status=status,
                result=result.to_dict(),
                commit=result.commit if status == "saved" else None,
                decided_by=account.login,
            )

    def _open(self, edit_id: str, version: str | None) -> Edit:
        edit = self.get(edit_id)
        if edit.status not in OPEN:
            raise EditRefused(f"This edit is {edit.status.replace('_', ' ')}.")
        if version is not None and version != edit.updated_at:
            raise EditConflict(
                "This draft was changed in another window since you opened it. Nothing was "
                "saved: reload it to see that version."
            )
        return edit


# Rules --------------------------------------------------------------------------


def editable_problem(path: str) -> str | None:
    """Why a person can't edit `path` in DataLab, or None."""
    if lone_surrogate(path) is not None or not safe_path(path) or not copied(path):
        return "That name can't be used in the knowledge base."
    if note := kb.edit_source(path):
        return f"{path} can't be edited here. {note}"
    if kb.place(path) not in EDITABLE_PLACES:
        return f"{path} can't be edited here."
    return None


def template(path: str, summary: str = "", body: str = "") -> str:
    """A new file's text: a page's front matter as the check wants it (a draft)."""
    where = kb.place(path)
    parts = path.split("/")
    if where == "page":
        stem = parts[1][: -len(".md")]
        fields: dict[str, Any] = {
            "id": stem,
            "kind": kb.FOLDERS[parts[0]],
            "status": "draft",
            "summary": summary or "One line saying what this page establishes.",
            "evidence": [],
            "limitations": [],
            "related": [],
            "cohorts": [],
        }
        text = body or "Plain-language explanation, caveats, and a worked example (no data)."
        return f"---\n{_yaml(fields)}---\n\n# {stem}\n\n{text.rstrip()}\n"
    if where == "skill":
        fields = {"name": parts[1], "description": summary or "When the agent uses this skill."}
        return f"---\n{_yaml(fields)}---\n\n# {parts[1]}\n\n{body.rstrip()}\n"
    return body


def with_section(text: str, title: str, body: str) -> str:
    """`text` with a section added at the end: a suggested update to a page."""
    return f"{text.rstrip()}\n\n## {' '.join(title.split())}\n\n{body.strip()}\n"


def review_notes(before: str | None, text: str) -> list[str]:
    """What the edit does to the fields only people set, said to the person."""
    old = (kb.front_matter(before)[0] or {}) if before is not None else {}
    new = kb.front_matter(text)[0] or {}
    notes = []
    was, now = old.get("status"), new.get("status")
    if before is not None and was != now and now is not None:
        notes.append(f"You're changing its status from {was or 'none'} to {now}.")
    if now == "reviewed":
        notes.append(
            "It's saved as reviewed, by you: DataLab fills in reviewed_by and reviewed_on from "
            "your GitHub account when you save."
        )
    for key in kb.REVIEW_FIELDS:
        if str(old.get(key, "")) != str(new.get(key, "")):
            notes.append(
                f"{key} is set by DataLab from the person saving, so your change to it isn't kept."
            )
            break
    return notes


def merge3(clone, ours: str, base: str, theirs: str) -> tuple[str, bool]:
    """A three-way merge of the person's text and GitHub's, from where they
    started: the result, and whether it merged without overlaps (else the
    overlapping lines are marked, for the person to resolve)."""
    with tempfile.TemporaryDirectory(prefix="datalab-merge-") as folder:
        paths = []
        for name, text in (("yours", ours), ("base", base), ("theirs", theirs)):
            path = Path(folder) / name
            path.write_text(text, encoding="utf-8", newline="")
            paths.append(str(path))
        done = clone.git(
            "merge-file", "-p",
            "-L", "Your edit", "-L", "Where you started", "-L", "The version now on GitHub",
            *paths, cwd=Path(folder), check=False,
        )  # fmt: skip
    if done.returncode < 0 or done.returncode > 127:
        raise GitError("git merge-file failed.")
    return done.stdout.decode("utf-8", "replace"), done.returncode == 0


def _check_text(path: str, text: str) -> None:
    if (not_text := lone_surrogate(text)) is not None:
        raise EditRefused(f"Line {not_text.line}: {not_text.message}")
    if "\0" in text or len(text.encode()) > kb.size_limit(path):
        raise EditRefused(f"{path} must be text under {size_text(kb.size_limit(path))}.")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _yaml(fields: dict[str, Any]) -> str:
    return yaml.safe_dump(
        fields, sort_keys=False, allow_unicode=True, default_flow_style=None, width=4096
    )


def _edit(row: sqlite3.Row) -> Edit:
    return Edit(
        id=row["id"],
        path=row["path"],
        base=row["base"],
        new_page=bool(row["new_page"]),
        text=row["text"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        origin=json.loads(row["origin_json"]),
        result=json.loads(row["result_json"]),
        commit=row["commit_sha"],
        decided_by=row["decided_by"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
