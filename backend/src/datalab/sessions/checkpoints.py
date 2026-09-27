"""Checkpoints of a conversation's workspace, taken after every turn.

A checkpoint records every file in `/work`: its path, size, and content hash.
File contents go into a content-addressed store next to the workspace, where
the agent can't reach it, so unchanged files cost nothing extra. Restoring a
checkpoint puts `/work` back as it was then.

The agent can plant links, pipes, and odd files in `/work`, and it can
change the folder while DataLab looks at it. So:
- a checkpoint is taken only while the container is paused, and a restore
  only while it is stopped: nothing can swap a file for a link mid-way;
- links are recorded as links and never followed, and every file read must
  resolve inside the workspace;
- the browser is shown files from the latest checkpoint, never from the live
  folder (see api/files.py).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # a planted pipe must not hang the open
_BINARY = getattr(os, "O_BINARY", 0)
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str  # "too large", "not a regular file", "unreadable"
    size: int | None = None


@dataclass(frozen=True)
class Checkpoint:
    number: int
    created_at: str
    label: str
    turn: int | None
    files: int
    bytes: int
    skipped: list[Skipped] = field(default_factory=list)
    # Taken after a turn's rigor review (which can change files), not the turn itself.
    review: bool = False


@dataclass(frozen=True)
class Entry:
    """One file in a checkpoint."""

    path: str
    sha256: str
    size: int
    modified: float
    executable: bool


@dataclass(frozen=True)
class RestoreResult:
    checkpoint: Checkpoint
    written: int
    removed: int
    # Files left as they are: too large for the checkpoint being restored, or
    # ones the pre-restore checkpoint couldn't save (so deleting them would
    # lose them for good).
    left_alone: list[str]
    # Files in the checkpoint that weren't put back, because that would have
    # meant replacing something left alone.
    not_restored: list[str] = field(default_factory=list)


class UnsafePath(RuntimeError):
    pass


class CheckpointMissing(KeyError):
    pass


class Checkpoints:
    def __init__(self, store: Path, work: Path, *, max_file_bytes: int = 100 * 1024**2) -> None:
        self._store = store
        self._work = work
        self._max_file_bytes = max_file_bytes

    @property
    def _manifests(self) -> Path:
        return self._store / "manifests"

    @property
    def _objects(self) -> Path:
        return self._store / "objects"

    # Taking ------------------------------------------------------------------

    def take(self, label: str, *, turn: int | None = None, review: bool = False) -> Checkpoint:
        """Record the workspace as it is now. Nothing may run in the container."""
        self._manifests.mkdir(parents=True, exist_ok=True)
        self._objects.mkdir(parents=True, exist_ok=True)
        latest = self.latest()
        previous = self._contents(latest.number)["entries"] if latest else {}
        work_real = os.path.realpath(self._work)
        files: dict[str, dict[str, Any]] = {}
        dirs: list[str] = []
        links: dict[str, str] = {}
        skipped: list[Skipped] = []
        total = 0
        for rel, kind in _walk(self._work):
            full = self._work / rel
            if kind == "dir":
                dirs.append(rel)
                continue
            if kind == "unreadable":
                skipped.append(Skipped(rel, "unreadable"))
                continue
            if kind == "link":
                try:
                    links[rel] = os.readlink(full)
                except OSError:
                    skipped.append(Skipped(rel, "unreadable"))
                continue
            if kind != "file":
                skipped.append(Skipped(rel, "not a regular file"))
                continue
            try:
                # os.lstat, not the directory entry: on Windows only this
                # reports the file's index number.
                info = os.lstat(full)
            except OSError:
                skipped.append(Skipped(rel, "unreadable"))
                continue
            if info.st_size > self._max_file_bytes:
                skipped.append(Skipped(rel, "too large", info.st_size))
                continue
            signature = [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]
            known = previous.get(rel)
            if known and known.get("signature") == signature and self._has_object(known["sha256"]):
                digest = known["sha256"]
            else:
                digest = self._store_file(full, work_real)
                if digest is None:
                    skipped.append(Skipped(rel, "unreadable"))
                    continue
            files[rel] = {
                "sha256": digest,
                "size": info.st_size,
                "modified": info.st_mtime,
                "executable": bool(info.st_mode & stat.S_IXUSR),
                "signature": signature,
            }
            total += info.st_size

        number = (latest.number if latest else 0) + 1
        checkpoint = Checkpoint(
            number=number,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            label=label,
            turn=turn,
            files=len(files),
            bytes=total,
            skipped=skipped,
            review=review,
        )
        # Contents first, then the summary: a checkpoint exists once its summary does.
        _write_json(self._contents_path(number), {"entries": files, "dirs": dirs, "links": links})
        _write_json(self._summary_path(number), asdict(checkpoint))
        return checkpoint

    # Reading -----------------------------------------------------------------

    def list(self) -> list[Checkpoint]:
        if not self._manifests.exists():
            return []
        return [_summary(p) for p in sorted(self._manifests.glob("[0-9]*[0-9].json"))]

    def get(self, number: int) -> Checkpoint | None:
        path = self._summary_path(number)
        return _summary(path) if path.exists() else None

    def latest(self) -> Checkpoint | None:
        checkpoints = self.list()
        return checkpoints[-1] if checkpoints else None

    def entries(self, number: int) -> dict[str, Entry]:
        """The files in checkpoint `number`, by path."""
        return {
            rel: Entry(rel, e["sha256"], e["size"], e.get("modified", 0), e["executable"])
            for rel, e in self._contents(number)["entries"].items()
        }

    def links(self, number: int) -> dict[str, str]:
        """The links in checkpoint `number`: path -> target, never followed."""
        return dict(self._contents(number).get("links", {}))

    def open_object(self, entry: Entry) -> int:
        """Open a file's saved content (in DataLab's own store) for reading."""
        return os.open(self._object_path(entry.sha256), os.O_RDONLY | _BINARY)

    # Restoring ---------------------------------------------------------------

    def restore(self, number: int, *, keep: frozenset[str] = frozenset()) -> RestoreResult:
        """Put the workspace back as it was at checkpoint `number`.

        The container must be stopped: this writes into the agent's folder.
        Paths in `keep` (and files too large to be in the checkpoint) are left
        exactly as they are, and so is anything that would have to replace
        them; those are reported as not restored.
        """
        checkpoint = self.get(number)
        if checkpoint is None:
            raise CheckpointMissing(number)
        contents = self._contents(number)
        left_alone = {s.path for s in checkpoint.skipped if s.reason == "too large"} | set(keep)

        def blocked(rel: str) -> bool:
            # Writing a file or link at `rel` would replace, or sit inside,
            # something being left alone.
            return (
                rel in left_alone or _under_any(rel, left_alone) or _under_any_of(left_alone, rel)
            )

        entries = {r: e for r, e in contents["entries"].items() if not blocked(r)}
        links = {r: t for r, t in contents.get("links", {}).items() if not blocked(r)}
        not_restored = sorted(
            r for r in [*contents["entries"], *contents.get("links", {})] if blocked(r)
        )
        # Folders that must exist: the checkpoint's own, and every parent of
        # something restored or left alone. Never where a kept file sits.
        wanted_dirs = {d for d in contents["dirs"] if not _under_any(d, left_alone)}
        for rel in [*entries, *links, *left_alone]:
            wanted_dirs.update(_parents(rel))
        wanted_dirs -= {d for d in left_alone if not _is_real_dir(self._work / d)}

        # The agent may have made folders unreadable or read-only; with the
        # container stopped, DataLab can put that right.
        _make_folders_writable(self._work)
        removed = 0
        # Remove what the checkpoint doesn't have. Walk first, then delete, so
        # the walk never sees a half-changed tree.
        current = list(_walk(self._work))
        for rel, kind in current:
            if kind in ("dir", "unreadable") or rel in left_alone or _under_any(rel, left_alone):
                continue
            if (kind == "file" and rel in entries) or (kind == "link" and rel in links):
                continue
            (self._work / rel).unlink()
            removed += 1
        # Deepest first, so a folder is empty by the time it's checked.
        for rel, kind in sorted(current, key=lambda c: c[0].count("/"), reverse=True):
            target = self._work / rel
            if kind == "dir" and rel not in wanted_dirs and not any(target.iterdir()):
                target.rmdir()

        for rel in sorted(wanted_dirs, key=lambda d: d.count("/")):
            self._ensure_dir(rel)

        written = 0
        for rel, entry in entries.items():
            if self._write_if_changed(rel, entry):
                written += 1
        for rel, target in links.items():
            self._restore_link(rel, target)
        return RestoreResult(checkpoint, written, removed, sorted(left_alone), not_restored)

    # ------------------------------------------------------------------------

    def _summary_path(self, number: int) -> Path:
        return self._manifests / f"{number:05d}.json"

    def _contents_path(self, number: int) -> Path:
        return self._manifests / f"{number:05d}.contents.json"

    def _contents(self, number: int) -> dict[str, Any]:
        return json.loads(self._contents_path(number).read_text())

    def _object_path(self, digest: str) -> Path:
        return self._objects / digest[:2] / digest[2:]

    def _has_object(self, digest: str) -> bool:
        return self._object_path(digest).exists()

    def _store_file(self, path: Path, work_real: str) -> str | None:
        """Copy one workspace file into the store. None if it isn't a plain file."""
        fd = _open_inside(path, work_real)
        if fd is None:
            return None
        tmp = self._objects / f".incoming-{os.getpid()}"
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "rb") as source, open(tmp, "wb") as out:
                while chunk := source.read(_CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
            target = self._object_path(digest.hexdigest())
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return digest.hexdigest()

    def _ensure_dir(self, rel: str) -> None:
        """Make `rel` a real folder, replacing a link or file in the way."""
        current = self._work
        for part in rel.split("/"):
            current = current / part
            if _is_link(current) or (current.exists() and not current.is_dir()):
                current.unlink()
            if not current.exists():
                current.mkdir()

    def _write_if_changed(self, rel: str, entry: dict[str, Any]) -> bool:
        target = self._work / rel
        for parent in _parents(rel):
            self._ensure_dir(parent)
        if _is_link(target):
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)  # nothing kept is inside: see restore's `blocked`
        elif (
            target.is_file()
            and target.stat().st_size == entry["size"]
            and _sha256(target) == entry["sha256"]
        ):
            _set_executable(target, entry["executable"])
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY
        while True:
            tmp = target.with_name(f".datalab-restore-{secrets.token_hex(6)}")
            try:
                fd = os.open(tmp, flags, 0o644)
                break
            except FileExistsError:
                continue
        with os.fdopen(fd, "wb") as out, open(self._object_path(entry["sha256"]), "rb") as source:
            shutil.copyfileobj(source, out, _CHUNK)
        _set_executable(tmp, entry["executable"])
        os.replace(tmp, target)
        return True

    def _restore_link(self, rel: str, target: str) -> None:
        """Recreate a link the agent had made. The link is never followed."""
        path = self._work / rel
        if path.is_symlink() and os.readlink(path) == target:
            return
        if _is_link(path) or path.is_file():
            path.unlink()
        elif path.is_dir():
            return  # something else is there now; leave it
        # Windows without the right to make links: skip it.
        with contextlib.suppress(OSError):
            os.symlink(target, path)


def _walk(root: Path):
    """Yield (relative path, kind) for everything under `root`.

    Never follows links. Kinds: "dir", "file", "link", "other", and
    "unreadable" for a folder (already yielded as "dir") that can't be listed.
    Entries that vanish while walking are skipped.
    """
    stack = [("", root)]
    while stack:
        prefix, folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            if prefix:
                yield prefix.rstrip("/"), "unreadable"
            continue
        for entry in sorted(entries, key=lambda e: e.name):
            rel = f"{prefix}{entry.name}"
            try:
                info = entry.stat(follow_symlinks=False)
                junction = entry.is_junction()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or junction:
                yield rel, "link"
            elif stat.S_ISDIR(info.st_mode):
                yield rel, "dir"
                stack.append((f"{rel}/", Path(entry.path)))
            elif stat.S_ISREG(info.st_mode):
                yield rel, "file"
            else:
                yield rel, "other"


def _open_inside(path: Path, root_real: str) -> int | None:
    """Open a regular file for reading, only if it really is inside `root_real`."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _BINARY)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or not is_inside(path, root_real):
            os.close(fd)
            return None
        if _NONBLOCK:
            os.set_blocking(fd, True)
    except OSError:
        os.close(fd)
        return None
    return fd


def is_inside(path: Path, root_real: str) -> bool:
    resolved = os.path.realpath(path)
    return resolved.startswith(root_real.rstrip(os.sep) + os.sep)


def check_relative(rel: str) -> list[str]:
    """The parts of a relative path, or UnsafePath if it could point elsewhere."""
    if (
        not rel
        or "\\" in rel
        or (":" in rel and sys.platform == "win32")  # drive letters, alternate streams
        or "\0" in rel
    ):
        raise UnsafePath(rel)
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise UnsafePath(rel)
    return parts


def open_workspace_file(root: Path, rel: str) -> int:
    """Open a file under `root` for reading, refusing links and escapes.

    Only for folders the agent can't change (query results). Workspace files
    are served from checkpoints instead.
    """
    parts = check_relative(rel)
    root_real = os.path.realpath(root)
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise UnsafePath(rel)
    fd = _open_inside(root / rel, root_real)
    if fd is None:
        raise UnsafePath(rel)
    return fd


def list_files(root: Path) -> list[tuple[str, os.stat_result]]:
    """Plain files under `root` (no links), with their lstat. For folders the agent can't change."""
    found = []
    for rel, kind in _walk(root):
        if kind == "file":
            try:
                found.append((rel, os.lstat(root / rel)))
            except OSError:
                continue
    return found


def _under_any(rel: str, prefixes: set[str]) -> bool:
    return any(rel.startswith(p + "/") for p in prefixes)


def _under_any_of(paths: set[str], folder: str) -> bool:
    """Is any of `paths` inside `folder`?"""
    return any(p.startswith(folder + "/") for p in paths)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not _is_link(path)


def _make_folders_writable(root: Path) -> None:
    """Give DataLab back read and write access to every folder under `root`.

    Only while the container is stopped. Never follows links.
    """
    stack = [root]
    while stack:
        folder = stack.pop()
        with contextlib.suppress(OSError):
            mode = os.lstat(folder).st_mode
            if stat.S_ISDIR(mode) and (mode & 0o700) != 0o700:
                os.chmod(folder, mode | 0o700)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            with contextlib.suppress(OSError):
                if entry.is_dir(follow_symlinks=False) and not entry.is_junction():
                    stack.append(Path(entry.path))


def _parents(rel: str) -> list[str]:
    parts = rel.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _summary(path: Path) -> Checkpoint:
    raw = json.loads(path.read_text())
    raw["skipped"] = [Skipped(**s) for s in raw["skipped"]]
    return Checkpoint(**raw)


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _set_executable(path: Path, executable: bool) -> None:
    mode = path.stat().st_mode
    wanted = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if executable else mode & ~0o111
    if wanted != mode:
        path.chmod(wanted)
