"""Settings (milestone 7 and on): what the Settings screen shows and changes.

Only the status endpoint exists so far. The settings themselves are read
once at start from `settings.toml` (config.py); update metadata is
migration 0009.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings


@dataclass(frozen=True)
class SettingsServices:
    """What the Settings routes use, given by the app (app.py)."""

    settings: Settings  # `settings.updates`, and the rest to show
    database: sqlite3.Connection  # for update metadata (migration 0009)


class SettingsStatus(BaseModel):
    available: bool


def build_settings_router(services: SettingsServices) -> APIRouter:
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("/status")
    def status() -> SettingsStatus:
        return SettingsStatus(available=False)

    return router
