"""Provenance (milestone 8, brought forward): where each number in an answer
appears, and how each workspace file was made.

- After every turn, a hook (`SessionManager.register_after_turn`) records a
  `provenance` event for the turn's answer: each number with where it
  appears, and the output files it names (sessions/provenance.py).
- "How was this made?" for a file is computed when asked, from what DataLab
  already keeps: the checkpoints, the event log, and the Data accessed log.

Nothing here shows query results or command output: a query is named by its
Data accessed entry, a command by its text, and a script by its path and
checkpoint (the files API serves its content, as the person already sees it).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.data.access_log import AccessLog
from datalab.sessions.checkpoints import UnsafePath, check_relative
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.provenance import Turn, Version, file_chain, one_turn, record_turn
from datalab.sessions.store import ConversationStore


@dataclass(frozen=True)
class ProvenanceServices:
    """What provenance uses, given by the app (app.py)."""

    conversations: ConversationStore  # the event log: answers, commands, provenance events
    sessions: SessionManager  # checkpoints, the turn's sources, and the after-turn hook
    access_log: AccessLog  # the Data accessed log


class ChainCommandOut(BaseModel):
    id: str
    command: str
    exit_code: int | None
    names_file: bool  # the command's own text names the file
    via_script: str | None  # a script it ran that names the file
    seen_in_output: bool  # its output mentions the file (the output isn't shown)


class ChainScriptOut(BaseModel):
    path: str
    sha256: str
    names_file: bool


class ChainReaderOut(BaseModel):
    kind: Literal["script", "command"]
    ref: str


class ChainQueryOut(BaseModel):
    id: str
    started_at: str
    tables: list[str]
    row_count: int | None
    result_file: str | None
    read_by: list[ChainReaderOut]
    in_turn: bool


class FileProvenanceOut(BaseModel):
    path: str
    found: bool
    summary: str
    checkpoint: int | None = None
    turn: int | None = None
    in_review: bool = False  # first saved after the turn's rigor review, not the turn
    # ...and the turn's own checkpoint wasn't saved, so the turn's commands are listed too.
    turn_not_saved: bool = False
    commands: list[ChainCommandOut] = []
    more_commands: int = 0
    edited_directly: bool = False
    scripts: list[ChainScriptOut] = []
    queries: list[ChainQueryOut] = []
    more_queries: int = 0


def build_provenance_router(services: ProvenanceServices) -> APIRouter:
    store, sessions, access_log = services.conversations, services.sessions, services.access_log

    async def after_turn(conversation_id: str, info: TurnInfo) -> None:
        """Record where the turn's answer's numbers appear (a review run again has no answer)."""
        if info.review_only:
            return
        await asyncio.to_thread(
            record_turn,
            store,
            conversation_id,
            info.since,
            sessions.turn_sources(conversation_id),
            sessions.checkpoints(conversation_id),
            info.checkpoint,
        )

    sessions.register_after_turn(after_turn)
    router = APIRouter(prefix="/api/conversations/{conversation_id}", tags=["provenance"])

    @router.get("/provenance/{path:path}")
    def file_provenance(conversation_id: str, path: str) -> FileProvenanceOut:
        """How a workspace file (a path in /work, such as outputs/fig1.png) was made."""
        if store.get(conversation_id) is None:
            raise HTTPException(404, "No such conversation.")
        try:
            check_relative(path)
        except UnsafePath as error:
            raise HTTPException(404, "No such file.") from error
        checkpoints = sessions.checkpoints(conversation_id)
        # The summaries are small; a checkpoint's files are read only when needed.
        versions = [Version(c.number, c.turn, c.label, c.review) for c in checkpoints.list()]
        loaded: dict[int, dict[str, Any]] = {}

        def entries(number: int) -> dict[str, Any]:
            if number not in loaded:
                loaded[number] = checkpoints.entries(number)
            return loaded[number]

        def read(entry: Any, limit: int) -> bytes:
            with os.fdopen(checkpoints.open_object(entry), "rb") as source:
                return source.read(limit)

        chain = file_chain(
            path,
            versions,
            entries,
            lambda number: _turn(store, conversation_id, number),
            access_log.for_session(conversation_id),
            read,
        )
        return FileProvenanceOut(**chain)

    return router


def _turn(store: ConversationStore, conversation_id: str, number: int) -> Turn | None:
    """Turn `number`, parsed from its own events only (not the whole conversation's)."""
    starts = store.events_of_types_after(conversation_id, 0, ("user_message",))
    if not 0 < number <= len(starts):
        return None
    start, following = starts[number - 1], starts[number] if number < len(starts) else None
    events = []
    seq = start.seq - 1
    while batch := store.events_after(conversation_id, seq):
        for event in batch:
            if following is not None and event.seq >= following.seq:
                return one_turn(events, number, following.created_at)
            events.append(event)
        seq = batch[-1].seq
    return one_turn(events, number)
