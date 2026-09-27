"""SQL Playground (milestone 4): run SQL by hand, through the data service.

Only the status endpoint exists so far. Playground queries go through
`DataService.run_query` with `origin="playground"` and a `pg_…` owner id, so
they get the same SQL check, limits and logging as the agent's, and list
with `AccessLog.for_origin("playground", …)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.data.catalog import Catalog
from datalab.data.service import DataService


@dataclass(frozen=True)
class SqlServices:
    """What the Playground's routes use, given by the app (app.py)."""

    settings: Settings  # `settings.playground`; results go under `settings.data_dir`
    data: DataService
    catalog: Catalog  # the table browser
    access_log: AccessLog  # the Playground's query history


class SqlStatus(BaseModel):
    available: bool


def build_sql_router(services: SqlServices) -> APIRouter:
    router = APIRouter(prefix="/api/sql", tags=["sql"])

    @router.get("/status")
    def status() -> SqlStatus:
        return SqlStatus(available=False)

    return router
