"""Where the processed data's files come from: the lab's Dropbox folder, or
(practice DataLab) a synthetic folder bundled with DataLab.

Both list files by metadata only and fetch one file on request. Paths are
relative to the chosen folder, `/`-separated, as Dropbox writes them.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Protocol

from datalab.dropbox.client import (
    DropboxClient,
    DropboxRefused,
    TookTooLong,
    TooLarge,
    content_hash,
)


class CursorTooOld(RuntimeError):
    """The source can't say what changed since then: list everything again."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    id: str
    size: int
    modified: str | None = None
    content_hash: str | None = None

    @property
    def key(self) -> str:
        return path_key(self.path)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Changes:
    changed: list[FileEntry]
    # Keys of files and folders that are gone; a folder's removes what's under it.
    deleted: list[str]
    cursor: str | None


# How many of the chosen folder's top-level folders are listed at once.
LISTING_AT_ONCE = 4


@dataclass
class Progress:
    """How far a listing has got, for Settings to show. Counted from the
    listing's threads."""

    started: float = field(default_factory=time.monotonic)
    files: int = 0
    folders_done: int = 0
    # Top-level folders to list, once known.
    folders_total: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def count(self, *, files: int = 0, folders: int = 0) -> None:
        with self._lock:
            self.files += files
            self.folders_done += folders


class Source(Protocol):
    """A folder of processed data."""

    @property
    def label(self) -> str: ...

    def listing(self, progress: Progress | None = None) -> Changes:
        """Every file now (`deleted` empty), and a cursor for `changes`."""
        ...

    def changes(self, cursor: str) -> Changes:
        """What changed since `cursor`. CursorTooOld: call `listing` instead."""
        ...

    def fetch(self, entry: FileEntry, dest: Path, *, max_bytes: int, deadline: float) -> None:
        """Write the file to `dest`. TooLarge, TookTooLong or a source error."""
        ...


def path_key(path: str) -> str:
    return path.lower()


class DropboxSource:
    """The chosen folder in the lab's Dropbox team space."""

    def __init__(self, client: DropboxClient, folder_id: str, folder_path: str) -> None:
        self._client = client
        self.folder_id = folder_id
        self.folder_path = folder_path

    @property
    def label(self) -> str:
        return self.folder_path.rsplit("/", 1)[-1] or self.folder_path

    def listing(self, progress: Progress | None = None) -> Changes:
        """The folder's top level, then each of its folders, `LISTING_AT_ONCE`
        at a time: Dropbox pages a big recursive listing slowly, one page
        after another. The cursor comes first, so whatever changes while the
        parts are listed comes back at the next refresh."""
        progress = progress or Progress()
        prefix = self._current_prefix()
        cursor = self._client.latest_cursor(self.folder_id, recursive=True)
        changed: list[FileEntry] = []
        folders: list[str] = []
        for page in self._client.list_folder(self.folder_id):
            for item in page.items:
                if item.tag == "folder":
                    folders.append(item.id or item.path_lower)
                elif (entry := _entry(item, prefix)) is not None:
                    changed.append(entry)
        progress.folders_total = len(folders)
        progress.count(files=len(changed))

        def below(folder: str) -> list[FileEntry]:
            found: list[FileEntry] = []
            for page in self._client.list_folder(folder, recursive=True):
                new = [e for item in page.items if (e := _entry(item, prefix)) is not None]
                found += new
                progress.count(files=len(new))
            progress.count(folders=1)
            return found

        pool = ThreadPoolExecutor(LISTING_AT_ONCE, thread_name_prefix="dropbox-listing")
        try:
            for found in pool.map(below, folders):
                changed += found
        finally:
            # On an error, don't wait for the folders not yet started.
            pool.shutdown(cancel_futures=True)
        return Changes(changed, [], cursor)

    def changes(self, cursor: str) -> Changes:
        prefix = self._current_prefix()
        changed: list[FileEntry] = []
        deleted: list[str] = []
        while True:
            try:
                page = self._client.changes(cursor)
            except DropboxRefused as refused:
                if "reset" in refused.summary:
                    raise CursorTooOld(refused.summary) from refused
                raise
            for item in page.items:
                if item.tag == "deleted":
                    if (rel := _relative(item.path_lower, item.path_lower, prefix)) is not None:
                        deleted.append(path_key(rel))
                elif (entry := _entry(item, prefix)) is not None:
                    changed.append(entry)
            cursor = page.cursor
            if not page.has_more:
                return Changes(changed, deleted, cursor)

    def fetch(self, entry: FileEntry, dest: Path, *, max_bytes: int, deadline: float) -> None:
        # By id: a file renamed since the list was made is still the same file.
        # Dropbox's answer must put it in the folder as it's named now.
        self._client.download(
            entry.id or f"{self.folder_path}/{entry.path}",
            dest,
            max_bytes=max_bytes,
            deadline=deadline,
            inside=self._current_prefix(),
        )

    def _current_prefix(self) -> str:
        """The folder's path now (it may have been renamed or moved), lower-cased."""
        folder = self._client.metadata(self.folder_id)
        if folder.tag != "folder":
            raise DropboxRefused("path/not_folder")
        self.folder_path = folder.path_display
        return folder.path_lower


def _relative(path_lower: str, path_display: str, prefix: str) -> str | None:
    """The part of a Dropbox path under the chosen folder, or None if it isn't."""
    if not path_lower.startswith(prefix + "/"):
        return None
    return path_display[len(prefix) + 1 :]


def _entry(item, prefix: str) -> FileEntry | None:
    if item.tag != "file":
        return None
    rel = _relative(item.path_lower, item.path_display, prefix)
    if rel is None or not safe_relative(rel):
        return None
    return FileEntry(rel, item.id, item.size, item.modified, item.content_hash)


def safe_relative(path: str) -> bool:
    """Whether a relative path can be written under a folder as it is: no
    empty, `.` or `..` part, no backslash or drive, no control characters."""
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    return all(p not in ("", ".", "..") and p.isprintable() for p in parts)


class LocalSource:
    """A folder on this computer that DataLab owns: practice DataLab's
    synthetic processed data. It has no cursor: each refresh lists it all."""

    def __init__(self, root: Path, label: str = "Practice processed data") -> None:
        self.root = root
        self._label = label

    @property
    def label(self) -> str:
        return self._label

    def listing(self, progress: Progress | None = None) -> Changes:
        found: list[FileEntry] = []
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__")))
            for name in sorted(files):
                if name.startswith(".") or name.endswith((".py", ".pyc")):
                    continue
                full = Path(folder) / name
                if full.is_symlink() or not full.is_file():
                    continue
                rel = full.relative_to(self.root).as_posix()
                stat = full.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                found.append(
                    FileEntry(rel, f"local:{rel}", stat.st_size, modified, content_hash(full))
                )
        if progress is not None:
            progress.count(files=len(found))
        return Changes(found, [], None)

    def changes(self, cursor: str) -> Changes:
        raise CursorTooOld("a local folder has no cursor")

    def fetch(self, entry: FileEntry, dest: Path, *, max_bytes: int, deadline: float) -> None:
        source = self.root / entry.path
        if source.is_symlink() or not source.is_file():
            raise DropboxRefused("path/not_found")
        if source.stat().st_size > max_bytes:
            raise TooLarge(f"{source.stat().st_size} bytes")
        if time.monotonic() > deadline:
            raise TookTooLong("the copy ran past its deadline")
        partial = dest.with_name(dest.name + ".partial")
        try:
            shutil.copyfile(source, partial)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)


def practice_folder() -> Path:
    """The synthetic processed data bundled with DataLab (practice only)."""
    return Path(str(resources.files("datalab").joinpath("practice_dropbox")))
