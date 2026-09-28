"""GitHub sign-in (milestones 5 and 6): one for both lab repos, knowledge and pipelines.

GitHub's device flow (repos/github.py): `POST /sign-in` starts it and returns
the code to enter; the page then calls `POST /sign-in/poll` every `interval`
seconds until it's signed in (or expired, denied); `POST /sign-in/cancel`
and `POST /sign-out`. `GET /status` says whether signing in is possible here
and which repos it's for; each repo's own status and Sync are its area's
(`/api/knowledge`, `/api/pipelines`).

Signing in is possible when the GitHub App is set (`[repos] client_id`) and
at least one of `[repos] knowledge` and `[repos] pipelines` is, outside
practice. It uses the app's one `GitHubAuth`, shared with both repos, since
each token refresh replaces the refresh token.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.config import Settings
from datalab.repos.github import Account, GitHubAuth, GitHubUnavailable, SignIn, SignInNeeded


@dataclass(frozen=True)
class GitHubServices:
    """What the GitHub routes use, given by the app (app.py)."""

    settings: Settings  # `settings.repos`
    auth: GitHubAuth | None  # None when the GitHub App isn't set


class GitHubAccountOut(BaseModel):
    login: str
    name: str


class GitHubRepoOut(BaseModel):
    area: Literal["knowledge", "pipelines"]
    name: str  # such as SripadaLab-UM/ihs-pipelines


class GitHubStatusOut(BaseModel):
    available: bool
    # Why not, when it isn't.
    message: str | None = None
    signed_in: bool = False
    account: GitHubAccountOut | None = None
    # The lab repos this sign-in is for.
    repos: list[GitHubRepoOut] = []


class GitHubSignInOut(BaseModel):
    state: Literal["signed out", "waiting", "signed in", "expired", "denied", "failed"]
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: str | None = None
    # Seconds to wait between polls.
    interval: int | None = None
    account: GitHubAccountOut | None = None
    message: str | None = None


def unavailable(settings: Settings, auth: GitHubAuth | None) -> str | None:
    """Why signing in to GitHub isn't possible here, or None."""
    repos = settings.repos
    if settings.profile == "practice":
        return "Practice DataLab doesn't use the lab's repositories."
    if repos.knowledge is None and repos.pipelines is None and repos.support is None:
        return "Neither of the lab's repositories is set in settings.toml ([repos])."
    if auth is None:
        return "The lab's GitHub App isn't set in settings.toml ([repos] client_id)."
    return None


def build_github_router(services: GitHubServices) -> APIRouter:
    router = APIRouter(prefix="/api/github", tags=["github"])
    settings, shared = services.settings, services.auth
    why_not = unavailable(settings, shared)
    areas: tuple[tuple[Literal["knowledge", "pipelines"], str | None], ...] = (
        ("knowledge", settings.repos.knowledge),
        ("pipelines", settings.repos.pipelines),
    )
    repos = [GitHubRepoOut(area=area, name=name) for area, name in areas if name is not None]

    def auth() -> GitHubAuth:
        if why_not is not None or shared is None:
            raise HTTPException(409, why_not or "GitHub sign-in isn't set up.")
        return shared

    async def run(work: Callable[[], SignIn]) -> GitHubSignInOut:
        # GitHub and the keychain: never on the event loop.
        try:
            return _sign_in(await asyncio.to_thread(work))
        except SignInNeeded as error:
            # Not 401: that's the browser's own DataLab session.
            raise HTTPException(403, str(error)) from None
        except GitHubUnavailable as error:
            raise HTTPException(502, str(error)) from None

    @router.get("/status")
    async def status() -> GitHubStatusOut:
        if why_not is not None or shared is None:
            return GitHubStatusOut(available=False, message=why_not)
        state = await asyncio.to_thread(shared.status)
        return GitHubStatusOut(
            available=True,
            signed_in=state.state == "signed in",
            account=_account(state.account),
            repos=repos,
        )

    @router.get("/sign-in")
    async def sign_in_state() -> GitHubSignInOut:
        return await run(auth().status)

    @router.post("/sign-in")
    async def start_sign_in() -> GitHubSignInOut:
        return await run(auth().start)

    @router.post("/sign-in/poll")
    async def poll_sign_in() -> GitHubSignInOut:
        return await run(auth().poll)

    @router.post("/sign-in/cancel")
    async def cancel_sign_in() -> GitHubSignInOut:
        return await run(auth().cancel)

    @router.post("/sign-out")
    async def sign_out() -> GitHubSignInOut:
        return await run(auth().sign_out)

    return router


def _account(account: Account | None) -> GitHubAccountOut | None:
    return GitHubAccountOut(login=account.login, name=account.name) if account else None


def _sign_in(state: SignIn) -> GitHubSignInOut:
    expires = (
        datetime.fromtimestamp(state.expires_at, UTC).isoformat(timespec="seconds")
        if state.expires_at
        else None
    )
    return GitHubSignInOut(
        state=state.state,
        user_code=state.user_code,
        verification_uri=state.verification_uri,
        expires_at=expires,
        interval=state.interval,
        account=_account(state.account),
        message=state.message,
    )
