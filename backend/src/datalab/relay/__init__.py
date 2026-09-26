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
from datalab.relay.policy import Refused, check_responses_request, model_allowed, parse_request
from datalab.sessions.tokens import SessionTokens, bearer_token

log = logging.getLogger(__name__)

# Response headers passed back to Codex. Everything else (cookies, upstream
# infrastructure headers) stays here.
_PASS_BACK = ("content-type", "x-request-id", "openai-processing-ms")


def build_relay_router(
    tokens: SessionTokens,
    api_key: Callable[[], str],
    base_url: str,
    client: httpx.AsyncClient,
    allowed_models: tuple[str, ...] | None = None,
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
        access = tokens.resolve(bearer_token(request.headers.get("authorization")))
        if access is None:
            return _refused(401, "unknown session")
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
        return await _forward(client, "POST", f"{base_url}/responses", checked, key)

    @router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def everything_else(path: str) -> Response:
        return _refused(403, f"/{path} isn't available")

    return router


async def _forward(
    client: httpx.AsyncClient, method: str, url: str, body: bytes | None, key: str
) -> Response:
    headers = {
        "authorization": f"Bearer {key}",
        "accept-encoding": "identity",
    }
    if body is not None:
        headers["content-type"] = "application/json"
    try:
        upstream = await client.send(
            client.build_request(method, url, content=body, headers=headers), stream=True
        )
    except httpx.HTTPError as error:
        return _refused(502, f"couldn't reach U-M GPT ({type(error).__name__})")
    passed = {k: v for k, v in upstream.headers.items() if k.lower() in _PASS_BACK}
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=passed,
        background=BackgroundTask(upstream.aclose),
    )


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
