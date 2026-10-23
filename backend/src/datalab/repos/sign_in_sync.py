"""The lab's shared DataLab: sync the knowledge base and pipelines repos when
someone signs in, with their GitHub sign-in (a server has no installer, and
no one else's sign-in to clone with), in the background.

The first sign-in clones them; after that it's at most every few minutes,
so many sign-ins don't each ask GitHub. A failure is the repos' own state
(Settings → Connections), and never stops the sign-in.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from datalab.auth.acting import act_as, stop_acting
from datalab.auth.users import User

log = logging.getLogger(__name__)

AGAIN_SECONDS = 10 * 60


class Syncable(Protocol):
    @property
    def available(self) -> bool: ...

    def sync(self) -> Any: ...


class RepoSyncOnSignIn:
    def __init__(
        self,
        *repos: Syncable,
        clock: Callable[[], float] = time.monotonic,
        start: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._repos = repos
        self._clock = clock
        self._start = start or _thread
        self._lock = threading.Lock()
        self._last: float | None = None
        self._running = False

    def __call__(self, user: User) -> None:
        with self._lock:
            now = self._clock()
            recent = self._last is not None and now - self._last < AGAIN_SECONDS
            if self._running or recent:
                return
            self._running = True
            self._last = now
        self._start(lambda: self._sync_as(user))

    def _sync_as(self, user: User) -> None:
        token = act_as(user)
        try:
            for repo in self._repos:
                if not repo.available:
                    continue
                try:
                    repo.sync()
                except Exception:
                    log.exception("syncing a lab repository after %s signed in", user.login)
        finally:
            stop_acting(token)
            with self._lock:
                self._running = False


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="repo-sync-on-sign-in", daemon=True).start()
