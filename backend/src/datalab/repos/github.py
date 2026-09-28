"""Signing in to GitHub with the lab's GitHub App (device flow), and its tokens.

The person enters a short code at github.com/login/device; DataLab polls
until GitHub answers. The app has no client secret and no private key in
DataLab: the device flow and refreshing need only its client id.

- User tokens last 8 hours. The refresh token lasts about six months and
  GitHub replaces it every time it's used, so both new tokens are saved to
  the keychain before either is used. When the refresh token has expired
  (or GitHub refuses it), the person signs in again.
- The tokens live only in the OS keychain and in this process. Git gets the
  token from the credential helper (credential_helper.py), never from a file,
  a command line, or a container.
- The app may read and write repository contents and read metadata, nothing
  more. It can't see team membership, so missing access shows up as a 404
  from the repository itself, and DataLab says whom to ask.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, NoReturn

import httpx
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "datalab-github"
KEYCHAIN_ACCOUNT = "user-token"
GITHUB = "https://github.com"
API = "https://api.github.com"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Refresh a token this long before it expires, so a git command never
# starts with one that runs out part-way.
_REFRESH_MARGIN = 10 * 60
_TIMEOUT = httpx.Timeout(15)
# What GitHub says when a refresh token can never work again.
_REFUSED_REFRESH = ("bad_refresh_token", "invalid_grant")
# Said until the person signs in again (or out), so they know why.
RAN_OUT = "The GitHub sign-in has run out. Sign in again."
_UNSAVED = (
    "DataLab couldn't save your GitHub sign-in to the keychain, and keeps trying. "
    "Until it can, syncing and sharing wait; if DataLab stops first, sign in again."
)


class SignInNeeded(RuntimeError):
    """Nobody is signed in, or the sign-in ran out: sign in again."""


class GitHubUnavailable(RuntimeError):
    """GitHub couldn't be reached, or answered with an error. The sign-in is kept."""


@dataclass(frozen=True)
class Account:
    login: str
    id: int
    name: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.login

    @property
    def email(self) -> str:
        # GitHub's private address for the account: commits are attributed
        # to it, and reading the real address would need another permission.
        return f"{self.id}+{self.login}@users.noreply.github.com"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    # Seconds since the epoch; None when GitHub gave no expiry.
    access_expires_at: float | None
    refresh_token: str | None
    refresh_expires_at: float | None
    account: Account | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Tokens | None:
        try:
            raw = json.loads(text)
            account = raw.get("account")
            return cls(
                access_token=str(raw["access_token"]),
                access_expires_at=raw.get("access_expires_at"),
                refresh_token=raw.get("refresh_token"),
                refresh_expires_at=raw.get("refresh_expires_at"),
                account=Account(**account) if isinstance(account, dict) else None,
            )
        except (ValueError, KeyError, TypeError):
            return None


class TokenStore:
    """The user's GitHub tokens in the OS keychain, as one entry."""

    def __init__(self, service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT) -> None:
        self._service = service
        self._account = account

    def load(self) -> Tokens | None:
        try:
            text = keyring.get_password(self._service, self._account)
        except KeyringError:
            return None
        return Tokens.from_json(text) if text else None

    def save(self, tokens: Tokens) -> None:
        keyring.set_password(self._service, self._account, tokens.to_json())

    def clear(self) -> None:
        with contextlib.suppress(PasswordDeleteError, KeyringError):
            keyring.delete_password(self._service, self._account)


def saved_token_values(store: TokenStore | None = None) -> list[str]:
    """The saved tokens themselves, for the Safety check to look for. Never
    shown or logged."""
    tokens = (store or TokenStore()).load()
    if tokens is None:
        return []
    return [t for t in (tokens.access_token, tokens.refresh_token) if t]


SignInState = Literal["signed out", "waiting", "signed in", "expired", "denied", "failed"]


@dataclass(frozen=True)
class SignIn:
    """Where signing in has got to, as the Knowledge tab shows it."""

    state: SignInState
    # While waiting: the code to enter, and where.
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: float | None = None
    # How often to poll, in seconds.
    interval: int | None = None
    account: Account | None = None
    message: str | None = None


@dataclass
class _DeviceFlow:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: int
    next_poll_at: float


RepoAccess = Literal["write", "read", "none"]


class GitHubAuth:
    """The GitHub sign-in for this DataLab: the device flow, and fresh tokens.

    Thread-safe: git commands on worker threads ask for tokens while the web
    routes run the flow. Only this object refreshes, and only one refresh
    runs at a time, since each one replaces the refresh token.
    """

    def __init__(
        self,
        client_id: str,
        *,
        store: TokenStore | None = None,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_id = client_id
        self._store = store or TokenStore()
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._clock = clock
        self._lock = threading.RLock()
        self._flow: _DeviceFlow | None = None
        # How the last flow ended, until the next one starts.
        self._ended: SignIn | None = None
        # Tokens GitHub gave that the keychain wouldn't take: kept here (the
        # old refresh token no longer works) and saved again at each use.
        self._unsaved: Tokens | None = None

    # The device flow --------------------------------------------------------

    def status(self) -> SignIn:
        """Where signing in is, without asking GitHub."""
        with self._lock:
            if self._flow is not None:
                return self._waiting(self._flow)
            tokens = self._load()
            if tokens is not None and not self._refresh_expired(tokens):
                message = _UNSAVED if self._unsaved is not None else None
                return SignIn("signed in", account=tokens.account, message=message)
            if tokens is not None:
                return self._ended or SignIn("signed out", message=RAN_OUT)
            return self._ended or SignIn("signed out")

    def start(self) -> SignIn:
        """Ask GitHub for a code for the person to enter."""
        with self._lock:
            raw = self._post(f"{GITHUB}/login/device/code", {"client_id": self._client_id})
            if "device_code" not in raw:
                raise GitHubUnavailable(_github_error(raw, "GitHub didn't start the sign-in."))
            now = self._clock()
            interval = int(raw.get("interval") or 5)
            self._flow = _DeviceFlow(
                device_code=str(raw["device_code"]),
                user_code=str(raw["user_code"]),
                verification_uri=str(raw.get("verification_uri") or f"{GITHUB}/login/device"),
                expires_at=now + float(raw.get("expires_in") or 900),
                interval=interval,
                next_poll_at=now + interval,
            )
            self._ended = None
            return self._waiting(self._flow)

    def poll(self) -> SignIn:
        """Ask GitHub once whether the code was entered, if it's time to.

        GitHub wants at least `interval` seconds between polls; asking sooner
        just returns the current state."""
        with self._lock:
            flow = self._flow
            if flow is None:
                return self.status()
            now = self._clock()
            if now >= flow.expires_at:
                return self._end(SignIn("expired", message="The code expired. Start again."))
            if now < flow.next_poll_at:
                return self._waiting(flow)
            raw = self._post(
                f"{GITHUB}/login/oauth/access_token",
                {
                    "client_id": self._client_id,
                    "device_code": flow.device_code,
                    "grant_type": _DEVICE_GRANT,
                },
            )
            error = raw.get("error")
            if error == "authorization_pending":
                flow.next_poll_at = now + flow.interval
                return self._waiting(flow)
            if error == "slow_down":
                flow.interval = int(raw.get("interval") or flow.interval + 5)
                flow.next_poll_at = now + flow.interval
                return self._waiting(flow)
            if error == "expired_token":
                return self._end(SignIn("expired", message="The code expired. Start again."))
            if error == "access_denied":
                return self._end(SignIn("denied", message="The sign-in was cancelled on GitHub."))
            if error or "access_token" not in raw:
                message = _github_error(raw, "GitHub didn't finish the sign-in.")
                return self._end(SignIn("failed", message=message))
            tokens = self._tokens_from(raw, now)
            self._save(tokens)
            self._flow = None
            # Who signed in: commits are attributed to them.
            account = self._fetch_account(tokens.access_token)
            if account is not None:
                tokens = replace(tokens, account=account)
                self._save(tokens)
            self._ended = None
            message = _UNSAVED if self._unsaved is not None else None
            return SignIn("signed in", account=account, message=message)

    def cancel(self) -> SignIn:
        with self._lock:
            if self._flow is not None:
                self._flow = None
                self._ended = None
            return self.status()

    def sign_out(self) -> SignIn:
        """Forget the tokens on this computer. (Revoking them on GitHub would
        need the app's client secret, which DataLab doesn't have; the person
        can revoke the app under GitHub's Settings → Applications.)"""
        with self._lock:
            self._flow = None
            self._ended = None
            self._forget()
            return SignIn("signed out")

    # Tokens -----------------------------------------------------------------

    def signed_in(self) -> bool:
        tokens = self._load()
        return tokens is not None and not self._refresh_expired(tokens)

    def access_token(self) -> str:
        """A token good for at least ten more minutes, refreshed if need be.

        SignInNeeded if nobody is signed in or the sign-in ran out;
        GitHubUnavailable if a refresh couldn't reach GitHub (the old tokens
        are kept, so it can be tried again)."""
        with self._lock:
            tokens = self._load()
            if tokens is None:
                raise SignInNeeded("Sign in to GitHub first.")
            now = self._clock()
            expires = tokens.access_expires_at
            if expires is None or expires - now > _REFRESH_MARGIN:
                return tokens.access_token
            refresh_ran_out = (
                tokens.refresh_expires_at is not None and tokens.refresh_expires_at <= now
            )
            if not tokens.refresh_token or refresh_ran_out:
                if expires > now:
                    return tokens.access_token  # good for a few minutes more
                self._ran_out()
            raw = self._post(
                f"{GITHUB}/login/oauth/access_token",
                {
                    "client_id": self._client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                },
            )
            if "access_token" not in raw:
                if raw.get("error") in _REFUSED_REFRESH:
                    # Expired, revoked, or already used: it can't be tried again.
                    log.warning("GitHub refused the refresh token: %s", raw.get("error"))
                    self._ran_out()
                # Anything else may pass: keep the sign-in, try again later.
                raise GitHubUnavailable(_github_error(raw, "GitHub didn't refresh the sign-in."))
            fresh = replace(self._tokens_from(raw, now), account=tokens.account)
            # Saved before it's used: the old refresh token no longer works.
            self._save(fresh)
            return fresh.access_token

    def token_for_git(self) -> None:
        """Make sure git's credential helper, which reads the keychain, will
        find a fresh token there."""
        self.access_token()
        if self._unsaved is not None:
            raise GitHubUnavailable(_UNSAVED)

    def account(self) -> Account | None:
        """Who is signed in, asking GitHub if it isn't known yet."""
        with self._lock:
            tokens = self._load()
            if tokens is None:
                return None
            if tokens.account is not None:
                return tokens.account
        account = self._fetch_account(self.access_token())
        if account is not None:
            with self._lock:
                current = self._load()
                if current is not None:
                    self._save(replace(current, account=account))
        return account

    def repo_access(self, repo: str) -> RepoAccess:
        """What the signed-in person may do in `repo` ("owner/name"). The
        app can't read team membership: a repository that isn't visible
        answers 404, which means no access."""
        token = self.access_token()
        try:
            response = self._http.get(f"{API}/repos/{repo}", headers=_api_headers(token))
        except httpx.HTTPError as error:
            raise GitHubUnavailable(
                f"GitHub couldn't be reached ({type(error).__name__})."
            ) from None
        if response.status_code in (403, 404):
            return "none"
        if response.status_code == 401:
            raise SignInNeeded("GitHub didn't accept the sign-in. Sign in again.")
        if response.status_code != 200:
            raise GitHubUnavailable(f"GitHub answered {response.status_code}.")
        permissions = response.json().get("permissions") or {}
        return "write" if permissions.get("push") else "read"

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """A request to GitHub's API (`path` such as /repos/owner/name/contents/…)
        as the signed-in person, with this sign-in's own client. The token is
        only ever in the request's header, never logged or returned, and only
        ever sent to https://api.github.com: a path that could lead anywhere
        else is refused (ValueError), as are headers, auth or redirects of the
        caller's own."""
        url = api_url(path)
        refused = {"headers", "auth", "follow_redirects"} & set(kwargs)
        if refused:
            raise ValueError(f"GitHubAuth.request doesn't take {', '.join(sorted(refused))}.")
        token = self.access_token()
        try:
            return self._http.request(
                method, url, headers=_api_headers(token), follow_redirects=False, **kwargs
            )
        except httpx.HTTPError as error:
            raise GitHubUnavailable(
                f"GitHub couldn't be reached ({type(error).__name__})."
            ) from None

    # ------------------------------------------------------------------------

    def _load(self) -> Tokens | None:
        if self._unsaved is not None:
            self._save(self._unsaved)  # try the keychain again
            if self._unsaved is not None:
                return self._unsaved
        return self._store.load()

    def _save(self, tokens: Tokens) -> None:
        try:
            self._store.save(tokens)
        except Exception as error:  # a locked or broken keychain
            log.error("couldn't save the GitHub sign-in to the keychain: %s", type(error).__name__)
            self._unsaved = tokens
        else:
            self._unsaved = None

    def _forget(self) -> None:
        self._unsaved = None
        self._store.clear()

    def _ran_out(self) -> NoReturn:
        self._forget()
        self._ended = SignIn("signed out", message=RAN_OUT)
        raise SignInNeeded(RAN_OUT)

    def _tokens_from(self, raw: dict[str, Any], now: float) -> Tokens:
        def at(key: str) -> float | None:
            value = raw.get(key)
            return now + float(value) if value else None

        return Tokens(
            access_token=str(raw["access_token"]),
            access_expires_at=at("expires_in"),
            refresh_token=raw.get("refresh_token") or None,
            refresh_expires_at=at("refresh_token_expires_in"),
        )

    def _refresh_expired(self, tokens: Tokens) -> bool:
        now = self._clock()
        if tokens.access_expires_at is None or tokens.access_expires_at > now:
            return False
        return tokens.refresh_expires_at is not None and tokens.refresh_expires_at <= now

    def _fetch_account(self, token: str) -> Account | None:
        try:
            response = self._http.get(f"{API}/user", headers=_api_headers(token))
            if response.status_code != 200:
                return None
            raw = response.json()
            return Account(login=str(raw["login"]), id=int(raw["id"]), name=raw.get("name") or "")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(url, data=data, headers={"accept": "application/json"})
        except httpx.HTTPError as error:
            raise GitHubUnavailable(
                f"GitHub couldn't be reached ({type(error).__name__})."
            ) from None
        if response.status_code >= 500 or response.status_code == 429:
            raise GitHubUnavailable(f"GitHub answered {response.status_code}. Try again later.")
        try:
            raw = response.json()
        except ValueError:
            raise GitHubUnavailable("GitHub's answer couldn't be read.") from None
        return raw if isinstance(raw, dict) else {}

    def _waiting(self, flow: _DeviceFlow) -> SignIn:
        return SignIn(
            "waiting",
            user_code=flow.user_code,
            verification_uri=flow.verification_uri,
            expires_at=flow.expires_at,
            interval=flow.interval,
        )

    def _end(self, result: SignIn) -> SignIn:
        self._flow = None
        self._ended = result
        return result


_API_HOST = "api.github.com"


def api_url(path: str) -> httpx.URL:
    """GitHub's API URL for `path`, which must be a plain absolute path on
    api.github.com, in plain ASCII: no `//`, `@`, backslash, `..`, `?`, `#`
    or `%` (nothing percent-encoded or full-width to slip past these).
    ValueError otherwise."""
    if (
        not path.startswith("/")
        or any(bad in path for bad in ("//", "@", "\\", "..", "?", "#", "%"))
        or any(ord(ch) < 0x21 or ord(ch) >= 0x7F for ch in path)
    ):
        raise ValueError("Not a GitHub API path.")
    url = httpx.URL(API).join(path)
    if url.scheme != "https" or url.host != _API_HOST or url.port not in (None, 443):
        raise ValueError("Not a GitHub API path.")
    return url


def _api_headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "accept": "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
    }


def _github_error(raw: dict[str, Any], fallback: str) -> str:
    # GitHub's own description, never anything of ours (no codes or tokens).
    described = raw.get("error_description") or raw.get("error")
    return f"{fallback} ({str(described)[:200]})" if described else fallback


def access_message(repo: str, login: str | None, contact: str | None) -> str:
    """What to tell someone GitHub won't let see `repo`."""
    who = f"your GitHub account (@{login})" if login else "your GitHub account"
    ask = contact or "the lab's DataLab maintainer"
    org = repo.split("/")[0]
    return (
        f"GitHub says {who} can't open {repo}. Ask {ask} to add you to the "
        f"datalab-users team in {org}, then press Sync."
    )
