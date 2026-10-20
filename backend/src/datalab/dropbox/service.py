"""The processed data as DataLab's routes and agent tools use it.

- Which folder: the person connects Dropbox and chooses the lab's folder in
  Settings (practice DataLab has its bundled synthetic folder instead).
- What's in it: the file list (index.py), refreshed on request and before a
  conversation that uses it, if it's over an hour old.
- One file for an agent (`load`): only a file in the list, only up to the
  size limit, checked for free disk space first. It's downloaded once into
  this person's cache, then placed in the conversation's /data/dropbox,
  which the agent can read but not change. Every request, fetched or
  refused, is in the conversation's Data accessed panel.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.dropbox.auth import DropboxAuth, DropboxUnavailable, SignIn, SignInNeeded
from datalab.dropbox.client import DropboxClient, DropboxRefused, TookTooLong, TooLarge
from datalab.dropbox.families import Family, families, pattern
from datalab.dropbox.index import ProcessedIndex
from datalab.dropbox.sources import (
    DropboxSource,
    FileEntry,
    LocalSource,
    Source,
    practice_folder,
    safe_relative,
)
from datalab.textcheck import size_text

log = logging.getLogger(__name__)

PRACTICE_FOLDER_ID = "practice"
# A file list older than this is refreshed before a conversation uses it.
STALE_SECONDS = 60 * 60
# Downloads at once, across conversations.
DOWNLOADS_AT_ONCE = 2
CREDENTIAL_NAMES = (".env", "credentials", "id_rsa", ".pem", ".key", ".p12", ".pfx", ".netrc")


class NotAvailable(RuntimeError):
    """The processed data can't be used now: why, in plain words."""


class LoadRefused(RuntimeError):
    """DataLab won't fetch this file: why, for the agent."""


@dataclass(frozen=True)
class Folder:
    id: str
    path: str
    name: str


@dataclass(frozen=True)
class Loaded:
    load_id: str
    entry: FileEntry
    # Where it is on the host, and where the agent sees it.
    host_path: Path
    container_path: str
    cached: bool


@dataclass(frozen=True)
class Status:
    source: str  # "dropbox", "practice" or "off"
    configured: bool
    sign_in: SignIn | None
    folder: str | None
    files: int
    bytes: int
    indexed_at: str | None
    problem: str | None = None

    @property
    def ready(self) -> bool:
        return self.problem is None and self.folder is not None


class ProcessedData:
    def __init__(
        self,
        settings: Settings,
        db: sqlite3.Connection,
        access_log: AccessLog,
        *,
        auth: DropboxAuth | None = None,
        practice_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._log = access_log
        self.index = ProcessedIndex(db)
        self._practice = settings.profile == "practice"
        self.auth = (
            None
            if self._practice
            else (
                auth
                or (DropboxAuth(settings.dropbox.app_key) if settings.dropbox.app_key else None)
            )
        )
        self._client = DropboxClient(self.auth) if self.auth is not None else None
        self._practice_root = practice_root or practice_folder()
        self._cache = settings.data_dir / "processed-cache"
        self._refreshing = threading.Lock()
        self._downloads = threading.Semaphore(DOWNLOADS_AT_ONCE)
        self._fetching: dict[str, threading.Lock] = {}
        self._fetching_lock = threading.Lock()
        if self._practice:
            chosen = self.index.chosen()
            if chosen is None or chosen.folder_id != PRACTICE_FOLDER_ID:
                self.index.choose(PRACTICE_FOLDER_ID, "Practice processed data", None)

    # What Settings shows ----------------------------------------------------

    def status(self) -> Status:
        chosen = self.index.chosen()
        files, size = self.index.totals()
        common = {
            "folder": chosen.folder_path if chosen else None,
            "files": files,
            "bytes": size,
            "indexed_at": chosen.indexed_at if chosen else None,
        }
        if self._practice:
            return Status("practice", True, None, **common)
        if self.auth is None:
            return Status(
                "off",
                False,
                None,
                **common,
                problem="The lab's Dropbox app isn't set in settings.toml ([dropbox] app_key).",
            )
        sign_in = self.auth.status()
        problem = None
        if sign_in.state != "signed in":
            problem = "Connect Dropbox in Settings → Processed data."
        elif chosen is None:
            problem = "Choose the lab's processed-data folder in Settings → Processed data."
        return Status("dropbox", True, sign_in, **common, problem=problem)

    def available(self) -> bool:
        """Whether a conversation can use the processed data now."""
        return self.status().ready

    # Choosing the folder (Dropbox only) -------------------------------------

    def team_folders(self, inside: str = "") -> list[Folder]:
        """The folders in one folder of the person's Dropbox team space (by its
        id or path; "" for the top), to choose from or open. Only folders: a
        file is never chosen, and nothing is downloaded."""
        client = self._require_client()
        found: list[Folder] = []
        for page in client.list_folder(inside, recursive=False):
            found += [Folder(i.id, i.path_display, i.name) for i in page.items if i.tag == "folder"]
        return sorted(found, key=lambda f: f.name.lower())

    def choose(self, folder_id: str) -> Folder:
        client = self._require_client()
        item = client.metadata(folder_id)
        if item.tag != "folder":
            raise NotAvailable("That isn't a folder.")
        account = self.auth.account() if self.auth else None
        self.index.choose(item.id, item.path_display, account.root_namespace if account else None)
        return Folder(item.id, item.path_display, item.name)

    def disconnect(self) -> SignIn:
        if self.auth is None:
            raise NotAvailable("Dropbox isn't set up in this DataLab.")
        self.index.forget()
        self._clear_cache()
        return self.auth.sign_out()

    # The file list ----------------------------------------------------------

    def refresh(self, *, if_older_than: float | None = None) -> int:
        """Bring the file list up to date; how many files it has. With
        `if_older_than` (seconds), only if it's at least that old."""
        status = self.status()
        if not status.ready:
            raise NotAvailable(status.problem or "The processed data isn't set up.")
        if if_older_than is not None and status.indexed_at:
            age = time.time() - datetime.fromisoformat(status.indexed_at).timestamp()
            if age < if_older_than:
                return status.files
        with self._refreshing:
            try:
                return self.index.refresh(self._source())
            except SignInNeeded as error:
                raise NotAvailable(str(error)) from None
            except (DropboxUnavailable, DropboxRefused) as error:
                raise NotAvailable(f"The file list couldn't be refreshed: {error}") from None

    def ensure_fresh(self) -> None:
        """Before a conversation that uses it: refresh a stale list, and carry
        on with the old one if Dropbox can't be reached."""
        try:
            self.refresh(if_older_than=STALE_SECONDS)
        except NotAvailable as error:
            log.warning("processed data not refreshed: %s", error)

    def datasets(self, folder: str = "", contains: str = "") -> list[Family]:
        """Datasets under `folder` (a path in the chosen folder), optionally
        only those whose pattern contains `contains`."""
        self._require_ready()
        prefix = _clean_folder(folder)
        wanted = contains.lower().strip()
        entries = (e for e in self.index.files() if e.key.startswith(prefix))
        return [f for f in families(entries) if not wanted or wanted in f.pattern]

    def files_of(self, dataset: str, limit: int = 200) -> list[FileEntry]:
        """The files of one dataset (as `datasets` names it), newest first."""
        self._require_ready()
        found = [e for e in self.index.files() if pattern(e.path) == dataset.lower().strip()]
        found.sort(key=lambda e: e.modified or "", reverse=True)
        return found[:limit]

    def describe(self, path: str) -> tuple[FileEntry, bool]:
        """A file's metadata, and whether it's already downloaded."""
        entry = self._entry(path)
        return entry, self._cached_path(entry).is_file()

    # One file for an agent --------------------------------------------------

    def load(self, session_id: str, staging: Path, path: str) -> Loaded:
        """Fetch one file into a conversation's /data/dropbox (`staging` on
        the host). LoadRefused says why not; it's in the panel either way."""
        load_id = f"f_{secrets.token_hex(8)}"
        try:
            entry = self._entry(path)
            self._check(entry)
        except LoadRefused as refused:
            self._log.file_refused(
                load_id=load_id, session_id=session_id, path=path[:1000], reason=str(refused)
            )
            raise
        self._log.file_started(load_id=load_id, session_id=session_id, path=entry.path)
        started = time.monotonic()
        try:
            source, cached = self._fetch_to_cache(entry, started)
            target = staging / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            _place(source, target)
        except LoadRefused as refused:
            self._finish(load_id, "failed", entry, started, str(refused))
            raise
        except Exception:
            self._finish(load_id, "failed", entry, started, "DataLab couldn't fetch the file.")
            raise
        self._finish(load_id, "succeeded", entry, started, None, cached=cached)
        return Loaded(load_id, entry, target, f"/data/dropbox/{entry.path}", cached)

    # ------------------------------------------------------------------------

    def _entry(self, path: str) -> FileEntry:
        try:
            self._require_ready()
        except NotAvailable as error:
            raise LoadRefused(str(error)) from None
        clean = path.strip().lstrip("/")
        if clean.startswith("data/dropbox/"):
            clean = clean[len("data/dropbox/") :]
        entry = self.index.get(clean) if safe_relative(clean) else None
        if entry is None:
            raise LoadRefused(
                f"{path!r} isn't a file in the processed data. Use list_data_files to find it."
            )
        return entry

    def _check(self, entry: FileEntry) -> None:
        name = entry.name.lower()
        if name in CREDENTIAL_NAMES or name.endswith(CREDENTIAL_NAMES):
            raise LoadRefused(
                f"{entry.path} looks like a credentials file, so DataLab won't fetch it."
            )
        limit = self._settings.dropbox.max_file_bytes
        if entry.size > limit:
            raise LoadRefused(
                f"{entry.path} is {size_text(entry.size)}, more than the {size_text(limit)} "
                "DataLab fetches for an agent. Ask the person to share a smaller extract."
            )
        if not self._cached_path(entry).is_file():
            free = shutil.disk_usage(self._settings.data_dir).free
            if free - entry.size < self._settings.limits.min_free_disk_bytes:
                raise LoadRefused(
                    f"There isn't enough free disk space to fetch {entry.path} "
                    f"({size_text(entry.size)})."
                )

    def _fetch_to_cache(self, entry: FileEntry, started: float) -> tuple[Path, bool]:
        cached = self._cached_path(entry)
        with self._file_lock(cached.name):
            if cached.is_file():
                os.utime(cached)  # recently used: kept longest
                return cached, True
            self._cache.mkdir(parents=True, exist_ok=True)
            deadline = started + self._settings.limits.deadline_seconds
            with self._downloads:
                try:
                    self._source().fetch(
                        entry,
                        cached,
                        max_bytes=self._settings.dropbox.max_file_bytes,
                        deadline=deadline,
                    )
                except TooLarge:
                    raise LoadRefused(f"{entry.path} is larger than DataLab fetches.") from None
                except TookTooLong:
                    raise LoadRefused(
                        f"Fetching {entry.path} took longer than "
                        f"{self._settings.limits.deadline_seconds:.0f} s."
                    ) from None
                except SignInNeeded as error:
                    raise LoadRefused(str(error)) from None
                except DropboxRefused as error:
                    raise LoadRefused(
                        f"Dropbox couldn't find {entry.path} ({error.summary}). The file list may "
                        "be out of date: the person can refresh it in Settings."
                    ) from None
                except DropboxUnavailable as error:
                    raise LoadRefused(str(error)) from None
            self._trim_cache(keep=cached)
            return cached, False

    def _cached_path(self, entry: FileEntry) -> Path:
        stamp = hashlib.sha256(
            f"{entry.id}\0{entry.content_hash or entry.size}".encode()
        ).hexdigest()
        suffix = Path(entry.name).suffix.lower()
        return (
            self._cache / f"{stamp[:32]}{suffix if suffix.isascii() and len(suffix) <= 10 else ''}"
        )

    def _file_lock(self, name: str) -> threading.Lock:
        with self._fetching_lock:
            return self._fetching.setdefault(name, threading.Lock())

    def _trim_cache(self, *, keep: Path) -> None:
        """Drop the least recently used downloads past the cache's limit.
        Conversations keep their own links to files already placed."""
        if not self._cache.is_dir():
            return
        files = sorted(
            (p for p in self._cache.iterdir() if p.is_file() and not p.name.endswith(".partial")),
            key=lambda p: p.stat().st_mtime,
        )
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= self._settings.dropbox.max_cache_bytes:
                break
            if path == keep:
                continue
            total -= path.stat().st_size
            path.unlink(missing_ok=True)

    def _clear_cache(self) -> None:
        if self._cache.is_dir():
            for path in self._cache.iterdir():
                path.unlink(missing_ok=True)

    def _finish(
        self,
        load_id: str,
        status: str,
        entry: FileEntry,
        started: float,
        message: str | None,
        *,
        cached: bool = False,
    ) -> None:
        self._log.file_finished(
            load_id,
            status=status,
            size=entry.size if status == "succeeded" else None,
            content_hash=entry.content_hash,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            message=message,
            cached=cached,
        )

    def _require_ready(self) -> None:
        """Refuse when it can't be used; list the folder if it never has been."""
        status = self.status()
        if not status.ready:
            raise NotAvailable(status.problem or "The processed data isn't set up.")
        if status.indexed_at is None:
            self.refresh()

    def _source(self) -> Source:
        if self._practice:
            return LocalSource(self._practice_root)
        chosen = self.index.chosen()
        if chosen is None or self._client is None:
            raise NotAvailable("Choose the lab's processed-data folder in Settings first.")
        return DropboxSource(self._client, chosen.folder_id, chosen.folder_path)

    def _require_client(self) -> DropboxClient:
        if self._client is None:
            raise NotAvailable(
                "Practice DataLab uses its own synthetic folder."
                if self._practice
                else "The lab's Dropbox app isn't set in settings.toml ([dropbox] app_key)."
            )
        return self._client


def _place(source: Path, target: Path) -> None:
    """Put a cached file in a conversation's folder: a hard link when it can
    be (no second copy), else a copy. The agent sees it through a read-only
    mount; the files themselves stay writable on the host, so deleting a
    conversation or the cache works on Windows too."""
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    try:
        os.link(source, partial)
    except OSError:
        shutil.copyfile(source, partial)
    partial.replace(target)


def _clean_folder(folder: str) -> str:
    clean = folder.strip().strip("/").lower()
    if clean.startswith("data/dropbox"):
        clean = clean[len("data/dropbox") :].strip("/")
    return f"{clean}/" if clean else ""
