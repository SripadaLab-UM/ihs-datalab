"""The research helper's container is always cleaned up, even when cancelled."""

import asyncio
import contextlib
from typing import ClassVar

from datalab.sessions import helper as module
from datalab.sessions.approvals import Approvals
from datalab.sessions.tokens import SessionTokens


class SlowContainers:
    made: ClassVar[list["SlowContainers"]] = []

    def __init__(self, session_id, kind, paths, **_):
        self.kind = kind
        self.paths = paths
        self.agent = f"{session_id}-agent"
        self.removed = False
        SlowContainers.made.append(self)

    async def start(self, token):
        await asyncio.sleep(30)  # cancelled while starting

    async def remove(self):
        await asyncio.sleep(0.05)  # an await that a cancelled task would skip
        self.removed = True


async def test_a_cancelled_helper_still_removes_its_containers(settings, monkeypatch):
    monkeypatch.setattr(module, "SessionContainers", SlowContainers)
    tokens = SessionTokens()
    research = module.ResearchHelper(settings, tokens, Approvals(), lambda *a: None)
    run = asyncio.create_task(research._run("a question"))
    await asyncio.sleep(0.1)
    [containers] = SlowContainers.made
    assert containers.kind == "research"
    assert "helpers" in str(containers.paths.root) and "sessions" not in str(containers.paths.root)
    run.cancel()
    await asyncio.sleep(0.01)
    run.cancel()  # Stop pressed twice, or Stop then close
    with contextlib.suppress(asyncio.CancelledError):
        await run
    assert containers.removed
    assert not containers.paths.root.exists()
    assert tokens._by_token == {}  # its token is revoked
