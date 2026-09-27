"""Knowledge (milestone 5): the lab knowledge base, its proposed edits, and Save & share.

Only the status endpoint exists so far. Each session gets an editable copy of
the knowledge base at `/work/kb`; after each turn an after-turn hook
(`SessionManager.register_after_turn`) diffs the copy in the turn's
checkpoint against the version it was copied from, and proposes the edits
(see docs/KNOWLEDGE_BASE.md, "How edits happen"). Its tables are migration
0007.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore


@dataclass(frozen=True)
class KnowledgeServices:
    """What the Knowledge routes use, given by the app (app.py)."""

    settings: Settings  # `settings.repos`; the clone lives under `settings.data_dir`
    database: sqlite3.Connection  # for this area's own tables (migration 0007)
    conversations: ConversationStore  # proposed-edit cards are conversation events
    sessions: SessionManager  # after-turn hooks and read-only mounts


class KnowledgeStatus(BaseModel):
    available: bool
    # The local copy of the knowledge-base repo. Milestone 5 adds its other
    # states (cloning, in sync, behind, sync failed).
    repo: Literal["not configured"]


def build_knowledge_router(services: KnowledgeServices) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

    @router.get("/status")
    def status() -> KnowledgeStatus:
        return KnowledgeStatus(available=False, repo="not configured")

    return router
