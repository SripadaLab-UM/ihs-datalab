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
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
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


class DestinationStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def list(self) -> list[Destination]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, path, added_at, key FROM export_destinations ORDER BY rowid"
            ).fetchall()
        return [Destination(*row) for row in rows]

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

    def remove(self, destination_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM export_destinations WHERE id = ?", (destination_id,)
            )
        return cursor.rowcount > 0


def export(
    destination: Path,
    *,
    title: str,
    tag: str = "",
    sources: list[ExportSource],
    extra_files: dict[str, bytes] | None = None,
    about: dict[str, Any],
) -> ExportResult:
    """Copy files into a new dated folder in `destination`, with a manifest.

    The folder is named for the date, the title with anything shaped like a
    study identifier taken out, and `tag` (the conversation's ID), so the
    name says which conversation it's from without leaning on its title.

    The agent's files go under `files/`, so none can pose as DataLab's own
    report or manifest. Types that run when opened get `.txt` added, and
    every file is marked as downloaded, so the computer treats it with the
    same caution as a file from the internet.
    """
    if not destination.is_dir():
        raise ExportError("The export folder isn't there any more. Choose it again in Settings.")
    folder = _new_folder(destination, title, tag)
    written: list[dict[str, Any]] = []
    try:
        for source in sources:
            entry = _copy(folder, source)
            written.append(entry)
        for name, data in (extra_files or {}).items():
            target = _target(folder, name)
            with open(target, "xb") as out:
                out.write(data)
            mark_downloaded(target)
            written.append(
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )
        manifest = {
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "datalab_version": __version__,
            **about,
            "files": written,
        }
        with open(folder / MANIFEST, "x", encoding="utf-8") as out:
            out.write(json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        # Leave no half-finished export behind.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return ExportResult(folder, [w["path"] for w in written])


def _copy(folder: Path, source: ExportSource) -> dict[str, Any]:
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
    target = _target(folder, name)
    digest = hashlib.sha256()
    with open(target, "xb") as out:
        if data is not None:
            digest.update(data)
            out.write(data)
        else:
            with os.fdopen(source.open(), "rb") as reader:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
    mark_downloaded(target)
    if name != f"{FILES}/{source.path}":
        entry["renamed_from"] = source.path
    return {"path": name, **entry, "bytes": target.stat().st_size, "sha256": digest.hexdigest()}


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
                ["xattr", "-w", "com.apple.quarantine", flag, str(path)],
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


def _new_folder(destination: Path, title: str, tag: str = "") -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    name = safe_name(scrub_title(title))
    base = f"{stamp} {name} {safe_name(tag, 40)}" if tag else f"{stamp} {name}"
    for number in range(1, 1000):
        candidate = destination / (base if number == 1 else f"{base} ({number})")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise ExportError("Couldn't make a new folder for the export.")


def _target(folder: Path, relative: str) -> Path:
    parts = relative.split("/")
    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
        raise ExportError(f"Can't export {relative!r}.")
    target = folder.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def safe_name(title: str, limit: int = 60, max_bytes: int = 120) -> str:
    """A folder name that works on Mac and Windows."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:limit]
    while len(name.encode()) > max_bytes:
        name = name[:-1]
    return name.rstrip(" .") or "export"
