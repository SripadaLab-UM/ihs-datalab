"""Export destinations, and exporting a conversation's files and report.

Only the person exports, to a folder they chose. The practice profile has a
single disposable folder instead, so nothing from it can end up somewhere
real.

Settings → Export folders uses these routes (see export_folders.py for the
checks and the words). A folder inside a sync app's folder is still a folder
on this computer: every response says "Saved to <name> (on this computer)",
and for a Dropbox folder adds who will upload it. Nothing says it synced.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from datalab import export_folders, exports
from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.export_folders import Location, Provider, Status
from datalab.exports import Destination, DestinationStore, ExportError, ExportSource
from datalab.sessions import picker
from datalab.sessions.checkpoints import UnsafePath, check_relative, open_workspace_file
from datalab.sessions.inputs import AttachmentStore, NotAttachable
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.titles import scrub_title

PRACTICE_DESTINATION = export_folders.PRACTICE_ID
PRACTICE_WHY = (
    "Available on the real DataLab. Practice DataLab saves only to its own practice folder, "
    "so nothing from it can end up in a real Dropbox or shared folder."
)


class DestinationOut(BaseModel):
    """An export folder, and how it stands right now.

    The Workflows Deliver stage and every export chooser offer the folders
    with `available` true, by `name`.
    """

    id: str
    name: str  # the person's friendly name for it
    path: str
    where: str  # the path with the home folder as ~
    # Offered and ready now: what export and delivery choosers may pick.
    available: bool
    status: Status  # ready, missing, not_a_folder, not_writable, online_only, refused
    status_message: str | None  # why it isn't ready, in plain words
    location: Location  # this_computer, sync_folder, external_drive
    sync_provider: Provider | None  # dropbox, onedrive, box, google_drive, icloud
    sync_provider_name: str | None  # "Dropbox"
    location_note: str  # "Inside your Dropbox folder: Dropbox will upload it when …"
    offered: bool  # the person's switch: offered for exports and workflows
    workflow_keys: list[str]  # the workflow destination key mapped to it, if any
    practice: bool = False  # practice DataLab's own folder (can't be changed)


class PlaceOut(BaseModel):
    """A sync app's folder found on this computer, by name: where the picker can open."""

    id: str
    provider: Provider
    provider_name: str
    label: str  # "Dropbox (UniversityofMichigan)"
    where: str


class PlacesOut(BaseModel):
    can_add: bool
    why_not: str | None  # practice: "Available on the real DataLab…"
    places: list[PlaceOut]


class AddDestination(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    # A place from /export-destinations/places: the picker opens there.
    start_in: str | None = Field(default=None, max_length=300)


class ChangeDestination(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    offered: bool | None = None


class FolderTestOut(BaseModel):
    ok: bool
    status: Status
    message: str | None  # why it failed
    test_file: str | None  # the synthetic file's name
    removed: bool  # the test file is gone again
    saved_to: str | None  # "Saved to Lab Dropbox (on this computer)", when ok
    sync_note: str | None  # "Dropbox will upload it when …", when ok and in a sync folder
    destination: DestinationOut


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
    """A finished export. Show `saved_to`, then `sync_note` when there is one."""

    folder: str  # the dated folder made for it
    files: list[str]
    destination_id: str
    destination_name: str
    saved_to: str  # "Saved to Lab Dropbox (on this computer)"
    sync_provider: Provider | None
    sync_note: str | None  # "Dropbox will upload them when its app is running and signed in. …"


def destination_out(settings: Settings, destination: Destination) -> DestinationOut:
    path = Path(destination.path)
    checked = export_folders.check_folder(
        path, protected=export_folders.protected_folders(settings)
    )
    info = export_folders.describe(path)
    return DestinationOut(
        id=destination.id,
        name=destination.name,
        path=destination.path,
        where=export_folders.display_path(path),
        available=destination.offered and checked.ready,
        status=checked.status,
        status_message=checked.message,
        location=info.location,
        sync_provider=info.sync_provider,
        sync_provider_name=export_folders.PROVIDER_NAMES.get(info.sync_provider or ""),
        location_note=info.note,
        offered=destination.offered,
        workflow_keys=[destination.key] if destination.key else [],
    )


def practice_destination_out(settings: Settings) -> DestinationOut:
    folder = settings.data_dir / "practice-exports"
    return DestinationOut(
        id=PRACTICE_DESTINATION,
        name=export_folders.PRACTICE_NAME,
        path=str(folder),
        where=export_folders.display_path(folder),
        available=True,
        status="ready",
        status_message=None,
        location="this_computer",
        sync_provider=None,
        sync_provider_name=None,
        location_note=(
            "Practice DataLab's own folder, inside its data folder. Nothing here is real."
        ),
        offered=True,
        workflow_keys=[],
        practice=True,
    )


def export_out(target: export_folders.Target, result: exports.ExportResult) -> ExportOut:
    return ExportOut(
        folder=str(result.folder),
        files=result.files,
        destination_id=target.id or PRACTICE_DESTINATION,
        destination_name=target.name,
        saved_to=export_folders.saved_to(target.name),
        sync_provider=target.sync_provider,
        sync_note=export_folders.sync_note(target.sync_provider, files=len(result.files)),
    )


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
    protected = export_folders.protected_folders(settings)

    def find(destination_id: str) -> Destination:
        destination = None if practice else destinations.get(destination_id)
        if destination is None:
            raise HTTPException(404, "No such export folder.")
        return destination

    def name_or_422(name: str, *, other_than: str | None = None) -> str:
        try:
            name = export_folders.check_name(name)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if any(
            d.name.casefold() == name.casefold() and d.id != other_than for d in destinations.list()
        ):
            raise HTTPException(409, f"Another export folder is already called {name}.")
        return name

    @router.get("/export-destinations")
    def list_destinations() -> list[DestinationOut]:
        if practice:
            return [practice_destination_out(settings)]
        return [destination_out(settings, d) for d in destinations.list()]

    @router.get("/export-destinations/places")
    def places() -> PlacesOut:
        """Sync apps' folders found on this computer, by name, for the picker to open in."""
        if practice:
            return PlacesOut(can_add=False, why_not=PRACTICE_WHY, places=[])
        return PlacesOut(
            can_add=True,
            why_not=None,
            places=[
                PlaceOut(
                    id=root.id,
                    provider=root.provider,
                    provider_name=export_folders.PROVIDER_NAMES[root.provider],
                    label=root.label,
                    where=root.where,
                )
                for root in export_folders.sync_roots()
            ],
        )

    @router.post("/export-destinations", status_code=201)
    async def add_destination(body: AddDestination | None = None) -> DestinationOut:
        """Choose a folder in the computer's picker and add it as a destination.

        The browser never names the path: it can only say which found sync
        folder the picker opens in, and give the folder a name.
        """
        if practice:
            raise HTTPException(403, PRACTICE_WHY)
        body = body or AddDestination()
        name = name_or_422(body.name) if body.name and body.name.strip() else None
        start_in = None
        if body.start_in:
            root = next((r for r in export_folders.sync_roots() if r.id == body.start_in), None)
            if root is None:
                raise HTTPException(404, "That folder isn't on this computer any more.")
            start_in = root.path
        try:
            chosen = await picker.pick("folder", start_in=start_in)
        except picker.PickerBusy as error:
            raise HTTPException(409, str(error)) from error
        except picker.PickerUnavailable as error:
            raise HTTPException(501, str(error)) from error
        if not chosen:
            raise HTTPException(400, "No folder was chosen.")
        try:
            # The same places are off limits as for attaching: nothing is
            # exported into system folders, app data, or DataLab's own data.
            real = export_folders.check_new_folder(chosen[0], protected=protected)
        except NotAttachable as error:
            raise HTTPException(422, str(error)) from error
        if any(Path(d.path) == real for d in destinations.list()):
            raise HTTPException(409, "That folder is already an export folder.")
        if name is None:
            name = _free_name(real.name or str(real), destinations)
        added = destinations.add(name, real)
        return destination_out(settings, added)

    @router.patch("/export-destinations/{destination_id}")
    def change_destination(destination_id: str, body: ChangeDestination) -> DestinationOut:
        """Rename a folder, or turn it on or off as a destination."""
        destination = find(destination_id)
        if body.name is not None:
            destinations.rename(destination.id, name_or_422(body.name, other_than=destination.id))
        if body.offered is not None:
            destinations.set_offered(destination.id, body.offered)
        return destination_out(settings, find(destination_id))

    @router.post("/export-destinations/{destination_id}/test")
    async def test_destination(destination_id: str) -> FolderTestOut:
        """Save a small synthetic file there, read it back, and remove it.

        That shows DataLab can save there on this computer. It says nothing
        about whether a sync app has uploaded anything.
        """
        if practice:
            if destination_id != PRACTICE_DESTINATION:
                raise HTTPException(404, "No such export folder.")
            target = export_folders.practice_target(settings)
            result = await asyncio.to_thread(
                export_folders.write_test_file, target.path, protected=protected, skip_checks=True
            )
            shown = practice_destination_out(settings)
        else:
            destination = find(destination_id)
            result = await asyncio.to_thread(
                export_folders.write_test_file, Path(destination.path), protected=protected
            )
            shown = destination_out(settings, destination)
        return FolderTestOut(
            ok=result.ok,
            status=result.status,
            message=result.message,
            test_file=result.test_file,
            removed=result.removed,
            saved_to=export_folders.saved_to(shown.name) if result.ok else None,
            sync_note=export_folders.sync_note(shown.sync_provider) if result.ok else None,
            destination=shown,
        )

    @router.delete("/export-destinations/{destination_id}", status_code=204)
    def remove_destination(destination_id: str) -> None:
        """Forget a destination. The folder, and files already exported there, are untouched."""
        if practice or not destinations.remove(destination_id):
            raise HTTPException(404, "No such export folder.")

    @router.post("/conversations/{conversation_id}/exports", status_code=201)
    async def export(conversation_id: str, body: NewExport) -> ExportOut:
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPException(404, "No such conversation.")
        if not body.files and body.report is None:
            raise HTTPException(422, "Choose something to export.")
        target = export_target(settings, destinations, body.destination_id)
        folder = target.path
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
        return export_out(target, result)

    return router


def _free_name(name: str, destinations: DestinationStore) -> str:
    """A default name no other folder has: "IHS", then "IHS (2)"."""
    name = export_folders.check_name(name[: export_folders.NAME_MAX - 5] or "Export folder")
    taken = {d.name.casefold() for d in destinations.list()}
    candidate, number = name, 2
    while candidate.casefold() in taken:
        candidate, number = f"{name} ({number})", number + 1
    return candidate


def export_target(
    settings: Settings, destinations: DestinationStore, destination_id: str
) -> export_folders.Target:
    """The folder an export goes to, checked again now. Raises HTTPException.

    Also used by the SQL Playground's exports, so every export goes through
    the same checks.
    """
    try:
        return export_folders.target(settings, destinations, destination_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ExportError as error:
        raise HTTPException(422, str(error)) from error


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
