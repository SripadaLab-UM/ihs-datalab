"""Docker on Windows, for the page (windows_vm.py, docs/DISTRIBUTION.md).

- `GET /api/docker` says how Docker stands ("ready", "vm-refused",
  "starting", "stopped", "unknown"; "unsupported" off Windows), whether an
  administrator prompt is showing, and where to turn on temporary
  administrator access (`[windows] admin_access_url`). `?fresh=true` checks
  again now instead of using the last few seconds' answer.
- `POST /api/docker/start` opens Docker Desktop if it's closed.
- `POST /api/docker/fix` shows Windows' administrator prompt and puts back
  the right Docker's virtual machine needs, then restarts Docker Desktop. It
  answers once the prompt has been answered. Nothing is ever done without the
  person pressing the button: a policy decides when the right goes, but only
  the person decides when an administrator prompt appears.

Like every /api route, these need the sign-in cookie, and each POST must be
JSON from DataLab's own page (web.py ApiProtection).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.windows_vm import DockerDoctor, DockerState, FixOutcome


class DockerStatusOut(BaseModel):
    state: DockerState
    # An administrator prompt from DataLab is showing now.
    fixing: bool
    admin_access_url: str | None


class DockerFixOut(BaseModel):
    outcome: FixOutcome
    state: DockerState


def build_docker_router(settings: Settings, doctor: DockerDoctor) -> APIRouter:
    router = APIRouter(prefix="/api/docker", tags=["docker"])

    def status(state: DockerState) -> DockerStatusOut:
        return DockerStatusOut(
            state=state,
            fixing=doctor.fixing,
            admin_access_url=settings.windows.admin_access_url,
        )

    @router.get("")
    async def get_status(fresh: bool = False) -> DockerStatusOut:
        return status(await asyncio.to_thread(doctor.state, fresh=fresh))

    @router.post("/start")
    async def start() -> DockerStatusOut:
        return status(await asyncio.to_thread(doctor.start))

    @router.post("/fix")
    async def fix() -> DockerFixOut:
        outcome = await asyncio.to_thread(doctor.fix)
        return DockerFixOut(
            outcome=outcome, state=await asyncio.to_thread(doctor.state, fresh=True)
        )

    return router
