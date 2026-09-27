"""Export destinations, and exporting a conversation's files and report.

Only the person exports, to a folder they chose. The practice profile has a
single disposable folder instead, so nothing from it can end up somewhere
real.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from datalab import exports
from datalab.config import Settings, default_data_dir
from datalab.data.access_log import AccessLog
from datalab.exports import DestinationStore, ExportError, ExportSource
from datalab.sessions import picker
from datalab.sessions.checkpoints import UnsafePath, check_relative, open_workspace_file
from datalab.sessions.inputs import AttachmentStore, NotAttachable, check_attachable
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.titles import scrub_title

PRACTICE_DESTINATION = "practice"


class DestinationOut(BaseModel):
    id: str
    name: str
    path: str
    available: bool


class FileRef(BaseModel):
    root: Literal["outputs", "work", "results"]
    path: str


class ReportIn(BaseModel):
    # The report body, rendered by the web UI from the conversation. DataLab
    # cleans it and wraps it in a page that can't load or send anything.
    html: str = Field(max_length=50 * 1024**2)
    css: str = Field(default="", max_length=200_000)


class NewExport(BaseModel):
    destination_id: str
    files: list[FileRef] = Field(default_factory=list, max_length=5000)
    report: ReportIn | None = None
    # The checkpoint whose files the person was shown, so nothing newer slips in.
    checkpoint: int | None = None
    # Export agent-made web pages as they are, scripts and all, instead of
    # inert copies. Their scripts could contact the internet when opened.
    raw_html: bool = False


class ExportOut(BaseModel):
    folder: str
    files: list[str]


def build_exports_router(
    settings: Settings,
    store: ConversationStore,
    destinations: DestinationStore,
    sessions: SessionManager,
    attachments: AttachmentStore,
    audit: AccessLog,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["exports"])
    practice = settings.profile == "practice"
    practice_folder = settings.data_dir / "practice-exports"
    protected = [settings.data_dir, default_data_dir("real"), default_data_dir("practice")]

    def destination_folder(destination_id: str) -> Path:
        return export_folder(settings, destinations, destination_id)

    @router.get("/export-destinations")
    def list_destinations() -> list[DestinationOut]:
        if practice:
            return [
                DestinationOut(
                    id=PRACTICE_DESTINATION,
                    name="Practice exports",
                    path=str(practice_folder),
                    available=True,
                )
            ]
        return [
            DestinationOut(id=d.id, name=d.name, path=d.path, available=d.available)
            for d in destinations.list()
        ]

    @router.post("/export-destinations", status_code=201)
    async def add_destination() -> DestinationOut:
        """Choose a folder in the computer's picker and add it as a destination."""
        if practice:
            raise HTTPException(403, "Practice DataLab exports only to its own practice folder.")
        try:
            chosen = await picker.pick("folder")
        except picker.PickerBusy as error:
            raise HTTPException(409, str(error)) from error
        except picker.PickerUnavailable as error:
            raise HTTPException(501, str(error)) from error
        if not chosen:
            raise HTTPException(400, "No folder was chosen.")
        try:
            # The same places are off limits as for attaching: nothing is
            # exported into system folders, app data, or DataLab's own data.
            real, _ = check_attachable(chosen[0], protected=protected)
        except NotAttachable as error:
            raise HTTPException(422, str(error)) from error
        if not real.is_dir():
            raise HTTPException(422, "Choose a folder.")
        if any(Path(d.path) == real for d in destinations.list()):
            raise HTTPException(409, "That folder is already an export destination.")
        added = destinations.add(real.name or str(real), real)
        return DestinationOut(id=added.id, name=added.name, path=added.path, available=True)

    @router.delete("/export-destinations/{destination_id}", status_code=204)
    def remove_destination(destination_id: str) -> None:
        """Forget a destination. Files already exported there are untouched."""
        if practice or not destinations.remove(destination_id):
            raise HTTPException(404, "No such export folder.")

    @router.post("/conversations/{conversation_id}/exports", status_code=201)
    async def export(conversation_id: str, body: NewExport) -> ExportOut:
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPException(404, "No such conversation.")
        if not body.files and body.report is None:
            raise HTTPException(422, "Choose something to export.")
        folder = destination_folder(body.destination_id)
        sources = _sources(sessions, conversation_id, body.files, body.checkpoint, body.raw_html)
        study_data = not practice and (
            conversation.kind == "data" or bool(attachments.list(conversation_id))
        )
        # Titles are scrubbed when written, but one typed by the person, or
        # stored before titles were scrubbed, could still name a participant.
        # What's outside the files (the folder name, the manifest, the
        # report's page title) carries only the scrubbed title.
        title = scrub_title(conversation.title) or "Conversation"
        about = {
            "profile": settings.profile,
            "contains_study_data": study_data,
            "conversation": {
                "id": conversation.id,
                "title": title,
                "kind": conversation.kind,
                "mode": conversation.mode,
                "model": conversation.model,
            },
            # Which saved version of the workspace the files are from (the
            # History panel's numbering).
            "checkpoint": body.checkpoint,
        }
        report = body.report

        def run() -> exports.ExportResult:
            extra = {}
            if report is not None:
                extra["conversation.html"] = exports.report_document(title, report.html, report.css)
            return exports.export(
                folder,
                title=title,
                tag=conversation.id,
                sources=sources,
                extra_files=extra,
                about=about,
            )

        try:
            result = await asyncio.to_thread(run)
        except (ExportError, UnsafePath, OSError) as error:
            raise HTTPException(422, f"The export didn't finish: {error}") from error
        audit.record_export(
            session_id=conversation_id,
            destination=str(folder),
            files=len(result.files),
            contains_study_data=study_data,
        )
        store.append(
            conversation_id,
            "exported",
            {"folder": str(result.folder), "files": len(result.files)},
        )
        return ExportOut(folder=str(result.folder), files=result.files)

    return router


def export_folder(settings: Settings, destinations: DestinationStore, destination_id: str) -> Path:
    """The folder an export goes to, checked again now. Raises HTTPException.

    Also used by the SQL Playground's exports, so every export goes through
    the same checks.
    """
    if settings.profile == "practice":
        if destination_id != PRACTICE_DESTINATION:
            raise HTTPException(404, "No such export folder.")
        folder = settings.data_dir / "practice-exports"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    destination = destinations.get(destination_id)
    if destination is None:
        raise HTTPException(404, "No such export folder.")
    folder = Path(destination.path)
    # Checked again now: the folder may have been replaced by a link since.
    if os.path.realpath(folder) != str(folder):
        raise HTTPException(422, "That export folder now points somewhere else. Add it again.")
    protected = [settings.data_dir, default_data_dir("real"), default_data_dir("practice")]
    try:
        check_attachable(folder, protected=protected)
    except NotAttachable as error:
        raise HTTPException(422, f"DataLab can't export there any more: {error}") from error
    return folder


def _sources(
    sessions: SessionManager,
    conversation_id: str,
    files: list[FileRef],
    checkpoint: int | None,
    raw_html: bool,
):
    """What to copy: workspace files from the checkpoint shown, results as they are."""
    checkpoints = sessions.checkpoints(conversation_id)
    if checkpoint is None and any(f.root != "results" for f in files):
        # The person chose files as a listing showed them: exactly those.
        raise HTTPException(422, "Say which checkpoint's files to export.")
    chosen = checkpoints.get(checkpoint) if checkpoint is not None else None
    if checkpoint is not None and chosen is None:
        raise HTTPException(404, "No such checkpoint.")
    saved = checkpoints.entries(chosen.number) if chosen else {}
    results = sessions.paths(conversation_id).oracle_results
    sources: list[ExportSource] = []
    used: set[str] = set()
    for ref in files:
        try:
            check_relative(ref.path)
        except UnsafePath as error:
            raise HTTPException(422, f"Can't export {ref.path!r}.") from error
        rel = ref.path
        if ref.root == "results":
            if not (results / ref.path).is_file():
                raise HTTPException(404, f"No such query result: {ref.path}")
            target = f"query-results/{ref.path}"
            opener = _result_opener(results, ref.path)
            container_path = f"/data/oracle/{ref.path}"
        else:
            rel = f"outputs/{ref.path}" if ref.root == "outputs" else ref.path
            entry = saved.get(rel)
            if entry is None:
                raise HTTPException(404, f"No such file: {ref.path}")
            target = f"outputs/{ref.path}" if ref.root == "outputs" else f"workspace/{ref.path}"
            opener = _saved_opener(checkpoints, entry)
            container_path = f"/work/{rel}"
        key = target.casefold()
        if key in used or any(u.startswith(key + "/") or key.startswith(u + "/") for u in used):
            raise HTTPException(422, f"Two of the chosen files would have the same name: {target}")
        used.add(key)
        sibling = None
        if ref.root != "results" and ref.path.lower().endswith((".html", ".htm")):
            sibling = _sibling_reader(checkpoints, saved, rel)
        sources.append(
            ExportSource(opener, target, container_path, raw_html=raw_html, sibling=sibling)
        )
    return sources


def _sibling_reader(checkpoints, saved, page: str):
    """Read files next to a web page, by relative URL, from the same checkpoint."""
    folder = page.rsplit("/", 1)[0] if "/" in page else ""

    def read(url: str) -> bytes | None:
        relative = unquote(url.split("#")[0].split("?")[0])
        parts = [p for p in f"{folder}/{relative}".split("/") if p not in ("", ".")]
        resolved: list[str] = []
        for part in parts:
            if part == "..":
                if not resolved:
                    return None  # outside the workspace
                resolved.pop()
            else:
                resolved.append(part)
        entry = saved.get("/".join(resolved))
        if entry is None:
            return None
        with os.fdopen(checkpoints.open_object(entry), "rb") as reader:
            return reader.read()

    return read


def _saved_opener(checkpoints, entry):
    return lambda: checkpoints.open_object(entry)


def _result_opener(folder: Path, path: str):
    return lambda: open_workspace_file(folder, path)
