"""A conversation's files: outputs, query results, previews, and checkpoints.

Files in a workspace are written by the agent, so they are untrusted, and the
agent can change them while DataLab reads them. So:
- workspace files (outputs, /work) are served from the **latest checkpoint**,
  which was taken with the container paused and lives where the agent can't
  reach. They update after each turn. Nothing reads the live folder;
- query results are DataLab's own files, mounted read-only for the agent, so
  they are served directly;
- contents are served with a policy that makes them inert if opened in a tab;
- HTML is previewed through `/preview/<token>/…`, a capability link pinned to
  one checkpoint, cleaned (htmlclean.py), in a sandboxed frame with scripts
  off. See docs/SAFETY.md.
"""

from __future__ import annotations

import os
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from datalab.htmlclean import clean_html
from datalab.sessions.checkpoints import (
    CheckpointMissing,
    Checkpoints,
    Entry,
    UnsafePath,
    check_relative,
    list_files,
    open_workspace_file,
)
from datalab.sessions.containers import DockerError
from datalab.sessions.manager import Busy, SessionManager
from datalab.sessions.store import ConversationStore

Root = Literal["outputs", "work", "results"]
Kind = Literal["html", "image", "csv", "text", "pdf", "other"]

_IMAGES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXT = {
    ".txt", ".md", ".json", ".log", ".py", ".r", ".sql", ".yaml", ".yml", ".toml",
    ".xml", ".qmd", ".rmd", ".ipynb", ".sh", ".tex", ".bib", ".svg", ".css",
}  # fmt: skip
# What an HTML preview may load besides the page: its own styles, images, and fonts.
_PREVIEW_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    **_IMAGES,
}
_MAX_IMAGE_BYTES = 25 * 1024**2
_MAX_PREVIEW_BYTES = 25 * 1024**2
_DEFAULT_HEAD = 256 * 1024
# Workspace files opened directly in a tab are inert: no scripts, no requests.
_FILE_POLICY = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"


class FileOut(BaseModel):
    path: str
    size: int
    modified: str
    kind: Kind


class SkippedOut(BaseModel):
    path: str
    reason: str
    size: int | None


class CheckpointOut(BaseModel):
    number: int
    created_at: str
    label: str
    turn: int | None
    files: int
    bytes: int
    skipped: list[SkippedOut]


class RestoreOut(BaseModel):
    written: int
    removed: int
    # Left as they were: too large to checkpoint, or impossible to save first.
    left_alone: list[str]
    # In the checkpoint, but not put back because that would replace one of those.
    not_restored: list[str]


class NewPreview(BaseModel):
    root: Root
    path: str


class PreviewOut(BaseModel):
    url: str


@dataclass(frozen=True)
class _Grant:
    """What one preview link may show: files under `prefix` in one checkpoint."""

    entries: dict[str, Entry]
    checkpoints: Checkpoints
    prefix: str


class Previews:
    """Capability links for HTML previews. They last until DataLab restarts."""

    def __init__(self, limit: int = 100) -> None:
        self._grants: OrderedDict[str, _Grant] = OrderedDict()
        self._limit = limit

    def grant(self, grant: _Grant) -> str:
        token = secrets.token_urlsafe(24)
        self._grants[token] = grant
        while len(self._grants) > self._limit:
            self._grants.popitem(last=False)
        return token

    def get(self, token: str) -> _Grant | None:
        return self._grants.get(token)


def kind_of(path: str) -> Kind:
    suffix = os.path.splitext(path)[1].lower()
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in _IMAGES:
        return "image"
    if suffix in (".csv", ".tsv"):
        return "csv"
    if suffix == ".pdf":
        return "pdf"
    if suffix in _TEXT:
        return "text"
    return "other"


def build_files_router(
    store: ConversationStore, sessions: SessionManager, previews: Previews
) -> APIRouter:
    router = APIRouter(prefix="/api/conversations/{conversation_id}", tags=["files"])

    def conversation_or_404(conversation_id: str):
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPException(404, "No such conversation.")
        return conversation

    def prefix(root: Root) -> str:
        return "outputs/" if root == "outputs" else ""

    def saved_files(conversation_id: str, root: Root) -> tuple[Checkpoints, dict[str, Entry]]:
        """The workspace files under `root` in the latest checkpoint, by path under it."""
        checkpoints = sessions.checkpoints(conversation_id)
        latest = checkpoints.latest()
        if latest is None:
            return checkpoints, {}
        start = prefix(root)
        return checkpoints, {
            rel[len(start) :]: entry
            for rel, entry in checkpoints.entries(latest.number).items()
            if rel.startswith(start)
        }

    def open_file(conversation_id: str, root: Root, path: str) -> int:
        try:
            check_relative(path)
        except UnsafePath as error:
            raise HTTPException(404, "No such file.") from error
        if root == "results":
            return _open_result(sessions.paths(conversation_id).oracle_results, path)
        checkpoints, files = saved_files(conversation_id, root)
        entry = files.get(path)
        if entry is None:
            raise HTTPException(404, "No such file (files appear here after each turn).")
        return checkpoints.open_object(entry)

    @router.get("/files")
    def list_folder(conversation_id: str, root: Root = "outputs") -> list[FileOut]:
        """Files as of the end of the last turn (or query results, as they are)."""
        conversation_or_404(conversation_id)
        if root == "results":
            listed = [
                (rel, info.st_size, info.st_mtime)
                for rel, info in list_files(sessions.paths(conversation_id).oracle_results)
            ]
        else:
            _, files = saved_files(conversation_id, root)
            listed = [(rel, e.size, e.modified) for rel, e in files.items()]
        return [
            FileOut(
                path=rel,
                size=size,
                modified=datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds"),
                kind=kind_of(rel),
            )
            for rel, size, modified in sorted(listed)
        ]

    @router.get("/files/{root}/{path:path}")
    def file_content(
        conversation_id: str,
        root: Root,
        path: str,
        head: int = Query(default=_DEFAULT_HEAD, ge=1, le=16 * 1024**2),
    ) -> Response:
        """A file's content for the in-app viewer: images, or the start of a text file."""
        conversation_or_404(conversation_id)
        kind = kind_of(path)
        if kind not in ("image", "csv", "text", "html"):
            raise HTTPException(415, "DataLab can't preview this kind of file.")
        fd = open_file(conversation_id, root, path)
        with os.fdopen(fd, "rb") as source:
            if kind == "image":
                data = source.read(_MAX_IMAGE_BYTES + 1)
                if len(data) > _MAX_IMAGE_BYTES:
                    raise HTTPException(413, "This image is too large to preview.")
                media_type = _IMAGES[os.path.splitext(path)[1].lower()]
                truncated = False
            else:
                data = source.read(head + 1)
                truncated = len(data) > head
                data = data[:head]
                media_type = "text/plain; charset=utf-8"
        return Response(
            data,
            media_type=media_type,
            headers={
                "content-security-policy": _FILE_POLICY,
                "cache-control": "no-store",
                "x-datalab-truncated": "1" if truncated else "0",
            },
        )

    @router.post("/previews")
    def new_preview(conversation_id: str, body: NewPreview) -> PreviewOut:
        conversation_or_404(conversation_id)
        if body.root == "results" or kind_of(body.path) != "html":
            raise HTTPException(422, "Only HTML outputs can be previewed.")
        checkpoints, files = saved_files(conversation_id, body.root)
        if body.path not in files:
            raise HTTPException(404, "No such file (files appear here after each turn).")
        token = previews.grant(_Grant(files, checkpoints, prefix(body.root)))
        return PreviewOut(url=f"/preview/{token}/{quote(body.path)}")

    @router.get("/checkpoints")
    def list_checkpoints(conversation_id: str) -> list[CheckpointOut]:
        conversation_or_404(conversation_id)
        return [
            CheckpointOut(
                number=c.number,
                created_at=c.created_at,
                label=c.label,
                turn=c.turn,
                files=c.files,
                bytes=c.bytes,
                skipped=[SkippedOut(**s.__dict__) for s in c.skipped],
            )
            for c in reversed(sessions.checkpoints(conversation_id).list())
        ]

    @router.post("/checkpoints/{number}/restore")
    async def restore(conversation_id: str, number: int) -> RestoreOut:
        conversation = conversation_or_404(conversation_id)
        try:
            result = await sessions.restore(conversation, number)
        except Busy as error:
            raise HTTPException(409, str(error)) from error
        except CheckpointMissing as error:
            raise HTTPException(404, "No such checkpoint.") from error
        except DockerError as error:
            raise HTTPException(503, str(error)) from error
        return RestoreOut(
            written=result.written,
            removed=result.removed,
            left_alone=result.left_alone,
            not_restored=result.not_restored,
        )

    return router


def build_preview_router(previews: Previews) -> APIRouter:
    """Serves previewed HTML and the files it uses. No cookie needed: the link is the key."""
    router = APIRouter(include_in_schema=False)

    @router.get("/preview/{token}/{path:path}")
    def preview(token: str, path: str, request: Request) -> Response:
        grant = previews.get(token)
        suffix = os.path.splitext(path)[1].lower()
        entry = grant.entries.get(path) if grant else None
        if grant is None or entry is None or suffix not in _PREVIEW_TYPES:
            raise HTTPException(404)
        # Only as part of the viewer's frame: not opened as a page of its own,
        # where the frame's protections wouldn't apply.
        destination = request.headers.get("sec-fetch-dest")
        page = suffix in (".html", ".htm")
        allowed = {"iframe"} if page else {"image", "style", "font"}
        if destination is not None and destination not in allowed:
            raise HTTPException(404)
        if entry.size > _MAX_PREVIEW_BYTES:
            raise HTTPException(413, "This file is too large to preview.")
        with os.fdopen(grant.checkpoints.open_object(entry), "rb") as source:
            data = source.read()
        if page:
            data = clean_html(data.decode("utf-8", "replace")).encode()
        origin = f"{request.url.scheme}://{request.url.netloc}"
        folder = f"{origin}/preview/{token}/"
        policy = "; ".join(
            [
                # Sandboxed with no allowances: no scripts, forms, popups, or
                # navigation, even if it's opened in a tab of its own.
                "sandbox",
                "default-src 'none'",
                f"img-src {folder} data:",
                f"style-src {folder} 'unsafe-inline'",
                f"font-src {folder} data:",
                f"media-src {folder} data:",
                "form-action 'none'",
                "base-uri 'none'",
                f"frame-ancestors {origin}",
            ]
        )
        return Response(
            data,
            media_type=_PREVIEW_TYPES[suffix],
            # No Cross-Origin-Resource-Policy: the sandboxed page has an opaque
            # origin, so to the browser its own images are cross-origin.
            # (X-DNS-Prefetch-Control: off is added to every response in web.py.)
            headers={"content-security-policy": policy, "cache-control": "no-store"},
        )

    return router


def _open_result(folder: Path, path: str) -> int:
    try:
        return open_workspace_file(folder, path)
    except UnsafePath as error:
        raise HTTPException(404, "No such file.") from error
