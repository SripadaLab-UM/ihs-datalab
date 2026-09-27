"""/api refuses JSON whose strings aren't text, before any route reads it.

JSON can carry a lone UTF-16 surrogate (`"\\ud800"`), and Python keeps it in
a `str`. Pydantic lets it into a plain `str` field, and `str.encode()` then
raises wherever the text is written, hashed or measured, so a request that
should be refused became a 500. Where Pydantic does refuse it (a field with
a length limit), FastAPI's 422 echoes the input back, and writing that
response as UTF-8 failed the same way. So each JSON body is read once here,
and one with a lone surrogate anywhere, in a key or a value, is refused
with a plain 422 that doesn't repeat it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from datalab.textcheck import lone_surrogate


class RefuseNonText:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or not _json(scope):
            await self._app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        problem = _non_text(body)
        if problem is not None:
            await JSONResponse({"detail": problem}, status_code=422)(scope, receive, send)
            return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)


def _json(scope: Scope) -> bool:
    """A body FastAPI would read as JSON: no content type, or a JSON one."""
    types = [v for k, v in scope.get("headers", []) if k.lower() == b"content-type"]
    if not types:
        return scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
    media = types[0].split(b";")[0].strip().lower()
    return media == b"application/json" or media.endswith(b"+json")


def _non_text(body: bytes) -> str | None:
    """What to say about the first string in the JSON that isn't text, or
    None (also for a body that isn't JSON: FastAPI says what's wrong)."""
    if not body:
        return None
    try:
        # From bytes, json reads surrogates written raw as well as escaped.
        stack: list[Any] = [json.loads(body)]
    except (ValueError, RecursionError):
        return None
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if (not_text := lone_surrogate(value)) is not None:
                return f"The request has {not_text.what}. Remove it."
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None
