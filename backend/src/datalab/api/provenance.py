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
from datalab.sessions.provenance import Version, file_chain, record_turn, turns_from_events
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
    names_file: bool
    via_script: str | None


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
        versions = [
            Version(c.number, c.turn, c.label, checkpoints.entries(c.number))
            for c in checkpoints.list()
        ]

        def read(entry: Any, limit: int) -> bytes:
            with os.fdopen(checkpoints.open_object(entry), "rb") as source:
                return source.read(limit)

        chain = file_chain(
            path,
            versions,
            turns_from_events(store.all_events_after(conversation_id, 0)),
            access_log.for_session(conversation_id),
            read,
        )
        return FileProvenanceOut(**chain)

    return router
