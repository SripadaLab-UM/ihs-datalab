"""Exports: the only way results leave DataLab, and only when the person asks.

Each export goes into its own dated folder in a destination the person chose,
with a manifest (`datalab-export.json`) saying what it is, when it was made,
and which conversation produced it. Nothing already in the destination is
ever overwritten.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from datalab import __version__
from datalab.htmlclean import clean_fragment
from datalab.sessions.titles import scrub_title
from datalab.svgcheck import is_static_svg

MANIFEST = "datalab-export.json"
# The agent's files go in here, apart from DataLab's report and manifest.
FILES = "files"
# File types that run code or open something else when double-clicked.
_ACTIVE_TYPES = {
    "bat", "cmd", "com", "exe", "scr", "pif", "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta",
    "lnk", "url", "ps1", "psm1", "msi", "msp", "cpl", "jar", "reg", "library-ms",
    "settingcontent-ms", "webloc", "inetloc", "fileloc", "terminal", "command", "app", "scpt",
    "applescript", "workflow", "action", "pkg", "dmg", "sh", "desktop", "chm", "application",
    "gadget", "msc", "inf", "xll", "iqy", "slk", "search-ms", "mht", "mhtml", "xhtml", "shtml",
    "xht", "svgz", "scf", "jnlp", "wsc", "sct", "website", "ps1xml", "psc1", "docm", "dotm",
    "xlsm", "xltm", "xlam", "pptm", "potm", "ppam", "sldm", "appref-ms",
}  # fmt: skip
_XML_ENCODING = re.compile(r"<\?xml[^>]*encoding\s*=\s*[\"']([^\"']+)", re.IGNORECASE)

# Exported conversation reports can't load or send anything, wherever they're opened.
REPORT_POLICY = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; "
    "form-action 'none'; base-uri 'none'"
)


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Destination:
    id: str
    name: str
    path: str
    added_at: str
    # What workflow files call it (`deliver: destination:`), set per computer.
    key: str | None = None
    # Offered for exports and workflow deliveries (Settings → Export folders).
    # Off: kept, but nothing is written there.
    offered: bool = True

    @property
    def available(self) -> bool:
        return Path(self.path).is_dir()


@dataclass(frozen=True)
class ExportSource:
    """One file to export: how to open it, and where it goes in the export."""

    open: Callable[[], int]  # returns a file descriptor to read
    path: str
    # Where it came from in the container, for the manifest.
    container_path: str
    # An agent-made web page as it is, scripts and all (the person chose to).
    raw_html: bool = False
    # For a web page: an image it uses, by relative path, as bytes (or None).
    sibling: Callable[[str], bytes | None] | None = None


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    files: list[str]
    # The manifest's "files" entries, and the manifest file's own sha256.
    entries: list[dict[str, Any]] = field(default_factory=list)
    manifest_sha256: str = ""


# Folders written to through an open handle ------------------------------------

# Where the system can: every write is made relative to an open folder
# (dir_fd), so a folder swapped for a link, or for another folder, after it
# was checked can't redirect anything. Elsewhere (Windows), the folder's
# identity is checked again before each step.
_BY_FD = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and {os.open, os.mkdir, os.unlink, os.stat} <= os.supports_dir_fd
    and shutil.rmtree.avoids_symlink_attacks
)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
Identity = tuple[int, int]
FOLDER_CHANGED = (
    "The export folder changed after DataLab checked it (it was moved, or replaced by a "
    "link or another folder), so nothing was written. Test it again in Settings."
)


def is_link(path: Path, info: os.stat_result) -> bool:
    """A link of any kind: a symlink, or on Windows a junction or other reparse
    point (lstat reports a junction as a plain folder)."""
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(isjunction and isjunction(path))


def identity(info: os.stat_result) -> Identity:
    """Which folder this is on disk, whatever it's called."""
    return (info.st_dev, info.st_ino)


class Folder:
    """An open folder: files made through it stay in it, even if its path changes.

    `path` is only for showing and for the manifest; writes never go by it
    where the system can open files relative to a folder.
    """

    def __init__(self, path: Path, fd: int | None, ident: Identity) -> None:
        self.path = path
        self._fd = fd
        self.identity = ident

    @classmethod
    def at(cls, path: Path, expected: Identity | None = None) -> Folder:
        """Open a folder that isn't a link. With `expected`, it must be the very
        folder that was checked (same device and inode), else ExportError."""
        fd = None
        try:
            if _BY_FD:
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
                info = os.fstat(fd)
            else:
                info = os.lstat(path)
                if is_link(path, info) or not stat.S_ISDIR(info.st_mode):
                    raise NotADirectoryError(str(path))
        except FileNotFoundError as error:
            raise ExportError(
                "The export folder isn't there any more. Choose it again in Settings."
            ) from error
        except OSError as error:
            raise ExportError(FOLDER_CHANGED) from error
        if expected is not None and identity(info) != expected:
            if fd is not None:
                os.close(fd)
            raise ExportError(FOLDER_CHANGED)
        return cls(path, fd, identity(info))

    def __enter__(self) -> Folder:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _where(self, name: str) -> tuple[Any, dict[str, Any]]:
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise ExportError(f"Can't write {name!r}.")
        if self._fd is not None:
            return name, {"dir_fd": self._fd}
        info = os.lstat(self.path)
        if is_link(self.path, info) or identity(info) != self.identity:
            raise ExportError(FOLDER_CHANGED)
        return self.path / name, {}

    def mkdir(self, name: str) -> None:
        where, kw = self._where(name)
        os.mkdir(where, 0o777, **kw)

    def child(self, name: str) -> Folder:
        """A folder inside this one, opened the same way (never through a link)."""
        where, kw = self._where(name)
        try:
            if self._fd is not None:
                fd = os.open(where, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC, **kw)
                return Folder(self.path / name, fd, identity(os.fstat(fd)))
        except OSError as error:
            raise ExportError(f"{name} in the export folder is a link or a file.") from error
        return Folder.at(self.path / name)

    def open_file(self, name: str, flags: int, mode: int = 0o644) -> int:
        where, kw = self._where(name)
        return os.open(where, flags | _NOFOLLOW | _CLOEXEC, mode, **kw)

    def lstat(self, name: str) -> os.stat_result:
        where, kw = self._where(name)
        return os.stat(where, follow_symlinks=False, **kw)

    def link_new(self, name: str, new_name: str) -> None:
        """Give the file `name` the name `new_name` as well, never replacing a
        file already called that (FileExistsError).

        A hard link where the folder can hold one. Otherwise (some drives and
        sync folders) a rename that refuses an existing name: renameatx_np
        with RENAME_EXCL on a Mac, renameat2 with RENAME_NOREPLACE on Linux,
        and os.rename on Windows, which never replaces a file. Only where
        none of these works is the name checked and then renamed, which
        leaves a moment in which another program could put a file there.
        """
        where, kw = self._where(name)
        target, _ = self._where(new_name)
        fd = kw.get("dir_fd")
        try:
            os.link(where, target, src_dir_fd=fd, dst_dir_fd=fd)
            return
        except FileExistsError:
            raise
        except OSError:
            pass
        if sys.platform == "win32":
            os.rename(where, target)  # refuses an existing name
            return
        if _rename_noreplace(where, target, fd):
            return
        try:
            self.lstat(new_name)
        except FileNotFoundError:
            os.rename(where, target, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            raise FileExistsError(new_name)

    def names(self) -> list[str]:
        """What's in the folder, listed through the open folder itself."""
        return os.listdir(self._fd if self._fd is not None else self.path)

    def unlink(self, name: str) -> None:
        where, kw = self._where(name)
        os.unlink(where, **kw)

    def rmtree(self, name: str) -> None:
        where, kw = self._where(name)
        shutil.rmtree(where, ignore_errors=True, **kw)


def _rename_noreplace(source: Any, target: Any, fd: int | None) -> bool:
    """Rename without ever replacing a file (FileExistsError if one is there).
    False where the system can't (the caller then falls back)."""
    import ctypes
    import errno

    if sys.platform == "darwin":
        symbol, flags, here = "renameatx_np", 0x00000004, -2  # RENAME_EXCL, AT_FDCWD
    elif sys.platform.startswith("linux"):
        symbol, flags, here = "renameat2", 0x1, -100  # RENAME_NOREPLACE, AT_FDCWD
    else:
        return False
    try:
        call = getattr(ctypes.CDLL(None, use_errno=True), symbol)
    except (OSError, AttributeError):
        return False
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    folder = here if fd is None else fd
    if call(folder, os.fsencode(source), folder, os.fsencode(target), flags) == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(str(target))
    if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
        return False
    raise OSError(code, os.strerror(code), str(target))


class DestinationStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def list(self) -> list[Destination]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, path, added_at, key, offered FROM export_destinations "
                "ORDER BY rowid"
            ).fetchall()
        return [Destination(*row[:5], offered=bool(row[5])) for row in rows]

    def get(self, destination_id: str) -> Destination | None:
        return next((d for d in self.list() if d.id == destination_id), None)

    def by_key(self, key: str) -> Destination | None:
        """The destination a workflow file names, as mapped on this computer."""
        return next((d for d in self.list() if d.key == key), None)

    def set_key(self, destination_id: str, key: str | None) -> bool:
        """Map a workflow destination key to this destination, moving it from
        any other one: a key names exactly one folder per computer."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if key is not None:
                    self._db.execute(
                        "UPDATE export_destinations SET key = NULL WHERE key = ?", (key,)
                    )
                cursor = self._db.execute(
                    "UPDATE export_destinations SET key = ? WHERE id = ?", (key, destination_id)
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return cursor.rowcount > 0

    def set_key_if_free(self, destination_id: str, key: str) -> bool:
        """Map `key` to this destination only if neither is mapped yet, checked
        and set in one transaction, so a mapping made meanwhile (in Settings)
        is never taken over. Whether it was mapped."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._db.execute(
                    "UPDATE export_destinations SET key = ? WHERE id = ? AND key IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM export_destinations WHERE key = ?)",
                    (key, destination_id, key),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return cursor.rowcount > 0

    def add(self, name: str, path: Path) -> Destination:
        destination = Destination(
            id=f"dest_{secrets.token_hex(6)}",
            name=name,
            path=str(path),
            added_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        with self._lock:
            # Columns named: the table gains columns over time (0008 added `key`).
            self._db.execute(
                "INSERT INTO export_destinations (id, name, path, added_at) VALUES (?, ?, ?, ?)",
                (destination.id, destination.name, destination.path, destination.added_at),
            )
        return destination

    def rename(self, destination_id: str, name: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE export_destinations SET name = ? WHERE id = ?", (name, destination_id)
            )
        return cursor.rowcount > 0

    def set_offered(self, destination_id: str, offered: bool) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE export_destinations SET offered = ? WHERE id = ?",
                (int(offered), destination_id),
            )
        return cursor.rowcount > 0

    def remove(self, destination_id: str) -> bool:
        """Forget a destination. The folder and everything in it stay as they are."""
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM export_destinations WHERE id = ?", (destination_id,)
            )
        return cursor.rowcount > 0


def export(
    destination: Path | Folder,
    *,
    title: str,
    tag: str = "",
    sources: list[ExportSource],
    extra_files: dict[str, bytes] | None = None,
    about: dict[str, Any],
) -> ExportResult:
    """Copy files into a new dated folder in `destination`, with a manifest.

    `destination` is best a Folder opened from the folder that was checked
    (export_folders.open_target): then everything is written inside that
    very folder. A path is opened here (never through a link).

    The folder is named for the date, the title with anything shaped like a
    study identifier taken out, and `tag` (the conversation's ID), so the
    name says which conversation it's from without leaning on its title.

    The agent's files go under `files/`, so none can pose as DataLab's own
    report or manifest. Types that run when opened get `.txt` added, and
    every file is marked as downloaded, so the computer treats it with the
    same caution as a file from the internet.
    """
    if isinstance(destination, Folder):
        return _export(destination, title, tag, sources, extra_files, about)
    with Folder.at(destination) as root:
        return _export(root, title, tag, sources, extra_files, about)


def _export(
    root: Folder,
    title: str,
    tag: str,
    sources: list[ExportSource],
    extra_files: dict[str, bytes] | None,
    about: dict[str, Any],
) -> ExportResult:
    name, folder = _new_folder(root, title, tag)
    written: list[dict[str, Any]] = []
    try:
        with folder:
            for source in sources:
                written.append(_copy(folder, source))
            for relative, data in (extra_files or {}).items():
                size, digest = _write(folder, relative, [data])
                written.append({"path": relative, "bytes": size, "sha256": digest})
            manifest = {
                "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "datalab_version": __version__,
                **about,
                "files": written,
            }
            text = (json.dumps(manifest, indent=2) + "\n").encode()
            _, manifest_sha256 = _write(folder, MANIFEST, [text], downloaded=False)
    except BaseException:
        # Leave no half-finished export behind.
        root.rmtree(name)
        raise
    return ExportResult(root.path / name, [w["path"] for w in written], written, manifest_sha256)


def _write(folder: Folder, relative: str, chunks, *, downloaded: bool = True) -> tuple[int, str]:
    """Write a new file at `relative` inside `folder`; its size and sha256."""
    parent, name = _target(folder, relative)
    digest = hashlib.sha256()
    size = 0
    try:
        fd = parent.open_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as out:
            for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
    finally:
        if parent is not folder:
            parent.close()
    if downloaded:
        mark_downloaded(folder.path.joinpath(*relative.split("/")))
    return size, digest.hexdigest()


def _copy(folder: Folder, source: ExportSource) -> dict[str, Any]:
    """Copy one of the agent's files into the export, made inert where needed."""
    # The name Windows would actually use: it drops trailing dots and spaces,
    # so "run.bat." is run.bat. Classify (and write) that name.
    path = effective_name(source.path)
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    entry: dict[str, Any] = {"from": source.container_path}
    if suffix in ("html", "htm") and not source.raw_html:
        # A web page is exported as an inert copy: cleaned, no outside URLs,
        # and a policy that blocks every request.
        with os.fdopen(source.open(), "rb") as reader:
            data = inert_page(reader.read().decode("utf-8", "replace"), source.sibling)
        name = f"{FILES}/{path}"
        entry["made_inert"] = True
    elif suffix == "svg":
        with os.fdopen(source.open(), "rb") as reader:
            data = reader.read()
        name = f"{FILES}/{path}" if svg_is_inert(data) else f"{FILES}/{path}.txt"
    else:
        name = f"{FILES}/{inert_name(path)}"
        data = None
    if data is not None:
        size, digest = _write(folder, name, [data])
    else:
        with os.fdopen(source.open(), "rb") as reader:
            size, digest = _write(folder, name, iter(lambda: reader.read(1024 * 1024), b""))
    if name != f"{FILES}/{source.path}":
        entry["renamed_from"] = source.path
    return {"path": name, **entry, "bytes": size, "sha256": digest}


_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}  # fmt: skip
_MAX_EMBEDDED = 10 * 1024**2


def effective_name(path: str) -> str:
    """`path` with trailing dots and spaces dropped from each part, as Windows does."""
    return "/".join(part.rstrip(" .") or "_" for part in path.split("/"))


def svg_is_inert(data: bytes) -> bool:
    """Whether an SVG has nothing that could run, load, or link elsewhere.

    Only plain UTF-8 is scanned: a file in another encoding (UTF-16, with or
    without a byte-order mark) would hide its contents from the scan.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf\x00")) or b"\x00" in data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    declared = _XML_ENCODING.search(text)
    if declared and declared.group(1).lower().replace("_", "-") not in (
        "utf-8",
        "us-ascii",
        "ascii",
    ):
        return False
    return is_static_svg(text)


def inert_page(source: str, sibling: Callable[[str], bytes | None] | None = None) -> bytes:
    """An agent-made web page, cleaned so it can't load or send anything when opened.

    Images it shows from its own folder are embedded, so it still looks right.
    """

    def embed(url: str) -> str | None:
        suffix = url.rsplit(".", 1)[-1].lower() if "." in url else ""
        if sibling is None or suffix not in _IMAGE_TYPES or ":" in url or url.startswith("/"):
            return None
        data = sibling(url)
        if data is None or len(data) > _MAX_EMBEDDED:
            return None
        return f"data:{_IMAGE_TYPES[suffix]};base64,{base64.b64encode(data).decode()}"

    return (
        '<!doctype html>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{REPORT_POLICY}">\n'
        '<meta http-equiv="x-dns-prefetch-control" content="off">\n'
        '<meta name="referrer" content="no-referrer">\n'
        + clean_fragment(source, offline=True, inline=embed)
    ).encode()


def inert_name(path: str) -> str:
    """`path`, with `.txt` added if its type would run or open something when opened."""
    name = path.rsplit("/", 1)[-1]
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in _ACTIVE_TYPES or name.lower().endswith(".appref-ms"):
        return f"{path}.txt"
    return path


def mark_downloaded(path: Path) -> None:
    """Mark a file as coming from outside, as a browser marks downloads.

    Windows then asks before running it (Mark of the Web); macOS checks it
    with Gatekeeper (the quarantine flag). Best effort elsewhere.
    """
    if sys.platform == "win32":
        with (
            contextlib.suppress(OSError),
            open(f"{path}:Zone.Identifier", "w", encoding="utf-8") as stream,
        ):
            stream.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    elif sys.platform == "darwin":
        flag = f"0083;{int(datetime.now().timestamp()):x};DataLab;"
        with contextlib.suppress(OSError):
            subprocess.run(
                # -s: the file itself, never what a link there points to.
                ["xattr", "-s", "-w", "com.apple.quarantine", flag, str(path)],
                check=False,
                capture_output=True,
            )


def report_document(title: str, body_html: str, css: str) -> bytes:
    """A self-contained conversation report that can't load or send anything.

    The policy comes first in <head>, so whatever the body contains is bound
    by it: no scripts, no outside images, no requests. The body is cleaned as
    previews are (no links, meta refreshes, or scripts), since it carries the
    agent's words, and every URL that isn't a data: URL is removed too, so it
    stays inert even in an app that ignores the policy.
    """
    body_html = clean_fragment(body_html, offline=True)
    safe_title = html.escape(title)
    safe_css = css.replace("<", "\\3c ")  # can't close the <style> element early
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{REPORT_POLICY}">\n'
        '<meta http-equiv="x-dns-prefetch-control" content="off">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f"<title>{safe_title}</title>\n<style>{safe_css}</style>\n"
        f"</head><body>\n{body_html}\n</body></html>\n"
    ).encode()


def _new_folder(destination: Folder, title: str, tag: str = "") -> tuple[str, Folder]:
    """A new dated folder in `destination`: its name, and the folder, open."""
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    name = safe_name(scrub_title(title))
    base = f"{stamp} {name} {safe_name(tag, 40)}" if tag else f"{stamp} {name}"
    for number in range(1, 1000):
        candidate = base if number == 1 else f"{base} ({number})"
        try:
            destination.mkdir(candidate)
        except FileExistsError:
            continue
        return candidate, destination.child(candidate)
    raise ExportError("Couldn't make a new folder for the export.")


def _target(folder: Folder, relative: str) -> tuple[Folder, str]:
    """The open folder a file at `relative` goes in (made as needed), and its name.
    The caller closes the folder unless it's `folder` itself."""
    parts = relative.split("/")
    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
        raise ExportError(f"Can't export {relative!r}.")
    current = folder
    for part in parts[:-1]:
        with contextlib.suppress(FileExistsError):
            current.mkdir(part)
        inner = current.child(part)
        if current is not folder:
            current.close()
        current = inner
    return current, parts[-1]


def safe_name(title: str, limit: int = 60, max_bytes: int = 120) -> str:
    """A folder name that works on Mac and Windows."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:limit]
    while len(name.encode()) > max_bytes:
        name = name[:-1]
    return name.rstrip(" .") or "export"
