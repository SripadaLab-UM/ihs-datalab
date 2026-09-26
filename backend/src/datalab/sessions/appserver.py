"""A client for `codex app-server`: JSON-RPC over the agent's stdin/stdout.

Codex writes one JSON object per line and omits the "jsonrpc" field. There
are three kinds of message:
- responses to our requests (they have "id" and "result" or "error");
- notifications (they have "method" and no "id"): streamed turn events;
- server requests (they have "method" and "id"): Codex asking us something,
  such as an approval, which we must answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Notification = Callable[[str, dict[str, Any]], Awaitable[None]]
ServerRequest = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AppServerError(RuntimeError):
    pass


class AppServerClient:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        on_notification: Notification,
        on_server_request: ServerRequest,
    ) -> None:
        self._process = process
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader = asyncio.create_task(self._read_messages())
        self._stderr = asyncio.create_task(self._drain_stderr())
        self._tasks: set[asyncio.Task[None]] = set()

    async def initialize(self) -> None:
        await self.request("initialize", {"clientInfo": {"name": "datalab", "version": "1"}})
        await self.notify("initialized")

    async def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 120
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    @property
    def alive(self) -> bool:
        return self._process.returncode is None and not self._reader.done()

    async def close(self) -> None:
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
            await self._process.wait()
        for task in (self._reader, self._stderr, *self._tasks):
            task.cancel()
        self._fail_pending(AppServerError("Codex stopped."))

    async def _send(self, message: dict[str, Any]) -> None:
        stdin = self._process.stdin
        if stdin is None or self._process.returncode is not None:
            raise AppServerError("Codex isn't running.")
        stdin.write((json.dumps(message) + "\n").encode())
        await stdin.drain()

    async def _read_messages(self) -> None:
        assert self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("ignoring a non-JSON line from codex app-server")
                    continue
                await self._dispatch(message)
        finally:
            self._fail_pending(AppServerError("Codex stopped unexpectedly."))

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    future.set_exception(AppServerError(error.get("message", str(error))))
                else:
                    future.set_result(message.get("result") or {})
            return
        if "id" in message:
            # Answer server requests in their own task: an approval may wait
            # minutes for the user, and other messages must keep flowing.
            task = asyncio.create_task(self._answer(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        await self._on_notification(message["method"], message.get("params") or {})

    async def _answer(self, message: dict[str, Any]) -> None:
        try:
            result = await self._on_server_request(message["method"], message.get("params") or {})
            await self._send({"id": message["id"], "result": result})
        except Exception as error:  # a failed answer must not stop the session
            log.exception("failed to answer %s", message["method"])
            with contextlib.suppress(AppServerError):
                await self._send(
                    {"id": message["id"], "error": {"code": -32603, "message": str(error)}}
                )

    async def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        while line := await self._process.stderr.readline():
            log.debug("codex: %s", line.decode(errors="replace").rstrip())

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
