"""The Safety check, for the Settings & Safety screen."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.safety import SafetyCheck


class CheckResultOut(BaseModel):
    id: str
    promise: str
    label: str
    status: Literal["pass", "fail", "skip"]
    detail: str


class SafetyReportOut(BaseModel):
    started_at: str
    finished_at: str
    passed: bool
    results: list[CheckResultOut]


def build_safety_router(check: SafetyCheck, last_report_file: Path) -> APIRouter:
    router = APIRouter(prefix="/api/safety", tags=["safety"])
    running = asyncio.Lock()

    @router.post("/check")
    async def run_check() -> SafetyReportOut:
        if running.locked():
            raise HTTPException(409, "A safety check is already running.")
        async with running:
            report = SafetyReportOut(**(await check.run()).to_dict())
        last_report_file.parent.mkdir(parents=True, exist_ok=True)
        last_report_file.write_text(report.model_dump_json(indent=1))
        return report

    @router.get("/last")
    def last_check() -> SafetyReportOut | None:
        if not last_report_file.exists():
            return None
        return SafetyReportOut(**json.loads(last_report_file.read_text()))

    return router
