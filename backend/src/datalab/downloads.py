"""Exports as downloads, on the lab's server (`deployment = "server"`), where
the person's own folders aren't DataLab's to write to.

An export is written as anywhere else (exports.export: the dated folder,
its files and manifest), into the person's own staging folder; then it's
zipped, and the browser downloads the zip from `GET /api/downloads/<id>`.
Each person reaches only their own (their folder, by their user id), and a
download is kept for a day.

```
downloads/user-<id>/staging/     exports being written (export_folders.download_target)
downloads/user-<id>/<dl id>.zip  ready to download
downloads/user-<id>/<dl id>.json its name, size and conversation
```
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

KEEP_SECONDS = 24 * 60 * 60
_ID = re.compile(r"dl_[0-9a-f]{32}")


@dataclass(frozen=True)
class Download:
    id: str
    name: str  # the zip's file name, for the browser
    files: int
    bytes: int
    session_id: str
    path: Path

    @property
    def url(self) -> str:
        return f"/api/downloads/{self.id}"


class Downloads:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "downloads"

    def folder(self, user_id: int) -> Path:
        return self.root / f"user-{int(user_id)}"

    def staging(self, user_id: int) -> Path:
        folder = self.folder(user_id) / "staging"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder

    def make(
        self, user_id: int, exported: Path, *, files: int, session_id: str, keep: bool = False
    ) -> Download:
        """Zip an export's dated folder for the browser, then remove the folder
        (`keep`: a workflow run's delivery, which stays on the server)."""
        self.prune(user_id)
        download_id = f"dl_{secrets.token_hex(16)}"
        target = self.folder(user_id) / f"{download_id}.zip"
        with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(exported.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    archive.write(path, Path(exported.name) / path.relative_to(exported))
        if not keep:
            shutil.rmtree(exported, ignore_errors=True)
        download = Download(
            download_id,
            f"{exported.name}.zip",
            files,
            target.stat().st_size,
            session_id,
            target,
        )
        record = {
            "name": download.name,
            "files": files,
            "bytes": download.bytes,
            "session_id": session_id,
            "made": time.time(),
        }
        (self.folder(user_id) / f"{download_id}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        return download

    def get(self, user_id: int, download_id: str) -> Download | None:
        """One of this person's downloads, while it's kept."""
        if not _ID.fullmatch(download_id):
            return None
        folder = self.folder(user_id)
        try:
            record = json.loads((folder / f"{download_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        path = folder / f"{download_id}.zip"
        if time.time() - float(record.get("made", 0)) > KEEP_SECONDS or not path.is_file():
            self._remove(folder, download_id)
            return None
        return Download(
            download_id,
            str(record["name"]),
            int(record["files"]),
            int(record["bytes"]),
            str(record["session_id"]),
            path,
        )

    def prune(self, user_id: int) -> None:
        """Remove this person's downloads older than a day, and any export
        left half-written in their staging folder."""
        folder = self.folder(user_id)
        if not folder.is_dir():
            return
        for record in folder.glob("dl_*.json"):
            self.get(user_id, record.stem)
        staging = folder / "staging"
        if staging.is_dir():
            for left in staging.iterdir():
                if time.time() - left.stat().st_mtime > KEEP_SECONDS:
                    shutil.rmtree(left, ignore_errors=True)

    def _remove(self, folder: Path, download_id: str) -> None:
        for suffix in (".zip", ".json"):
            with contextlib.suppress(OSError):
                os.unlink(folder / f"{download_id}{suffix}")
