"""Files and folders attached to a conversation.

Attached items are mounted **read-only** at `/inputs/<name>` in the agent's
container; nothing is copied. Only the person can attach, through the
computer's own picker (see picker.py). Some places are never attachable,
because they hold credentials, system files, or DataLab's own data (which
would expose other conversations).
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Literal

InputKind = Literal["file", "folder"]

# Inside the home folder: credentials and app data. Also refused: every file
# or folder directly in the home folder whose name starts with "." (dotfiles
# such as ~/.netrc, ~/.codex, ~/.Renviron).
_PRIVATE_HOME_FOLDERS = ("Library", "AppData")
# Cloud-synced folders live under ~/Library on a Mac (Dropbox, OneDrive, iCloud
# Drive). They hold research files, so they stay attachable.
_CLOUD_FOLDERS = ("Library/CloudStorage", "Library/Mobile Documents")
# Credential files, refused wherever they are.
_CREDENTIAL_FILES = {
    ".netrc", "_netrc", ".pgpass", ".renviron", ".git-credentials", ".npmrc", ".pypirc",
    ".vault-token", ".databrickscfg", ".env", "credentials", "credentials.json", "auth.json",
    "id_rsa", "id_ed25519", "id_ecdsa",
}  # fmt: skip
_SYSTEM_FOLDERS_POSIX = (
    "/etc", "/System", "/Library", "/private/etc", "/private/var", "/var", "/usr", "/bin",
    "/sbin", "/dev", "/proc", "/sys", "/Applications", "/opt", "/cores", "/tmp", "/private/tmp",
)  # fmt: skip
# Folders that hold whole drives or every user: attachable inside, not whole.
_CONTAINERS_POSIX = ("/Volumes", "/Users", "/home", "/private", "/mnt", "/media")
_MAX_NAME_BYTES = 200
# Control, line-separator, and text-direction characters: never in a name
# the agent is shown.
_UNPRINTABLE = re.compile(
    "[\\x00-\\x1f\\x7f\\u0085\\u061c\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u2069\\ufeff]"
)


class NotAttachable(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    id: str
    conversation_id: str
    name: str
    host_path: str
    kind: InputKind
    added_at: str

    @property
    def container_path(self) -> str:
        return f"/inputs/{self.name}"

    @property
    def available(self) -> bool:
        path = Path(self.host_path)
        return path.is_dir() if self.kind == "folder" else path.is_file()


def practice_samples() -> Path:
    """Synthetic sample files the practice profile offers instead of the computer's own."""
    return Path(str(resources.files("datalab").joinpath("practice_inputs")))


def is_sample(attachment: Attachment, samples: Path) -> bool:
    """Is this one of DataLab's own practice samples (wherever DataLab is installed)?"""
    real = os.path.realpath(attachment.host_path)
    return attachment.available and real.startswith(os.path.realpath(samples) + os.sep)


def check_attachable(
    path: Path, *, protected: list[Path], system_folders: tuple[str, ...] | None = None
) -> tuple[Path, InputKind]:
    """The real location of `path` and its kind, or NotAttachable saying why not.

    `protected` lists DataLab's own folders: they, anything inside them, and
    anything that contains them are refused.
    """
    if not path.is_absolute():
        raise NotAttachable(f"{path} isn't a full path.")
    text = str(path)
    if sys.platform == "win32" and (text.startswith("\\\\") or text.startswith("//")):
        raise NotAttachable("Network locations can't be attached. Copy the files locally first.")
    if text != text.rstrip() or _UNPRINTABLE.search(text):
        raise NotAttachable(f"{path.name!r} has a name DataLab can't attach; rename it first.")
    try:
        real = Path(os.path.realpath(path))
    except OSError as error:
        raise NotAttachable(f"{path} can't be read.") from error
    if sys.platform == "win32" and (str(real).startswith("\\\\") or str(real).startswith("//")):
        # A mapped drive resolves to a network path; Docker can't mount those.
        raise NotAttachable("Network locations can't be attached. Copy the files locally first.")
    if real.is_dir() and os.path.ismount(real):
        raise NotAttachable("That's a whole drive. Attach a folder on it with the files you need.")
    if real.is_dir():
        kind: InputKind = "folder"
    elif real.is_file():
        kind = "file"
    else:
        raise NotAttachable(f"{path} isn't a file or folder DataLab can attach.")

    home = Path(os.path.realpath(Path.home()))
    whole = "That's a whole home folder or drive. Attach the folder with the files you need."
    if _same(real, home) or _inside(home, real) or real.parent == real:
        raise NotAttachable(whole)
    if sys.platform == "win32":
        drive = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
        system = [Path(os.environ.get("SYSTEMROOT", r"C:\Windows")), drive / "ProgramData",
                  drive / "Program Files", drive / "Program Files (x86)"]  # fmt: skip
        containers = [drive / "Users"]
    else:
        system = [
            Path(f) for f in (_SYSTEM_FOLDERS_POSIX if system_folders is None else system_folders)
        ]
        containers = [Path(f) for f in _CONTAINERS_POSIX] if system_folders is None else []
    if any(_same(real, c) for c in containers):
        raise NotAttachable(whole)
    for folder in system:
        if _same(real, folder) or _inside(real, folder) or _inside(folder, real):
            raise NotAttachable("System files can't be attached.")

    cloud = any(_same(real, home / c) or _inside(real, home / c) for c in _CLOUD_FOLDERS)
    if not cloud:
        for folder in _PRIVATE_HOME_FOLDERS:
            private = home / folder
            if _same(real, private) or _inside(real, private):
                raise NotAttachable(
                    f"Files in {private} can't be attached: they're private app data."
                )
        top = _top_in(real, home)
        if top is not None and top.startswith("."):
            raise NotAttachable(f"~/{top} can't be attached: it's private settings or credentials.")
    if kind == "file" and real.name.lower() in _CREDENTIAL_FILES:
        raise NotAttachable(f"{real.name} looks like a credentials file, so it can't be attached.")
    for folder in protected:
        mine = Path(os.path.realpath(folder))
        if _same(real, mine) or _inside(real, mine) or _inside(mine, real):
            raise NotAttachable("DataLab's own data folder can't be attached.")
    return real, kind


def credential_files_in(folder: Path, *, limit: int = 20_000) -> tuple[list[str], bool]:
    """Credential-like files inside a folder, by path under it, and whether the look was complete.

    An attached folder is mounted whole (read-only still lets the agent read
    everything), so the person is told what it contains. Links aren't followed;
    the look stops after `limit` entries, however they're spread out.
    """
    found: list[str] = []
    seen = 0
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        return sorted(found), False
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.name.lower() in _CREDENTIAL_FILES:
                        found.append(os.path.relpath(entry.path, folder))
        except OSError:
            continue  # unreadable here: nothing the agent could read either
    return sorted(found), True


def recheck(attachment: Attachment, *, protected: list[Path]) -> str | None:
    """Why an attachment can't be mounted now, or None if it can.

    Run every time a container starts: the folder may have been replaced by a
    link since it was attached (a sync, `git pull`, or unpacked archive), and
    Docker follows links in the whole path.
    """
    path = Path(attachment.host_path)
    if not attachment.available:
        return f"{attachment.container_path} isn't on this computer any more"
    if os.path.realpath(path) != str(path):
        return f"{attachment.container_path} now points somewhere else (a link was put in its path)"
    try:
        _, kind = check_attachable(path, protected=protected)
    except NotAttachable as error:
        return f"{attachment.container_path} can't be attached any more: {error}"
    if kind != attachment.kind:
        return f"{attachment.container_path} changed from a {attachment.kind} to a {kind}"
    return None


def mount_name(wanted: str, taken: set[str]) -> str:
    """A name under /inputs that doesn't clash: 'data', then 'data (2)', …"""
    base = _UNPRINTABLE.sub("", wanted).replace("/", "_").replace("\\", "_").strip() or "input"
    if base in (".", ".."):
        base = "input"
    stem, dot, suffix = base.rpartition(".")
    if not (dot and stem):
        stem, suffix = base, ""
    # Short enough for any filesystem, keeping the extension.
    while len((stem + dot + suffix).encode()) > _MAX_NAME_BYTES and stem:
        stem = stem[:-1]
    base = f"{stem}{dot}{suffix}" if suffix else stem
    name, number = base, 2
    while name in taken:
        name = f"{stem} ({number}).{suffix}" if suffix else f"{stem} ({number})"
        number += 1
    return name


def mount_args(attachments: list[Attachment]) -> list[str]:
    """`docker run` arguments that mount each attachment read-only.

    Pass only attachments that `recheck` accepted just now.
    """
    args: list[str] = []
    for item in attachments:
        # --mount (not -v) so a missing source is an error, never an empty
        # folder, and colons in Windows paths aren't misread. Values are
        # CSV-quoted, so commas and quotes in names are safe.
        source, target = f"source={item.host_path}", f"target={item.container_path}"
        fields = ["type=bind", source, target, "readonly"]
        args += ["--mount", ",".join(_csv_field(f) for f in fields)]
    return args


def _same(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def _inside(path: Path, folder: Path) -> bool:
    """Is `path` strictly inside `folder`?"""
    return _key(path).startswith(_key(folder).rstrip("/") + "/")


def _top_in(path: Path, folder: Path) -> str | None:
    """The first part of `path` below `folder`, if `path` is inside it."""
    if not _inside(path, folder):
        return None
    return str(path)[len(str(folder).rstrip("/\\")) + 1 :].replace("\\", "/").split("/")[0]


def _key(path: Path) -> str:
    """Paths compared the way the disk does: Mac and Windows ignore case."""
    text = str(path).replace("\\", "/")
    return text.casefold() if sys.platform in ("darwin", "win32") else text


def _csv_field(value: str) -> str:
    if any(c in value for c in ',"\n\r'):
        return '"' + value.replace('"', '""') + '"'
    return value


class AttachmentStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    def add(self, conversation_id: str, host_path: Path, kind: InputKind) -> Attachment:
        with self._lock:
            taken = {a.name for a in self._list(conversation_id)}
            attachment = Attachment(
                id=f"in_{secrets.token_hex(6)}",
                conversation_id=conversation_id,
                name=mount_name(host_path.name, taken),
                host_path=str(host_path),
                kind=kind,
                added_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            self._db.execute(
                "INSERT INTO attachments VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attachment.id,
                    conversation_id,
                    attachment.name,
                    attachment.host_path,
                    kind,
                    attachment.added_at,
                ),
            )
        return attachment

    def list(self, conversation_id: str) -> list[Attachment]:
        with self._lock:
            return self._list(conversation_id)

    def remove(self, conversation_id: str, attachment_id: str) -> Attachment | None:
        with self._lock:
            found = next((a for a in self._list(conversation_id) if a.id == attachment_id), None)
            if found:
                self._db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            return found

    def _list(self, conversation_id: str) -> list[Attachment]:
        rows = self._db.execute(
            "SELECT id, conversation_id, name, host_path, kind, added_at FROM attachments "
            "WHERE conversation_id = ? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
        return [Attachment(*row) for row in rows]
