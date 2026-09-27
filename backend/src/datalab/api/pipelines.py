"""Pipelines (milestone 6): browsing and changing `ihsDataR` in the pipelines repo.

Only the status endpoint exists so far. Data engineering conversations get
what they read through a mount provider (`SessionManager.register_mounts`),
always read-only; the agent works on its own copy in `/work`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore


@dataclass(frozen=True)
class PipelineServices:
    """What the Pipelines routes use, given by the app (app.py)."""

    settings: Settings  # `settings.repos`
    conversations: ConversationStore
    sessions: SessionManager  # mounts and after-turn hooks


class PipelinesStatus(BaseModel):
    available: bool


def build_pipelines_router(services: PipelineServices) -> APIRouter:
    router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

    @router.get("/status")
    def status() -> PipelinesStatus:
        return PipelinesStatus(available=False)

    return router
