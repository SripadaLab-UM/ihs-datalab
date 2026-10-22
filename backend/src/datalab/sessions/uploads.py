"""Files and folders uploaded to a conversation from the browser (the lab's
server, where the person's own disk isn't DataLab's: `deployment = "server"`).

An upload is stored in the conversation's own folder and then attached like
any other input: mounted read-only at `/inputs/<name>`.

```
sessions/<conversation>/uploads/<upload id>/upload.json   its kind, size, finished
sessions/<conversation>/uploads/<upload id>/content/...   what was uploaded
```

The browser sends one file per request (api/inputs.py), each with its path
inside the upload: a name for "files", `<folder>/<path inside it>` for a
folder. Names are checked like an attached item's, credentials files are
refused, and sizes are capped per file and per conversation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from datalab.sessions.inputs import _CREDENTIAL_FILES, _UNPRINTABLE, Attachment

UploadKind = Literal["files", "folder"]

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_CONVERSATION_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 10_000  # in one upload
_MAX_PART_BYTES = 200
_MAX_DEPTH = 32
_ID = re.compile(r"up_[0-9a-f]{12}")


class UploadRefused(ValueError):
    """This file (or upload) can't be taken; the message says why."""


class UploadTooLarge(UploadRefused):
    pass


@dataclass(frozen=True)
class Upload:
    id: str
    kind: UploadKind
    files: int
    bytes: int
    finished: bool


class Uploads:
    def __init__(self, sessions_dir: Path) -> None:
        self._sessions = sessions_dir
        self._lock = threading.Lock()

    def folder(self, conversation_id: str) -> Path:
        return self._sessions / conversation_id / "uploads"

    def start(self, conversation_id: str, kind: UploadKind) -> Upload:
        upload = Upload(f"up_{secrets.token_hex(6)}", kind, 0, 0, False)
        place = self.folder(conversation_id) / upload.id
        (place / "content").mkdir(parents=True, mode=0o700)
        self._save(place, upload)
        return upload

    def get(self, conversation_id: str, upload_id: str) -> Upload | None:
        if not _ID.fullmatch(upload_id):
            return None
        return self._load(self.folder(conversation_id) / upload_id)

    async def write(
        self, conversation_id: str, upload_id: str, path: str, body: AsyncIterator[bytes]
    ) -> int:
        """Store one file of an upload, from the request's body; its size."""
        upload = self.get(conversation_id, upload_id)
        if upload is None:
            raise UploadRefused("No such upload.")
        if upload.finished:
            raise UploadRefused("That upload is finished; start another.")
        if upload.files >= MAX_FILES:
            raise UploadTooLarge(f"An upload can hold at most {MAX_FILES:,} files.")
        parts = check_path(path, upload.kind)
        place = self.folder(conversation_id) / upload_id
        target = place / "content" / Path(*parts)
        if target.exists():
            raise UploadRefused(f"{path} was uploaded already.")
        room = MAX_CONVERSATION_BYTES - self.used(conversation_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = target.with_name(f".{target.name}.part")
        size = 0
        try:
            with open(partial, "xb") as file:
                async for chunk in body:
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise UploadTooLarge(
                            f"{parts[-1]} is larger than {MAX_FILE_BYTES // 2**20} MB, "
                            "the most one file may be."
                        )
                    if size > room:
                        raise UploadTooLarge(
                            "This conversation's uploads would pass "
                            f"{MAX_CONVERSATION_BYTES // 2**30} GB. Remove some first."
                        )
                    file.write(chunk)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        with self._lock:
            current = self._load(place) or upload
            self._save(place, Upload(upload.id, upload.kind, current.files + 1,
                                     current.bytes + size, False))  # fmt: skip
        return size

    def finish(self, conversation_id: str, upload_id: str) -> list[Path]:
        """What the upload attaches: each file, or the one folder."""
        upload = self.get(conversation_id, upload_id)
        if upload is None:
            raise UploadRefused("No such upload.")
        place = self.folder(conversation_id) / upload_id
        content = place / "content"
        # Not a folder left empty by a file that was refused.
        items = sorted(
            p
            for p in content.iterdir()
            if not p.name.startswith(".") and (p.is_file() or any(_files(p)))
        )
        if not items:
            shutil.rmtree(place, ignore_errors=True)
            raise UploadRefused("Nothing was uploaded.")
        with self._lock:
            self._save(place, Upload(upload.id, upload.kind, upload.files, upload.bytes, True))
        return items

    def used(self, conversation_id: str) -> int:
        """Bytes this conversation's uploads hold."""
        folder = self.folder(conversation_id)
        if not folder.is_dir():
            return 0
        total = 0
        for place in folder.iterdir():
            upload = self._load(place)
            total += upload.bytes if upload else 0
        return total

    def owns(self, conversation_id: str, attachment: Attachment) -> bool:
        """Whether an attachment is one of this conversation's uploads, still
        where it was put (no link anywhere in its path)."""
        path = Path(attachment.host_path)
        content = self.folder(conversation_id)
        if os.path.realpath(path) != str(path) or not path.is_relative_to(content):
            return False
        relative = path.relative_to(content).parts
        return (
            len(relative) == 3
            and _ID.fullmatch(relative[0]) is not None
            and (relative[1] == "content")
        )

    def forget(self, conversation_id: str, attachment: Attachment) -> None:
        """Delete a removed attachment's uploaded files (and its upload, once empty)."""
        if not self.owns(conversation_id, attachment):
            return
        path = Path(attachment.host_path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        place = path.parent.parent
        with self._lock:
            upload = self._load(place)
            remaining = [p for p in (place / "content").iterdir()] if upload else []
            if not remaining:
                shutil.rmtree(place, ignore_errors=True)
            elif upload is not None:
                left = sum(_size(p) for p in remaining)
                self._save(place, Upload(upload.id, upload.kind, upload.files, left, True))

    def _save(self, place: Path, upload: Upload) -> None:
        record = {
            "kind": upload.kind,
            "files": upload.files,
            "bytes": upload.bytes,
            "finished": upload.finished,
        }
        temporary = place / "upload.json.new"
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(place / "upload.json")

    def _load(self, place: Path) -> Upload | None:
        try:
            record = json.loads((place / "upload.json").read_text(encoding="utf-8"))
            return Upload(
                place.name,
                record["kind"],
                int(record["files"]),
                int(record["bytes"]),
                bool(record["finished"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None


def check_path(path: str, kind: UploadKind) -> list[str]:
    """A file's path inside an upload, as parts, or UploadRefused."""
    parts = path.split("/")
    if kind == "files" and len(parts) != 1:
        raise UploadRefused("Upload files one by one, without folders.")
    if kind == "folder" and len(parts) < 2:
        raise UploadRefused("A folder's files are sent as <folder>/<path inside it>.")
    if len(parts) > _MAX_DEPTH:
        raise UploadRefused(f"{path[:80]} is too deep inside its folder.")
    for part in parts:
        if (
            part in ("", ".", "..")
            or "\\" in part
            or part != part.strip()
            or _UNPRINTABLE.search(part)
            or len(part.encode()) > _MAX_PART_BYTES
        ):
            raise UploadRefused(f"{path[:80]!r} has a name DataLab can't take; rename it first.")
    if parts[-1].lower() in _CREDENTIAL_FILES:
        raise UploadRefused(f"{parts[-1]} looks like a credentials file, so it wasn't uploaded.")
    return parts


def _files(folder: Path):
    return (p for p in folder.rglob("*") if p.is_file())


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in _files(path))
