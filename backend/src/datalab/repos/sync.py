"""Keeping one lab repo's clone in step with GitHub, and saying how it stands.

What the knowledge base does for its clone (knowledge/service.py), for any
lab repo: sync on request, sync first when a new conversation's copy is made
from a clone more than a few minutes old, remember the last error, and tell
missing access apart from any other failure (GitHub's API answers 404 for a
repository the person can't see, where git only fails).

The clone's state is a row in `repo_sync` (migration 0007), keyed by the
repo's name in DataLab ("knowledge", "pipelines"). Every repo shares the one
GitHub sign-in (`GitHubAuth`): only one object may refresh its tokens.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from datalab.repos.git import Clone, GitError
from datalab.repos.github import GitHubAuth, GitHubUnavailable, SignInNeeded, access_message

log = logging.getLogger(__name__)

# How old the clone may be before a new conversation's copy syncs it first,
# and how long that sync may take before the copy is made from what's there.
STALE_SECONDS = 10 * 60
SEED_SYNC_SECONDS = 15


class SyncState:
    """The `repo_sync` table: each clone's GitHub `main` when last synced, and the last error."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def get(self, repo: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM repo_sync WHERE repo = ?", (repo,)).fetchone()
        return dict(row) if row else {}

    def record(self, repo: str, *, head: str | None = None, error: str | None = None) -> None:
        now = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
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


class RepoSync:
    """One lab repo: its clone in `<data_dir>/repos/<name>`, synced with GitHub's `main`."""

    def __init__(
        self,
        key: str,
        repo: str,
        data_dir: Path,
        auth: GitHubAuth,
        state: SyncState,
        *,
        contact: str | None = None,
        # Tests only: a remote that's a folder on this computer.
        remote: str | None = None,
    ) -> None:
        self.key = key
        self.repo = repo
        self.auth = auth
        self.state = state
        self._contact = contact
        self.clone = Clone(
            data_dir / "repos" / repo.split("/")[1],
            remote or f"https://github.com/{repo}.git",
            before_network=auth.token_for_git,
            allow_local=remote is not None,
        )
        self._problem: tuple[str, str] | None = None  # state, message from the last sync

    def status(self) -> dict[str, Any]:
        """As the knowledge base's status: `repo` is signed out, no access,
        not cloned, in sync, behind, diverged, or sync failed."""
        sign_in = self.auth.status()
        synced = self.state.get(self.key)
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

    def sync(self) -> str | None:
        """Clone or fetch and fast-forward. GitHub's `main`, or None if it failed
        (`status()` says why)."""
        try:
            head = self.clone.sync()
        except SignInNeeded as error:
            self._problem = ("signed out", str(error))
        except (GitError, GitHubUnavailable) as error:
            self._problem = self._why_not(str(error))
            self.state.record(self.key, error=self._problem[1])
        else:
            self._problem = None
            self.state.record(self.key, head=head)
            return head
        return None

    def fresh_head(self) -> str | None:
        """GitHub's `main` for a new conversation's copy: synced first if the
        clone is more than a few minutes old (briefly; else as last synced).
        Hold `clone.lock` around this and the copy."""
        if self.clone.exists() and self.stale() and self.auth.signed_in():
            try:
                self.state.record(self.key, head=self.clone.sync(timeout=SEED_SYNC_SECONDS))
            except (GitError, SignInNeeded, GitHubUnavailable) as error:
                log.warning("couldn't sync %s before copying it: %s", self.repo, error)
        return self.clone.remote_head()

    def saved(self, commit: str | None) -> None:
        """After Save & share pushed `commit`: the clone's `main` follows."""
        self.state.record(self.key, head=commit)
        try:
            self.clone.fast_forward()
        except GitError as error:
            log.warning("couldn't fast-forward the %s clone: %s", self.repo, error)

    def stale(self) -> bool:
        synced = self.state.get(self.key).get("synced_at")
        if not synced:
            return True
        age = datetime.datetime.now(datetime.UTC) - datetime.datetime.fromisoformat(synced)
        return age.total_seconds() > STALE_SECONDS

    def _why_not(self, failure: str) -> tuple[str, str]:
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
