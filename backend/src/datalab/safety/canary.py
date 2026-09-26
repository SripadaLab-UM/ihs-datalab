"""Canaries: prove a request never arrived, instead of guessing from a reply.

A blocked request and one that arrived and was refused (401, 404) can look
alike from the outside. So probes aim at one-off canary addresses on DataLab
itself, `/__canary/<nonce>`. DataLab records every canary it receives, and a
recorded nonce means that route leads here and isn't sealed.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response


class Canaries:
    def __init__(self) -> None:
        self._issued: dict[str, str] = {}
        self._hits: set[str] = set()

    def new(self, label: str) -> str:
        """A fresh canary path, e.g. /__canary/3f9a…, remembered under `label`."""
        nonce = secrets.token_hex(12)
        self._issued[nonce] = label
        return f"/__canary/{nonce}"

    def record(self, nonce: str) -> None:
        if nonce in self._issued:
            self._hits.add(nonce)

    def hits(self, paths: list[str]) -> list[str]:
        """The labels of the given canaries that were reached."""
        nonces = [p.rsplit("/", 1)[-1] for p in paths]
        return [self._issued[n] for n in nonces if n in self._hits]

    def router(self) -> APIRouter:
        router = APIRouter(include_in_schema=False)

        @router.api_route("/__canary/{nonce}", methods=["GET", "POST", "HEAD", "PUT"])
        async def canary(nonce: str, request: Request) -> Response:
            self.record(nonce)
            return Response(status_code=204)

        return router
