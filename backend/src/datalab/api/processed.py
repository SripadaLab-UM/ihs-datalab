"""The processed data (the lab's Dropbox folder), in Settings → Processed data.

- `GET /api/processed`: where it stands: practice's own folder, or Dropbox
  connected (or not) and the folder chosen (or not), with the file list's size.
- Connecting: `POST /connect` returns Dropbox's page where the person
  approves the app; Dropbox then shows a code, which `POST /connect/finish`
  exchanges. `POST /connect/cancel`, and `POST /disconnect` (which also
  forgets the folder, its file list and the downloaded files).
- `GET /folders` lists the folders at the top of the team space, or inside
  one (`?inside=<id>`), so a subfolder (a test folder in the person's own
  folder, say) can be chosen; `POST /folder` chooses one and lists its
  files; `POST /refresh` brings the list up to date.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from datalab.dropbox.auth import DropboxUnavailable, SignIn, SignInNeeded
from datalab.dropbox.client import DropboxRefused
from datalab.dropbox.service import Folder, NotAvailable, ProcessedData
from datalab.dropbox.sources import Progress

T = TypeVar("T")


class SignInOut(BaseModel):
    state: Literal["signed out", "waiting", "signed in", "failed"]
    authorize_url: str | None = None
    account: str | None = None
    message: str | None = None


class ListingOut(BaseModel):
    """A listing under way: Dropbox doesn't say how many files to expect, so
    this counts what's come and the chosen folder's top-level folders done."""

    files: int
    folders_done: int
    folders_total: int | None = None
    seconds: int


class ProcessedOut(BaseModel):
    source: Literal["dropbox", "practice", "off"]
    configured: bool
    ready: bool
    problem: str | None = None
    sign_in: SignInOut | None = None
    folder: str | None = None
    files: int = 0
    bytes: int = 0
    indexed_at: str | None = None
    listing: ListingOut | None = None
    listing_error: str | None = None


class FolderOut(BaseModel):
    id: str
    path: str
    name: str


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class ChooseIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)


def build_processed_router(processed: ProcessedData) -> APIRouter:
    router = APIRouter(prefix="/api/processed", tags=["processed"])

    async def run(work: Callable[[], T]) -> T:
        # Dropbox and the keychain: never on the event loop.
        try:
            return await asyncio.to_thread(work)
        except NotAvailable as error:
            raise HTTPException(409, str(error)) from None
        except SignInNeeded as error:
            # Not 401: that's the browser's own DataLab session.
            raise HTTPException(403, str(error)) from None
        except DropboxRefused as error:
            raise HTTPException(409, f"Dropbox refused: {error.summary}") from None
        except DropboxUnavailable as error:
            raise HTTPException(502, str(error)) from None

    def auth():
        if processed.auth is None:
            raise HTTPException(409, processed.status().problem or "Dropbox isn't set up here.")
        return processed.auth

    def status_out() -> ProcessedOut:
        status = processed.status()
        return ProcessedOut(
            source=status.source,  # type: ignore[arg-type]
            configured=status.configured,
            ready=status.ready,
            problem=status.problem,
            sign_in=_sign_in(status.sign_in) if status.sign_in else None,
            folder=status.folder,
            files=status.files,
            bytes=status.bytes,
            indexed_at=status.indexed_at,
            listing=_listing(status.listing) if status.listing else None,
            listing_error=status.listing_error,
        )

    @router.get("")
    async def get_status() -> ProcessedOut:
        return await run(status_out)

    @router.post("/connect")
    async def connect() -> SignInOut:
        return _sign_in(await run(auth().start))

    @router.post("/connect/finish")
    async def finish(body: CodeIn) -> SignInOut:
        return _sign_in(await run(lambda: auth().finish(body.code)))

    @router.post("/connect/cancel")
    async def cancel() -> SignInOut:
        return _sign_in(await run(auth().cancel))

    @router.post("/disconnect")
    async def disconnect() -> SignInOut:
        return _sign_in(await run(processed.disconnect))

    @router.get("/folders")
    async def folders(
        inside: str = Query(default="", max_length=200, pattern=r"^(id:[A-Za-z0-9_-]+)?$"),
    ) -> list[FolderOut]:
        """Folders only: `inside` is a folder's Dropbox id from an earlier answer."""
        return [_folder(f) for f in await run(lambda: processed.team_folders(inside))]

    @router.post("/folder")
    async def choose(body: ChooseIn) -> ProcessedOut:
        await run(lambda: processed.choose(body.id))
        await run(processed.start_refresh)
        return await run(status_out)

    @router.post("/refresh")
    async def refresh() -> ProcessedOut:
        """Starts a refresh (or joins one under way) and returns at once;
        `listing` says how far it's got."""
        await run(processed.start_refresh)
        return await run(status_out)

    return router


def _sign_in(state: SignIn) -> SignInOut:
    return SignInOut(
        state=state.state,
        authorize_url=state.authorize_url,
        account=state.account.name if state.account else None,
        message=state.message,
    )


def _folder(folder: Folder) -> FolderOut:
    return FolderOut(id=folder.id, path=folder.path, name=folder.name)


def _listing(progress: Progress) -> ListingOut:
    return ListingOut(
        files=progress.files,
        folders_done=progress.folders_done,
        folders_total=progress.folders_total,
        seconds=int(time.monotonic() - progress.started),
    )
