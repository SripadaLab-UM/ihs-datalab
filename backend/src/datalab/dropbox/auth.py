"""Signing in to Dropbox with the lab's app (OAuth with PKCE), and its tokens.

DataLab opens Dropbox's sign-in page; the person approves the app and
Dropbox shows a short code, which they paste into DataLab. With PKCE the app
needs no secret: DataLab holds only its app key, which isn't one.

- Access tokens last about four hours. The refresh token doesn't expire or
  change; DataLab asks for a new access token with it as needed.
- The tokens live only in the OS keychain and in this process: never in a
  file, a command line, a container, or the browser.
- The app may read file names and contents, and basic account details (to
  find the team space, where the lab's folders are). Nothing else, and never
  write.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, NoReturn
from urllib.parse import urlencode

import httpx
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "datalab-dropbox"
KEYCHAIN_ACCOUNT = "user-token"
AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"
API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"
SCOPES = ("account_info.read", "files.metadata.read", "files.content.read")
# Refresh a token this long before it expires, so a download never starts
# with one that runs out part-way.
_REFRESH_MARGIN = 10 * 60
_TIMEOUT = httpx.Timeout(20)
# How long a started sign-in waits for its code.
_FLOW_SECONDS = 15 * 60
RAN_OUT = "The Dropbox sign-in no longer works. Connect Dropbox again."
_UNSAVED = (
    "DataLab couldn't save your Dropbox sign-in to the keychain, and keeps trying. If "
    "DataLab stops first, connect Dropbox again."
)


class SignInNeeded(RuntimeError):
    """Nobody is signed in, or Dropbox no longer accepts the sign-in."""


class DropboxUnavailable(RuntimeError):
    """Dropbox couldn't be reached, or answered with an error. The sign-in is kept."""


@dataclass(frozen=True)
class Account:
    name: str
    # The team space's root, where team folders are; the same as the home
    # namespace for an account without a team space.
    root_namespace: str
    home_namespace: str

    @property
    def team_space(self) -> bool:
        return self.root_namespace != self.home_namespace


@dataclass(frozen=True)
class Tokens:
    access_token: str
    # Seconds since the epoch.
    access_expires_at: float | None
    refresh_token: str | None
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
                account=Account(**account) if isinstance(account, dict) else None,
            )
        except (ValueError, KeyError, TypeError):
            return None


class TokenStore:
    """The person's Dropbox tokens in the OS keychain, as one entry."""

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


SignInState = Literal["signed out", "waiting", "signed in", "failed"]


@dataclass(frozen=True)
class SignIn:
    """Where connecting Dropbox has got to, as Settings shows it."""

    state: SignInState
    # While waiting: the page where the person approves the app and gets the code.
    authorize_url: str | None = None
    account: Account | None = None
    message: str | None = None


@dataclass
class _Flow:
    verifier: str
    authorize_url: str
    expires_at: float


def pkce_pair() -> tuple[str, str]:
    """A PKCE code verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class DropboxAuth:
    """The Dropbox sign-in for this DataLab, and fresh tokens.

    Thread-safe: downloads on worker threads ask for tokens while the web
    routes run the sign-in. One refresh at a time.
    """

    def __init__(
        self,
        app_key: str,
        *,
        store: TokenStore | None = None,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app_key = app_key
        self._store = store or TokenStore()
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._clock = clock
        self._lock = threading.RLock()
        self._flow: _Flow | None = None
        # How the last attempt ended, until the next one starts.
        self._ended: SignIn | None = None
        # Tokens the keychain wouldn't take: kept here and saved again at each use.
        self._unsaved: Tokens | None = None

    # Signing in -------------------------------------------------------------

    def status(self) -> SignIn:
        """Where signing in is, without asking Dropbox."""
        with self._lock:
            if self._flow is not None and self._flow.expires_at > self._clock():
                return SignIn("waiting", authorize_url=self._flow.authorize_url)
            self._flow = None
            tokens = self._load()
            if tokens is not None:
                message = _UNSAVED if self._unsaved is not None else None
                return SignIn("signed in", account=tokens.account, message=message)
            return self._ended or SignIn("signed out")

    def start(self) -> SignIn:
        """A new sign-in: the page where the person approves the app."""
        with self._lock:
            verifier, challenge = pkce_pair()
            query = urlencode(
                {
                    "client_id": self._app_key,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "token_access_type": "offline",
                    "scope": " ".join(SCOPES),
                }
            )
            url = f"{AUTHORIZE}?{query}"
            self._flow = _Flow(verifier, url, self._clock() + _FLOW_SECONDS)
            self._ended = None
            return SignIn("waiting", authorize_url=url)

    def finish(self, code: str) -> SignIn:
        """Exchange the code Dropbox showed the person for tokens."""
        code = code.strip()
        with self._lock:
            flow = self._flow
            if flow is None or flow.expires_at <= self._clock():
                self._flow = None
                return self._end(
                    SignIn("failed", message="That sign-in has expired. Connect Dropbox again.")
                )
            if not code or len(code) > 200 or not code.isprintable():
                return SignIn(
                    "waiting",
                    authorize_url=flow.authorize_url,
                    message="Paste the code Dropbox shows after you click Allow.",
                )
            raw = self._post_token(
                {
                    "code": code,
                    "grant_type": "authorization_code",
                    "code_verifier": flow.verifier,
                    "client_id": self._app_key,
                }
            )
            if "access_token" not in raw:
                if raw.get("error") == "invalid_grant":
                    # A mistyped or used code: the same sign-in can be tried again.
                    return SignIn(
                        "waiting",
                        authorize_url=flow.authorize_url,
                        message="Dropbox didn't accept that code. Check it, or start again.",
                    )
                return self._end(
                    SignIn(
                        "failed", message=_dropbox_error(raw, "Dropbox didn't finish the sign-in.")
                    )
                )
            now = self._clock()
            tokens = self._tokens_from(raw, now, refresh=raw.get("refresh_token"))
            self._flow = None
            account = self._fetch_account(tokens.access_token)
            if account is None:
                with contextlib.suppress(DropboxUnavailable):
                    self._revoke(tokens.access_token)
                return self._end(
                    SignIn(
                        "failed",
                        message="Dropbox signed you in, but didn't say which account. Try again.",
                    )
                )
            tokens = replace(tokens, account=account)
            self._save(tokens)
            self._ended = None
            message = _UNSAVED if self._unsaved is not None else None
            return SignIn("signed in", account=account, message=message)

    def cancel(self) -> SignIn:
        with self._lock:
            self._flow = None
            self._ended = None
            return self.status()

    def sign_out(self) -> SignIn:
        """Revoke the sign-in on Dropbox (when it can be reached) and forget it here."""
        with self._lock:
            self._flow = None
            self._ended = None
            tokens = self._load()
            if tokens is not None:
                try:
                    self._revoke(self._fresh_token(tokens))
                except (DropboxUnavailable, SignInNeeded) as error:
                    log.warning("couldn't revoke the Dropbox sign-in: %s", type(error).__name__)
            self._forget()
            return SignIn("signed out")

    # Tokens -----------------------------------------------------------------

    def signed_in(self) -> bool:
        return self._load() is not None

    def account(self) -> Account | None:
        tokens = self._load()
        return tokens.account if tokens is not None else None

    def access_token(self) -> str:
        """A token good for at least ten more minutes, refreshed if need be.

        SignInNeeded if nobody is signed in or Dropbox refused the refresh
        token; DropboxUnavailable if Dropbox couldn't be reached."""
        with self._lock:
            tokens = self._load()
            if tokens is None:
                raise SignInNeeded("Connect Dropbox in Settings first.")
            return self._fresh_token(tokens)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def refused(self) -> NoReturn:
        """Dropbox answered 401 to a token it gave: the sign-in is gone
        (revoked, or the app's access removed)."""
        with self._lock:
            self._ran_out()

    # ------------------------------------------------------------------------

    def _fresh_token(self, tokens: Tokens) -> str:
        now = self._clock()
        expires = tokens.access_expires_at
        if expires is None or expires - now > _REFRESH_MARGIN:
            return tokens.access_token
        if not tokens.refresh_token:
            if expires > now:
                return tokens.access_token
            self._ran_out()
        raw = self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self._app_key,
            }
        )
        if "access_token" not in raw:
            if raw.get("error") in ("invalid_grant", "invalid_client"):
                log.warning("Dropbox refused the refresh token: %s", raw.get("error"))
                self._ran_out()
            raise DropboxUnavailable(_dropbox_error(raw, "Dropbox didn't refresh the sign-in."))
        fresh = replace(
            self._tokens_from(raw, now, refresh=tokens.refresh_token), account=tokens.account
        )
        self._save(fresh)
        return fresh.access_token

    def _load(self) -> Tokens | None:
        if self._unsaved is not None:
            self._save(self._unsaved)
            if self._unsaved is not None:
                return self._unsaved
        return self._store.load()

    def _save(self, tokens: Tokens) -> None:
        try:
            self._store.save(tokens)
        except Exception as error:  # a locked or broken keychain
            log.error("couldn't save the Dropbox sign-in to the keychain: %s", type(error).__name__)
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

    def _end(self, result: SignIn) -> SignIn:
        self._flow = None
        self._ended = result
        return result

    def _tokens_from(self, raw: dict[str, Any], now: float, *, refresh: str | None) -> Tokens:
        expires_in = raw.get("expires_in")
        return Tokens(
            access_token=str(raw["access_token"]),
            access_expires_at=now + float(expires_in) if expires_in else None,
            refresh_token=refresh or None,
        )

    def _fetch_account(self, token: str) -> Account | None:
        try:
            response = self._http.post(
                f"{API}/2/users/get_current_account",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=b"null",
            )
            if response.status_code != 200:
                return None
            raw = response.json()
            root = raw.get("root_info") or {}
            name = (raw.get("name") or {}).get("display_name") or ""
            return Account(
                name=str(name),
                root_namespace=str(root["root_namespace_id"]),
                home_namespace=str(root["home_namespace_id"]),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def _revoke(self, token: str) -> None:
        try:
            self._http.post(
                f"{API}/2/auth/token/revoke", headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as error:
            raise DropboxUnavailable(
                f"Dropbox couldn't be reached ({type(error).__name__})."
            ) from None

    def _post_token(self, data: dict[str, str | None]) -> dict[str, Any]:
        try:
            response = self._http.post(f"{API}/oauth2/token", data=data)
        except httpx.HTTPError as error:
            raise DropboxUnavailable(
                f"Dropbox couldn't be reached ({type(error).__name__})."
            ) from None
        if response.status_code >= 500 or response.status_code == 429:
            raise DropboxUnavailable(f"Dropbox answered {response.status_code}. Try again later.")
        try:
            raw = response.json()
        except ValueError:
            raise DropboxUnavailable("Dropbox's answer couldn't be read.") from None
        return raw if isinstance(raw, dict) else {}


def _dropbox_error(raw: dict[str, Any], fallback: str) -> str:
    said = raw.get("error_description") or raw.get("error_summary") or raw.get("error")
    return f"{fallback} Dropbox said: {str(said)[:200]}" if said else fallback
