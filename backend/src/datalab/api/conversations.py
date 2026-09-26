"""Conversations: create, list, send, stop, delete, and follow their events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from datalab.relay.policy import model_allowed
from datalab.sessions.approvals import Unshowable
from datalab.sessions.manager import Busy, SessionManager
from datalab.sessions.modes import MODES
from datalab.sessions.plans import PlanInvalid
from datalab.sessions.store import Conversation, ConversationStore

_HEARTBEAT_SECONDS = 15


class NewConversation(BaseModel):
    mode: str = "analysis"
    title: str = Field(default="New conversation", max_length=200)
    model: str | None = None


class ConversationOut(BaseModel):
    id: str
    kind: Literal["data", "research"]
    mode: str
    title: str
    model: str
    created_at: str
    updated_at: str
    rigor_review: bool
    busy: bool


class ConversationChange(BaseModel):
    rigor_review: bool | None = None


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    effort: Literal["low", "medium", "high", "xhigh"] | None = None


class ApprovalAnswer(BaseModel):
    approve: bool
    # The question as the person left it: they may edit it before sending.
    question: str = Field(default="", max_length=4000)  # checked again, more strictly
    # Or, for an analysis plan, its parts as the person left them.
    plan: dict[str, str] | None = None


class EventOut(BaseModel):
    seq: int
    created_at: str
    type: str
    data: dict[str, Any]


class QueryRecordOut(BaseModel):
    id: str
    started_at: str
    status: str
    sql_text: str
    tables: list[str]
    row_count: int | None
    elapsed_ms: int | None
    result_file: str | None
    message: str | None


class ModeOut(BaseModel):
    id: str
    label: str
    kind: Literal["data", "research"]
    description: str
    starters: list[str]


class ModelsOut(BaseModel):
    default: str
    # Approved models U-M GPT offers now (empty if it couldn't be asked).
    available: list[str]


def build_conversations_router(
    store: ConversationStore,
    sessions: SessionManager,
    default_model: str,
    access_log,
    *,
    models: Callable[[], Awaitable[list[str]]],
    allowed_models: tuple[str, ...] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["conversations"])

    def out(conversation: Conversation) -> ConversationOut:
        return ConversationOut(**conversation.__dict__, busy=sessions.is_busy(conversation.id))

    def get_or_404(conversation_id: str) -> Conversation:
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPException(404, "No such conversation.")
        return conversation

    @router.get("/modes")
    def list_modes() -> list[ModeOut]:
        return [
            ModeOut(
                id=m.id,
                label=m.label,
                kind=m.kind,
                description=m.description,
                starters=list(m.starters),
            )
            for m in MODES.values()
        ]

    @router.get("/models")
    async def list_models() -> ModelsOut:
        """The approved models, for the picker. Asked of U-M GPT with DataLab's own key."""
        return ModelsOut(default=default_model, available=await models())

    @router.get("/conversations")
    def list_conversations() -> list[ConversationOut]:
        return [out(c) for c in store.list()]

    @router.post("/conversations", status_code=201)
    def create_conversation(body: NewConversation) -> ConversationOut:
        mode = MODES.get(body.mode)
        if mode is None:
            raise HTTPException(422, f"Unknown mode {body.mode!r}.")
        model = body.model or default_model
        if not model_allowed(model, allowed_models):
            raise HTTPException(422, f"{model!r} isn't approved for DataLab.")
        # The rigor review is on by default in Analysis mode.
        conversation = store.create(
            kind=mode.kind,
            mode=mode.id,
            title=body.title,
            model=model,
            rigor_review=mode.id == "analysis",
        )
        return out(conversation)

    @router.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> ConversationOut:
        return out(get_or_404(conversation_id))

    @router.patch("/conversations/{conversation_id}")
    def change_conversation(conversation_id: str, body: ConversationChange) -> ConversationOut:
        get_or_404(conversation_id)
        if body.rigor_review is not None:
            store.set_rigor_review(conversation_id, body.rigor_review)
        return out(get_or_404(conversation_id))

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> None:
        get_or_404(conversation_id)
        await sessions.delete(conversation_id)

    @router.post("/conversations/{conversation_id}/messages", status_code=202)
    async def send_message(conversation_id: str, body: NewMessage) -> ConversationOut:
        conversation = get_or_404(conversation_id)
        if not model_allowed(conversation.model, allowed_models):
            raise HTTPException(
                409,
                f"This conversation uses {conversation.model}, which isn't approved for DataLab "
                "any more. Start a new conversation to continue.",
            )
        try:
            await sessions.send(conversation, body.text, body.effort)
        except Busy as error:
            raise HTTPException(409, str(error)) from error
        return out(conversation)

    @router.post("/conversations/{conversation_id}/stop", status_code=202)
    async def stop(conversation_id: str) -> ConversationOut:
        conversation = get_or_404(conversation_id)
        await sessions.stop(conversation_id)
        return out(conversation)

    @router.post("/conversations/{conversation_id}/approvals/{approval_id}", status_code=204)
    async def answer_approval(conversation_id: str, approval_id: str, body: ApprovalAnswer) -> None:
        """Approve (maybe edited) or decline a research-helper question."""
        get_or_404(conversation_id)
        try:
            sessions.answer_approval(
                conversation_id, approval_id, body.approve, body.question, body.plan
            )
        except KeyError as error:
            raise HTTPException(
                409, "This request isn't waiting for an answer any more."
            ) from error
        except (Unshowable, PlanInvalid) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/conversations/{conversation_id}/events")
    def list_events(conversation_id: str, after: int = 0) -> list[EventOut]:
        get_or_404(conversation_id)
        return [EventOut(**e.__dict__) for e in store.events_after(conversation_id, after)]

    @router.get("/conversations/{conversation_id}/stream")
    async def stream(
        conversation_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        """Server-sent events: everything after `after`, then live events."""
        get_or_404(conversation_id)
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

        async def events() -> AsyncIterator[str]:
            seq = start
            while not await request.is_disconnected():
                batch = store.events_after(conversation_id, seq)
                for event in batch:
                    seq = event.seq
                    payload = json.dumps(
                        {"seq": event.seq, "created_at": event.created_at, "data": event.data}
                    )
                    yield f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"
                if not batch:
                    yield ": keep-alive\n\n"
                    await store.wait_for_events(conversation_id, seq, _HEARTBEAT_SECONDS)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.get("/conversations/{conversation_id}/data-accessed")
    def data_accessed(conversation_id: str) -> list[QueryRecordOut]:
        get_or_404(conversation_id)
        return [
            QueryRecordOut(
                id=r.id,
                started_at=r.started_at,
                status=r.status,
                sql_text=r.sql_text,
                tables=r.tables,
                row_count=r.row_count,
                elapsed_ms=r.elapsed_ms,
                result_file=r.result_path.rsplit("/", 1)[-1] if r.result_path else None,
                message=r.message,
            )
            for r in access_log.for_session(conversation_id)
        ]

    return router
