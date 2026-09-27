"""While an update is being installed, nothing new may start.

The updater checks that nothing is working before it starts. Work that
begins after that check (a turn, a query, a workflow run, an export, a
pipeline test, a sync or push) would be recorded after the backup, or cut
off by the restart. So the updater closes this gate first, then checks
again: from then on every request that could start or change something
(anything but GET, HEAD and OPTIONS, apart from the update's own routes) is
refused with 409, and the ones already under way are counted, so the check
can wait for them.
"""

from __future__ import annotations

import threading

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
# The update's own routes: checking, and the install that closes the gate.
_EXEMPT = frozenset({"/api/settings/updates/check", "/api/settings/updates/install"})


class UpdateGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version: str | None = None
        self._in_flight = 0

    @property
    def closed_for(self) -> str | None:
        """The version being installed while the gate is closed, else None."""
        return self._version

    def in_flight(self) -> int:
        """Requests under way that could start or change something."""
        with self._lock:
            return self._in_flight

    def close(self, version: str) -> None:
        with self._lock:
            self._version = version

    def open(self) -> None:
        with self._lock:
            self._version = None

    def enter(self) -> bool:
        """Count a request in, unless the gate is closed (checked together, so
        none slips in between the updater's close and its check)."""
        with self._lock:
            if self._version is not None:
                return False
            self._in_flight += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self._in_flight -= 1


class UpdateGateMiddleware:
    def __init__(self, app: ASGIApp, gate: UpdateGate) -> None:
        self._app = app
        self._gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "GET") in _SAFE
            or scope["path"] in _EXEMPT
        ):
            await self._app(scope, receive, send)
            return
        if not self._gate.enter():
            version = self._gate.closed_for
            response = JSONResponse(
                {
                    "detail": f"DataLab is being updated to {version}. Nothing new can start "
                    "until it has restarted."
                },
                status_code=409,
            )
            await response(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            self._gate.leave()
