"""Attaching files and folders to a conversation (read-only inputs)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.config import Settings, default_data_dir
from datalab.sessions import picker
from datalab.sessions.containers import DockerError
from datalab.sessions.inputs import (
    Attachment,
    AttachmentStore,
    InputKind,
    NotAttachable,
    check_attachable,
    practice_samples,
)
from datalab.sessions.manager import Busy, SessionManager
from datalab.sessions.store import ConversationStore


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


class Refused(BaseModel):
    path: str
    reason: str


class AttachResult(BaseModel):
    added: list[AttachmentOut]
    refused: list[Refused]


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
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["inputs"])
    practice = settings.profile == "practice"
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
        if sessions.is_busy(conversation_id):
            raise HTTPException(409, "Wait for the agent to finish, or stop it, then attach.")
        refused: list[Refused] = []
        chosen: list[tuple[Path, InputKind]] = []
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
        return AttachResult(added=[out(a) for a in added], refused=refused)

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
            store.append(
                conversation_id,
                "input_removed",
                {"items": [{"path": removed.container_path, "kind": removed.kind}]},
            )

    return router
