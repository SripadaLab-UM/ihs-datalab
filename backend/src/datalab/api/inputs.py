"""Attaching files and folders to a conversation (read-only inputs)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from datalab.config import Settings, default_data_dir
from datalab.data.access_log import AccessLog
from datalab.sessions import picker
from datalab.sessions.containers import DockerError
from datalab.sessions.inputs import (
    Attachment,
    AttachmentStore,
    InputKind,
    NotAttachable,
    check_attachable,
    credential_files_in,
    practice_samples,
)
from datalab.sessions.manager import Busy, SessionManager
from datalab.sessions.modes import MODES
from datalab.sessions.store import ConversationStore
from datalab.sessions.uploads import (
    MAX_CONVERSATION_BYTES,
    MAX_FILE_BYTES,
    UploadKind,
    UploadRefused,
    Uploads,
    UploadTooLarge,
)


class AttachmentOut(BaseModel):
    id: str
    name: str
    container_path: str
    host_path: str
    kind: Literal["file", "folder"]
    available: bool


class NewInputs(BaseModel):
    # "files" and "folder" open the computer's picker; "sample" is practice only.
    source: Literal["files", "folder", "sample"]
    sample: str | None = None


class NewUpload(BaseModel):
    kind: UploadKind


class UploadOut(BaseModel):
    id: str
    kind: UploadKind
    # The most one file, and all of a conversation's uploads, may be.
    max_file_bytes: int
    room_bytes: int


class UploadedFile(BaseModel):
    path: str
    bytes: int


class Refused(BaseModel):
    path: str
    reason: str


class AttachResult(BaseModel):
    added: list[AttachmentOut]
    refused: list[Refused]
    # What the person should know about what they attached (credential files
    # inside a folder, for example).
    warnings: list[str] = []


def out(attachment: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        name=attachment.name,
        container_path=attachment.container_path,
        host_path=attachment.host_path,
        kind=attachment.kind,
        available=attachment.available,
    )


def build_inputs_router(
    settings: Settings,
    store: ConversationStore,
    attachments: AttachmentStore,
    sessions: SessionManager,
    audit: AccessLog | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["inputs"])
    practice = settings.profile == "practice"
    uploads = Uploads(lambda conversation_id: sessions.paths(conversation_id).root)
    protected = [settings.data_dir, default_data_dir("real"), default_data_dir("practice")]

    def conversation_or_404(conversation_id: str):
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPException(404, "No such conversation.")
        return conversation

    @router.get("/input-samples")
    def samples() -> list[str]:
        """Synthetic files the practice profile can attach (empty elsewhere)."""
        if not practice:
            return []
        return sorted(p.name for p in practice_samples().iterdir() if not p.name.startswith("."))

    @router.get("/conversations/{conversation_id}/inputs")
    def list_inputs(conversation_id: str) -> list[AttachmentOut]:
        conversation_or_404(conversation_id)
        return [out(a) for a in attachments.list(conversation_id)]

    @router.post("/conversations/{conversation_id}/inputs")
    async def attach(conversation_id: str, body: NewInputs) -> AttachResult:
        conversation = conversation_or_404(conversation_id)
        mode = MODES.get(conversation.mode)
        if mode is None or not mode.attachments:
            raise HTTPException(
                403,
                f"Files can't be attached in {mode.label if mode else 'this'} mode. "
                "Attach them in a Workspace conversation instead.",
            )
        if sessions.is_busy(conversation_id):
            raise HTTPException(409, "Wait for the agent to finish, or stop it, then attach.")
        refused: list[Refused] = []
        chosen: list[tuple[Path, InputKind]] = []
        if body.source != "sample" and settings.on_server:
            raise HTTPException(
                409, "On the lab's DataLab, upload files from your computer instead."
            )
        if body.source == "sample":
            if not practice:
                raise HTTPException(403, "Samples are only for practice DataLab.")
            samples = practice_samples()
            found = [p for p in samples.iterdir() if p.name == body.sample]
            if not found:
                raise HTTPException(404, "No such sample.")
            real = Path(os.path.realpath(found[0]))
            if not str(real).startswith(os.path.realpath(samples) + os.sep):
                raise HTTPException(404, "No such sample.")
            if conversation.compute == "high":
                # Its containers run on another machine: the sample goes
                # there with the conversation's own files.
                real = await asyncio.to_thread(uploads.copy_in, conversation_id, real)
            chosen.append((real, "folder" if real.is_dir() else "file"))
        else:
            if practice:
                raise HTTPException(
                    403,
                    "Practice DataLab can't attach files from this computer, so nothing real "
                    "can reach it. Attach one of the samples instead.",
                )
            try:
                picked = await picker.pick(body.source)
            except picker.PickerBusy as error:
                raise HTTPException(409, str(error)) from error
            except picker.PickerUnavailable as error:
                raise HTTPException(501, str(error)) from error
            for path in picked:
                try:
                    chosen.append(check_attachable(path, protected=protected))
                except NotAttachable as error:
                    refused.append(Refused(path=str(path), reason=str(error)))

        added: list[Attachment] = []
        if chosen:
            try:
                async with sessions.changing_inputs(conversation):
                    added = [attachments.add(conversation_id, real, kind) for real, kind in chosen]
            except Busy as error:
                raise HTTPException(409, str(error)) from error
            except DockerError as error:
                raise HTTPException(503, str(error)) from error
            store.append(
                conversation_id,
                "input_attached",
                {"items": [{"path": a.container_path, "kind": a.kind} for a in added]},
            )
        warnings = await asyncio.to_thread(_folder_warnings, added)
        return AttachResult(added=[out(a) for a in added], refused=refused, warnings=warnings)

    def may_upload(conversation_id: str):
        conversation = conversation_or_404(conversation_id)
        if not settings.on_server:
            raise HTTPException(
                403, "Uploading is for the lab's DataLab; here, attach files from this computer."
            )
        if practice:
            raise HTTPException(
                403,
                "Practice DataLab can't take your files, so nothing real can reach it. "
                "Attach one of the samples instead.",
            )
        mode = MODES.get(conversation.mode)
        if mode is None or not mode.attachments:
            label = mode.label if mode else "this"
            raise HTTPException(403, f"Files can't be attached in {label} mode.")
        return conversation

    @router.post("/conversations/{conversation_id}/uploads", status_code=201)
    def start_upload(conversation_id: str, body: NewUpload) -> UploadOut:
        """An upload to send files into, one request each (`PUT .../files`)."""
        may_upload(conversation_id)
        upload = uploads.start(conversation_id, body.kind)
        return UploadOut(
            id=upload.id,
            kind=upload.kind,
            max_file_bytes=MAX_FILE_BYTES,
            room_bytes=max(0, MAX_CONVERSATION_BYTES - uploads.used(conversation_id)),
        )

    @router.put(
        "/conversations/{conversation_id}/uploads/{upload_id}/files",
        responses={413: {"description": "Too large"}, 422: {"description": "Refused"}},
    )
    async def upload_file(
        conversation_id: str,
        upload_id: str,
        request: Request,
        # The file's path inside the upload, percent-encoded (a header is
        # Latin-1). A custom header also means no other site can send this
        # without the browser asking DataLab first, which it refuses.
        x_datalab_path: Annotated[str, Header()],
    ) -> UploadedFile:
        """One file of an upload: the request's body, as it is."""
        may_upload(conversation_id)
        path = unquote(x_datalab_path)
        try:
            size = await uploads.write(conversation_id, upload_id, path, request.stream())
        except UploadTooLarge as error:
            raise HTTPException(413, str(error)) from None
        except UploadRefused as error:
            raise HTTPException(422, str(error)) from None
        return UploadedFile(path=path, bytes=size)

    @router.post("/conversations/{conversation_id}/uploads/{upload_id}/attach")
    async def attach_upload(conversation_id: str, upload_id: str) -> AttachResult:
        """Attach what an upload holds: each file, or its folder."""
        conversation = may_upload(conversation_id)
        if sessions.is_busy(conversation_id):
            raise HTTPException(409, "Wait for the agent to finish, or stop it, then attach.")
        upload = uploads.get(conversation_id, upload_id)
        if upload is None:
            raise HTTPException(404, "No such upload.")
        try:
            items = uploads.finish(conversation_id, upload_id)
        except UploadRefused as error:
            raise HTTPException(422, str(error)) from None
        try:
            async with sessions.changing_inputs(conversation):
                added = [
                    attachments.add(conversation_id, item, "folder" if item.is_dir() else "file")
                    for item in items
                ]
        except Busy as error:
            raise HTTPException(409, str(error)) from error
        except DockerError as error:
            raise HTTPException(503, str(error)) from error
        if audit is not None:
            audit.record_transfer(
                "upload", session_id=conversation_id, files=upload.files, size=upload.bytes
            )
        store.append(
            conversation_id,
            "input_attached",
            {"items": [{"path": a.container_path, "kind": a.kind} for a in added]},
        )
        warnings = await asyncio.to_thread(_folder_warnings, added)
        return AttachResult(added=[out(a) for a in added], refused=[], warnings=warnings)

    @router.delete("/conversations/{conversation_id}/inputs/{attachment_id}", status_code=204)
    async def detach(conversation_id: str, attachment_id: str) -> None:
        conversation = conversation_or_404(conversation_id)
        if not any(a.id == attachment_id for a in attachments.list(conversation_id)):
            raise HTTPException(404, "No such input.")
        try:
            # The container is confirmed gone first, so a removed item can't
            # stay mounted.
            async with sessions.changing_inputs(conversation):
                removed = attachments.remove(conversation_id, attachment_id)
        except Busy as error:
            raise HTTPException(409, str(error)) from error
        except DockerError as error:
            raise HTTPException(503, str(error)) from error
        if removed is not None:
            await asyncio.to_thread(uploads.forget, conversation_id, removed)
            store.append(
                conversation_id,
                "input_removed",
                {"items": [{"path": removed.container_path, "kind": removed.kind}]},
            )

    return router


def _folder_warnings(added: list[Attachment]) -> list[str]:
    warnings = []
    for attachment in added:
        if attachment.kind != "folder":
            continue
        found, complete = credential_files_in(Path(attachment.host_path))
        if found:
            shown = ", ".join(found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
            warnings.append(
                f"{attachment.name} contains what look like credentials files ({shown}). "
                "The agent can read everything in an attached folder. Remove it and attach "
                "just the files it needs if that's not what you want."
            )
        elif not complete:
            warnings.append(
                f"{attachment.name} is large, so DataLab couldn't check all of it for "
                "credentials files. The agent can read everything in it."
            )
    return warnings
