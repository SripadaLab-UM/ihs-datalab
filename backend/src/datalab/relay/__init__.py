"""The model relay: every model request from a session goes through here.

Codex in a container calls `http://gateway/v1/...` with its session token as
the API key. The gateway forwards to `/relay/v1/...` here, and the relay:
1. looks the token up (which session is this, data or research?);
2. checks the request against the policy (see policy.py);
3. swaps in the real U-M GPT key from the keychain and streams the response
   straight back.

The real key never leaves this process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from datalab.credentials import MissingCredential
from datalab.relay import recovery
from datalab.relay.policy import Refused, check_responses_request, model_allowed, parse_request
from datalab.sessions.tokens import SessionTokens, bearer_token

log = logging.getLogger(__name__)

# Response headers passed back to Codex. Everything else (cookies, upstream
# infrastructure headers) stays here.
_PASS_BACK = (
    "content-type",
    "x-request-id",
    "openai-processing-ms",
    "retry-after",
    "retry-after-ms",
)

# Tells a session how its model requests are going (session ID, event data):
# the chat shows "busy, retrying in 20 s" instead of a raw error.
StatusCallback = Callable[[str, dict], None]
# For a request arriving now from a session (by ID): a check that is true once
# that request's turn is over (Stopped, or the conversation shut down). Then
# nothing more is sent, even if Codex's connection is still open (it may stay
# open through the gateway).
TurnWatch = Callable[[str], Callable[[], bool]]


def build_relay_router(
    tokens: SessionTokens,
    api_key: Callable[[], str],
    base_url: str,
    client: httpx.AsyncClient,
    allowed_models: tuple[str, ...] | None = None,
    on_status: StatusCallback | None = None,
    watch_turn: TurnWatch | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/relay/v1", include_in_schema=False)
    base_url = base_url.rstrip("/")

    @router.get("/models")
    async def models(request: Request) -> Response:
        if tokens.resolve(bearer_token(request.headers.get("authorization"))) is None:
            return _refused(401, "unknown session")
        key = await asyncio.to_thread(_key_or_none, api_key)
        if key is None:
            return _refused(503, "no U-M GPT key is saved in DataLab")
        # Only the approved models are listed, so Codex never offers others.
        try:
            upstream = await client.get(
                f"{base_url}/models", headers={"authorization": f"Bearer {key}"}
            )
            listing = upstream.json()
        except (httpx.HTTPError, ValueError) as error:
            return _refused(502, f"couldn't reach U-M GPT ({type(error).__name__})")
        entries = listing.get("data") if isinstance(listing, dict) else None
        approved = [
            m
            for m in (entries if isinstance(entries, list) else [])
            if isinstance(m, dict) and model_allowed(m.get("id"), allowed_models)
        ]
        return JSONResponse({"object": "list", "data": approved}, status_code=upstream.status_code)

    @router.post("/responses")
    async def responses(request: Request) -> Response:
        token = bearer_token(request.headers.get("authorization"))
        access = tokens.resolve(token)
        if access is None:
            return _refused(401, "unknown session")
        # Taken as the request arrives, so it belongs to the turn current now.
        turn_over = watch_turn(access.session_id) if watch_turn is not None else None
        raw = await request.body()
        try:
            body = parse_request(raw)
            check_responses_request(body, access.kind, allowed_models)
        except json.JSONDecodeError:
            return _refused(400, "the request body isn't JSON")
        except Refused as refusal:
            log.warning("relay refused a request from session %s: %s", access.session_id, refusal)
            return _refused(403, str(refusal))
        key = await asyncio.to_thread(_key_or_none, api_key)
        if key is None:
            return _refused(503, "no U-M GPT key is saved in DataLab")
        # Exactly what was checked goes upstream, not the original bytes.
        checked = json.dumps(body, ensure_ascii=False).encode()

        def status(data: dict) -> None:
            if on_status is not None:
                on_status(access.session_id, {**data, "model": body.get("model")})

        def gone() -> bool:
            # A revoked token is the kill switch: the session ended, was
            # deleted, or its helper was cancelled.
            return tokens.resolve(token) is None or (turn_over is not None and turn_over())

        return await _forward(
            client,
            f"{base_url}/responses",
            checked,
            key,
            request=request,
            status=status,
            gone=gone,
        )

    @router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def everything_else(path: str) -> Response:
        return _refused(403, f"/{path} isn't available")

    return router


async def _forward(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    key: str,
    *,
    request: Request,
    status: Callable[[dict], None],
    gone: Callable[[], bool] = lambda: False,
) -> Response:
    """Send the request upstream and stream the answer back.

    A failure before any of the answer has streamed is retried here, within
    recovery's bounds, honouring the server's wait. The same checked bytes are
    sent each time. Nothing is sent once the request is `gone` (checked before
    every attempt, the first too) or Codex has stopped waiting.
    """
    headers = {
        "authorization": f"Bearer {key}",
        "accept-encoding": "identity",
        "content-type": "application/json",
    }
    waited = 0.0
    attempt = 0
    trouble: recovery.Trouble | None = None
    failure: Response = _refused(409, "this request's turn is over")
    while True:
        if gone():
            return failure  # the last failure, if there was one: not reported as failed
        attempt += 1
        try:
            upstream = await client.send(
                client.build_request("POST", url, content=body, headers=headers), stream=True
            )
        except httpx.HTTPError as error:
            trouble = recovery.connection_trouble(error)
            failure = _refused(502, f"couldn't reach U-M GPT ({type(error).__name__})")
        else:
            if upstream.status_code < 400:
                if trouble is not None:
                    status({"state": "recovered", "attempt": attempt})
                passed = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_BACK}
                return StreamingResponse(
                    upstream.aiter_bytes(),
                    status_code=upstream.status_code,
                    headers=passed,
                    background=BackgroundTask(upstream.aclose),
                )
            content = await _read_bounded(upstream)
            trouble = recovery.classify(upstream.status_code, upstream.headers, content)
            passed = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_BACK}
            failure = Response(content, status_code=upstream.status_code, headers=passed)
        log.warning("model request failed (attempt %d): %s", attempt, trouble.record())
        delay = recovery.next_delay(trouble, attempt, waited)
        if delay is None:
            status({"state": "failed", "attempt": attempt, **trouble.record()})
            return failure
        wait = round(delay)
        status({"state": "retrying", "attempt": attempt, "wait_seconds": wait, **trouble.record()})
        if not await _wait_unless_gone(request, delay, gone):
            return failure  # stopped: nothing to report as failed
        waited += delay


async def _read_bounded(upstream: httpx.Response, limit: int = 65_536) -> bytes:
    content = b""
    try:
        async for chunk in upstream.aiter_bytes():
            content += chunk
            if len(content) >= limit:
                break
    finally:
        await upstream.aclose()
    return content[:limit]


async def _wait_unless_gone(
    request: Request, seconds: float, stopped: Callable[[], bool] = lambda: False
) -> bool:
    """Wait, checking every second that Codex is still waiting and the person
    hasn't pressed Stop. False if either: then nothing is sent again."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while (left := deadline - loop.time()) > 0:
        if stopped() or await request.is_disconnected():
            return False
        await asyncio.sleep(min(1.0, left))
    return not (stopped() or await request.is_disconnected())


def _key_or_none(api_key: Callable[[], str]) -> str | None:
    try:
        return api_key()
    except MissingCredential:
        return None


def _refused(status: int, reason: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": f"DataLab refused this request: {reason}.",
                "type": "datalab_refused",
            }
        },
        status_code=status,
    )
