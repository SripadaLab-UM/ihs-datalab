"""Signing in to the lab's shared DataLab with GitHub (the web flow).

The lab's GitHub App, as on a person's computer (repos/github.py), but by
redirect rather than a code to type: `GET /auth/github/login` sends the
browser to GitHub, which sends it back to `/auth/github/callback` with a
code. DataLab trades the code (with the App's client secret, held only on
the server, and the PKCE verifier) for the person's tokens, then lets them
in only if they may write to the lab's access repository (the
datalab-users team's) and, while `[server] open_to = "admins"`, are an
administrator.

Each person's tokens are kept encrypted (tokens.py) and used as theirs:
Save & share commits and pushes as them.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from datalab.auth.acting import acting_user
from datalab.auth.tokens import DbTokenStore
from datalab.auth.users import User, Users
from datalab.config import Settings
from datalab.repos.github import (
    API,
    GITHUB,
    Account,
    GitHubAuth,
    GitHubUnavailable,
    RepoAccess,
    SignIn,
    SignInNeeded,
    Tokens,
)

_TIMEOUT = httpx.Timeout(15)
# The browser's half of a sign-in (state and PKCE verifier), in a short-lived cookie.
STATE_COOKIE = "datalab_oauth"
STATE_SECONDS = 10 * 60


class Refused(Exception):
    """Why someone can't sign in: a code the sign-in page explains."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Started:
    url: str
    # The cookie's value: state and verifier.
    cookie: str


class GitHubWebSignIn:
    def __init__(
        self,
        settings: Settings,
        db: sqlite3.Connection,
        users: Users,
        *,
        client_secret: str,
        token_key: Fernet,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        server = settings.server
        if server is None or not settings.repos.client_id:
            raise ValueError("GitHub sign-in needs [server] and [repos] client_id.")
        access_repo = server.access_repo or settings.repos.knowledge
        if not access_repo:
            raise ValueError("GitHub sign-in needs [server] access_repo or [repos] knowledge.")
        self._settings = settings
        self._server = server
        self._client_id = settings.repos.client_id
        self._client_secret = client_secret
        self._access_repo = access_repo
        self._db = db
        self._users = users
        self._key = token_key
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._clock = clock
        self._tokens_lock = threading.Lock()
        self._auths: dict[int, GitHubAuth] = {}
        self._auths_lock = threading.Lock()

    @property
    def redirect_uri(self) -> str:
        return f"{self._server.site}/auth/github/callback"

    def start(self) -> Started:
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "allow_signup": "false",
            }
        )
        return Started(f"{GITHUB}/login/oauth/authorize?{query}", f"{state}.{verifier}")

    def finish(self, code: str, state: str, cookie: str | None) -> User:
        """The person GitHub sent back. Refused if the browser's half doesn't
        match, GitHub won't trade the code, or they may not use DataLab."""
        expected_state, _, verifier = (cookie or "").partition(".")
        if not (expected_state and verifier and secrets.compare_digest(state, expected_state)):
            raise Refused("expired")
        raw = self._post(
            f"{GITHUB}/login/oauth/access_token",
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            },
        )
        if "access_token" not in raw:
            raise Refused("expired")
        now = self._clock()
        tokens = _tokens(raw, now)
        account = self._account(tokens.access_token)
        if self._access(tokens.access_token) != "write":
            raise Refused("not-in-lab")
        admin = account.id in self._server.admins
        if self._server.open_to == "admins" and not admin:
            raise Refused("admins-only")
        user = self._users.signed_in_with_github(account, admin=admin)
        self._store(user.id).save(replace(tokens, account=account))
        with self._auths_lock:
            self._auths.pop(user.id, None)  # fresh tokens: a fresh GitHubAuth
        return user

    def auth_for(self, user: User) -> GitHubAuth:
        """The person's own GitHub sign-in: their tokens, refreshed as needed,
        one refresh at a time (each replaces the refresh token)."""
        with self._auths_lock:
            auth = self._auths.get(user.id)
            if auth is None:
                auth = self._auths[user.id] = GitHubAuth(
                    self._client_id,
                    store=self._store(user.id),
                    client_secret=self._client_secret,
                    http=self._http,
                    clock=self._clock,
                )
            return auth

    def sign_out(self, user: User) -> None:
        """Forget the person's GitHub tokens (they sign in again next time)."""
        self._store(user.id).clear()
        with self._auths_lock:
            self._auths.pop(user.id, None)

    def _store(self, user_id: int) -> DbTokenStore:
        return DbTokenStore(self._db, user_id, self._key, self._tokens_lock)

    def _account(self, token: str) -> Account:
        response = self._get(f"{API}/user", token)
        if response.status_code != 200:
            raise Refused("failed")
        raw = response.json()
        return Account(login=str(raw["login"]), id=int(raw["id"]), name=raw.get("name") or "")

    def _access(self, token: str) -> str:
        # The App can't read team membership: a repository the person can't see
        # answers 404 (or 403 under the org's single sign-on), meaning no access.
        response = self._get(f"{API}/repos/{self._access_repo}", token)
        if response.status_code != 200:
            return "none"
        permissions = response.json().get("permissions") or {}
        return "write" if permissions.get("push") else "read"

    def _get(self, url: str, token: str) -> httpx.Response:
        try:
            return self._http.get(
                url,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/vnd.github+json",
                    "x-github-api-version": "2022-11-28",
                },
            )
        except httpx.HTTPError:
            raise Refused("unavailable") from None

    def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(url, data=data, headers={"accept": "application/json"})
            raw = response.json()
        except (httpx.HTTPError, ValueError):
            raise Refused("unavailable") from None
        return raw if isinstance(raw, dict) else {}


class PerPersonGitHub(GitHubAuth):
    """The lab's server's stand-in for the one GitHub sign-in a computer has
    (repos/github.py): the knowledge and pipelines services hold this, and
    each call is the acting person's own sign-in (auth/acting.py), from when
    they signed in to DataLab. Outside a request it's no one's: sign in.

    There's no device flow here: people sign in with GitHub as they sign in
    to DataLab."""

    def __init__(self, sign_in: GitHubWebSignIn | None, client_id: str) -> None:
        super().__init__(client_id, store=_Nowhere())
        self._sign_in = sign_in

    def _own(self) -> GitHubAuth:
        user = acting_user()
        if user is None or self._sign_in is None:
            raise SignInNeeded("Sign in with GitHub.")
        return self._sign_in.auth_for(user)

    def status(self) -> SignIn:
        try:
            return self._own().status()
        except SignInNeeded:
            return SignIn("signed out")

    def start(self) -> SignIn:
        return SignIn("signed out", message=_SIGN_IN_HERE)

    def poll(self) -> SignIn:
        return self.status()

    def cancel(self) -> SignIn:
        return self.status()

    def sign_out(self) -> SignIn:
        return SignIn("signed out", message=_SIGN_IN_HERE)

    def signed_in(self) -> bool:
        try:
            return self._own().signed_in()
        except SignInNeeded:
            return False

    def access_token(self) -> str:
        return self._own().access_token()

    def token_for_git(self) -> str:
        # Handed to that one git command (repos/git.py), never saved anywhere.
        return self._own().access_token()

    def account(self) -> Account | None:
        try:
            return self._own().account()
        except SignInNeeded:
            return None

    def repo_access(self, repo: str) -> RepoAccess:
        return self._own().repo_access(repo)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._own().request(method, path, **kwargs)


_SIGN_IN_HERE = "On the lab's DataLab you sign in with GitHub when you sign in to DataLab."


class _Nowhere:
    """No tokens of its own: the stand-in keeps none."""

    def load(self) -> Tokens | None:
        return None

    def save(self, tokens: Tokens) -> None:
        raise RuntimeError("The stand-in never keeps tokens.")

    def clear(self) -> None:
        return None


def _tokens(raw: dict[str, Any], now: float) -> Tokens:
    def at(key: str) -> float | None:
        value = raw.get(key)
        return now + float(value) if value else None

    return Tokens(
        access_token=str(raw["access_token"]),
        access_expires_at=at("expires_in"),
        refresh_token=raw.get("refresh_token") or None,
        refresh_expires_at=at("refresh_token_expires_in"),
    )


__all__ = ["STATE_COOKIE", "STATE_SECONDS", "GitHubUnavailable", "GitHubWebSignIn", "Refused"]
