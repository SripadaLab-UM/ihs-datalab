"""Workflows (milestone 6): workflow files, runs, and run records.

Only the status endpoint exists so far. A run's SQL steps go through
`DataService.run_query` with `origin="run"` and the run's `run_…` id, so they
share the data service's checks and appear in the audit log attributed to
the run. Run records are migration 0008.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.data.service import DataService


@dataclass(frozen=True)
class WorkflowServices:
    """What the Workflows routes use, given by the app (app.py)."""

    settings: Settings  # `settings.workflows`, `settings.repos`; runs go under `data_dir`
    database: sqlite3.Connection  # for run records (migration 0008)
    data: DataService  # SQL steps
    access_log: AccessLog  # a run's queries: `for_origin("run", run_id)`


class WorkflowsStatus(BaseModel):
    available: bool


def build_workflows_router(services: WorkflowServices) -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflows"])

    @router.get("/status")
    def status() -> WorkflowsStatus:
        return WorkflowsStatus(available=False)

    return router
