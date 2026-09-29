"""Docker on Windows, for the page (windows_vm.py, docs/DISTRIBUTION.md).

- `GET /api/docker` says how Docker stands ("ready", "not-installed",
  "stopped", "starting", "vm-refused", "unknown"; "unsupported" off
  Windows), whether an administrator prompt is showing, and where to turn on
  temporary administrator access (`[windows] admin_access_url`). It answers
  from the last check: a new one runs at most every 15 seconds, whoever asks
  (a GET can come from another page on this computer), and WSL's virtual
  machine is only tried while Docker Desktop is open but not answering.
- `POST /api/docker/check` checks again now (the page's Check again), at most
  every few seconds.
- `POST /api/docker/start` opens Docker Desktop if it's closed.
- `POST /api/docker/fix` shows Windows' administrator prompt and puts back
  the right Docker's virtual machine needs, then restarts Docker Desktop (its
  own processes ended, its WSL distribution stopped, opened again) and waits
  up to 4 minutes for it; only when the last check found the VM refused, and
  never while something in DataLab is working, since the restart would stop
  it. It answers once that's done, saying which step failed, if one did.
  Nothing is ever done without the person pressing the button: a policy
  decides when the right goes, but only the person decides when an
  administrator prompt appears.

Like every /api route, these need the sign-in cookie, and each POST must be
JSON from DataLab's own page (web.py ApiProtection; test_docker_api.py pins it).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel

from datalab.config import Settings
from datalab.windows_vm import DockerDoctor, DockerState, FixOutcome, RestartStep


class DockerStatusOut(BaseModel):
    state: DockerState
    # An administrator prompt from DataLab is showing now.
    fixing: bool
    admin_access_url: str | None


class DockerFixOut(BaseModel):
    outcome: FixOutcome
    state: DockerState
    # With "restart-failed": which step of restarting Docker Desktop didn't work.
    failed_step: RestartStep | None = None


def build_docker_router(
    settings: Settings, doctor: DockerDoctor, *, working: Callable[[], bool] = lambda: False
) -> APIRouter:
    """`working`: whether something in DataLab is going (a conversation's turn,
    a query, a workflow run, an export). Fix it restarts Docker Desktop, which
    would stop it, so the fix waits for it."""
    router = APIRouter(prefix="/api/docker", tags=["docker"])

    def status(state: DockerState) -> DockerStatusOut:
        return DockerStatusOut(
            state=state,
            fixing=doctor.fixing,
            admin_access_url=settings.windows.admin_access_url,
        )

    @router.get("")
    async def get_status() -> DockerStatusOut:
        return status(await asyncio.to_thread(doctor.state))

    @router.post("/check")
    async def check() -> DockerStatusOut:
        return status(await asyncio.to_thread(doctor.check))

    @router.post("/start")
    async def start() -> DockerStatusOut:
        return status(await asyncio.to_thread(doctor.start))

    @router.post("/fix")
    async def fix() -> DockerFixOut:
        if working():
            return DockerFixOut(outcome="working", state=await asyncio.to_thread(doctor.state))
        result = await asyncio.to_thread(doctor.fix)
        return DockerFixOut(
            outcome=result.outcome,
            state=await asyncio.to_thread(doctor.state),
            failed_step=result.failed_step,
        )

    return router
