"""The browser side of the boundary: sign-in cookie, security headers, web UI.

- DataLab listens on 127.0.0.1 only. On top of that, the API needs a session
  cookie, set by a one-time sign-in link that `datalab serve` opens. Other
  programs or web pages on this computer can't use the API without it.
- Every response carries a Content Security Policy that allows network
  requests only to DataLab itself. An agent's answer that contains an image
  link or a chart with a data URL can't make the browser send data anywhere.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from datalab.auth.users import LOCAL, User

if TYPE_CHECKING:
    from datalab.auth.sessions import WebSessions

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # React sets style attributes; no external stylesheets.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        # Only DataLab's own <base href>, which mount_web_ui writes.
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

_CSP = b"content-security-policy"
_SECURITY_HEADERS = [
    (_CSP, CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    # No DNS lookups for links in a page: a looked-up name could carry data out.
    (b"x-dns-prefetch-control", b"off"),
]


class BrowserSession:
    """The one-time sign-in link and the cookie it sets."""

    def __init__(self, port: int) -> None:
        self._launch_token: str | None = secrets.token_urlsafe(24)
        self._cookie = secrets.token_urlsafe(32)
        # Goes up each time the session ends: live-update streams opened before
        # stop (see ended_since).
        self.generation = 0
        # Named for the port: browsers share cookies between ports of one host,
        # so two DataLabs on one computer (practice and an evaluation run, say)
        # would otherwise sign each other's windows out.
        self.cookie_name = f"datalab_session_{port}"

    def sign_in_path(self) -> str:
        return f"/sign-in?token={self._launch_token}"

    def redeem(self, token: str) -> str | None:
        """Exchange the launch token for the cookie value. Works once."""
        if self._launch_token and hmac.compare_digest(token, self._launch_token):
            self._launch_token = None
            return self._cookie
        return None

    def valid(self, cookie: str | None) -> bool:
        return bool(cookie) and hmac.compare_digest(cookie or "", self._cookie)

    def user_for(self, cookie: str | None) -> User | None:
        """A person's computer has one user, the local one."""
        return LOCAL if self.valid(cookie) else None

    def end(self, cookie: str | None = None) -> None:
        """End the browser session: the cookie stops working, in every window
        that has it. The sign-in link was used up, so signing in again takes a
        new one: DataLab prints one when it starts."""
        self._cookie = secrets.token_urlsafe(32)
        self.generation += 1


class SignInGate(Protocol):
    """What ApiProtection asks of the browser's sign-in: BrowserSession on a
    person's computer, WebSessions (auth/sessions.py) on the lab's server."""

    cookie_name: str

    def user_for(self, cookie: str | None) -> User | None: ...

    def valid(self, cookie: str | None) -> bool: ...

    def end(self, cookie: str | None = None) -> None: ...


@dataclass(frozen=True)
class CookieRules:
    """How the sign-in cookie is set: on a person's computer, SameSite=Strict
    on 127.0.0.1; on the lab's server, Secure and SameSite=Lax (GitHub sends
    the browser back from another site, and a Strict cookie set then isn't
    sent on its first page), under DataLab's base path only."""

    path: str = "/"
    secure: bool = False
    samesite: str = "strict"

    def kwargs(self) -> dict[str, object]:
        return {
            "httponly": True,
            "samesite": self.samesite,
            "secure": self.secure,
            "path": self.path,
        }


def ended_since(request: Request) -> Callable[[], bool]:
    """For a live-update stream: whether the browser has been signed out
    since it opened (End session, sign-out, the sign-in ran out), so it stops
    instead of sending events until the browser reconnects (and is refused).
    On the server, always False where the stream didn't open signed in (tests)."""
    gate: SignInGate | None = getattr(request.app.state, "browser", None)
    if gate is None:
        return lambda: False
    if isinstance(gate, BrowserSession):
        # A person's computer: End session ends every window's streams.
        session, generation = gate, gate.generation
        return lambda: session.generation != generation
    # The lab's server: this browser's own sign-in.
    cookie = request.cookies.get(gate.cookie_name)
    opened = gate.valid(cookie)
    return lambda: opened and not gate.valid(cookie)


# Set in the request's scope state once ApiProtection has let it through its
# sign-in check (the api's routes, not /api/health): middleware inside that
# reads bodies (api/textguard.py) reads only these.
SIGNED_IN = "datalab.signed_in"
# And who signed in: an auth.users.User.
USER = "datalab.user"


def current_user(request: Request) -> User:
    """Who is making this request (ApiProtection set it). The local user
    where nothing checks (tests of single routes)."""
    return request.scope.get("state", {}).get(USER) or LOCAL


# Methods that change nothing: the only ones a request from another page may make.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def refuse_cross_site(scope: Scope, *, public_origin: str | None = None) -> str | None:
    """Why a state-changing /api request isn't DataLab's own page's, or None.

    Browsers count every port of 127.0.0.1 as one site, so another local web
    page (any program's, on any port) gets the SameSite=Strict cookie sent
    with its requests. A form or a no-cors fetch from there can only send a
    "simple" request: no JSON content type, and no way to hide where it's
    from. So every POST, PUT, PATCH and DELETE under /api must:
    - come from this origin (Sec-Fetch-Site same-origin; absent in older
      browsers and in tests),
    - if it says where it's from (Origin), be this very scheme, host and port,
    - be JSON (`content-type: application/json`), which only a same-origin
      page's fetch can send without the browser asking first (CORS preflight,
      which DataLab never allows). The frontend's request() sends it on every
      call, with or without a body. No /api route takes anything else (no
      uploads or forms: attaching files goes through the computer's own
      picker), so nothing is exempt.
    """
    headers = Headers(scope=scope)
    site = headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return f"Refused a request from another site ({site})."
    origin = headers.get("origin")
    if origin is not None:
        # On the lab's server, Apache answers the browser: its address is
        # the setting, not what reaches DataLab through the socket.
        own = public_origin or f"{scope.get('scheme', 'http')}://{headers.get('host', '')}"
        if origin != own:
            return "Refused a request from another origin."
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return "DataLab's API takes JSON requests only."
    return None


class ApiProtection:
    """Require the session cookie on /api (except /api/health), refuse
    state-changing requests from other pages (refuse_cross_site), and add
    security headers. With enforce=False (tests of single routes) neither
    check runs."""

    def __init__(
        self,
        app: ASGIApp,
        session: SignInGate,
        *,
        enforce: bool = True,
        public_origin: str | None = None,
        signed_out: str = "Open DataLab from its launcher to sign in.",
    ) -> None:
        self._app = app
        self._session = session
        self._signed_out = signed_out
        self._enforce = enforce
        # The lab's server's https:// address ([server] public_url).
        self._public_origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path: str = scope["path"]
        if (
            self._enforce
            and path.startswith("/api/")
            and scope.get("method", "GET") not in _SAFE_METHODS
            and (why := refuse_cross_site(scope, public_origin=self._public_origin))
        ):
            response = JSONResponse({"detail": why}, status_code=403)
            await response(scope, receive, _with_headers(send))
            return
        if path.startswith("/api/") and path != "/api/health":
            user = self._session.user_for(Request(scope).cookies.get(self._session.cookie_name))
            if self._enforce and user is None:
                response = JSONResponse({"detail": self._signed_out}, status_code=401)
                await response(scope, receive, _with_headers(send))
                return
            # Past the sign-in check: what runs inside may read the body.
            state = scope.setdefault("state", {})
            state[SIGNED_IN] = True
            state[USER] = user or LOCAL
        await self._app(scope, receive, _with_headers(send))


def _with_headers(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            # A few routes (workspace files, previews) set a stricter policy of
            # their own. Two policies would both apply, so keep only theirs.
            has_policy = any(name.lower() == b"content-security-policy" for name, _ in headers)
            extra = [h for h in _SECURITY_HEADERS if not (has_policy and h[0] == _CSP)]
            message["headers"] = [*headers, *extra]
        await send(message)

    return wrapped


class SessionUserOut(BaseModel):
    login: str
    name: str
    admin: bool
    # "desktop" or "server": what the page offers (sign-out, GitHub).
    deployment: str


class SessionActivityOut(BaseModel):
    # A conversation's agent is working (or starting, or restoring).
    agent_turn: bool
    # A workflow run is queued or running.
    workflow_run: bool


def add_session_routes(
    app: FastAPI,
    session: SignInGate,
    activity: Callable[[], SessionActivityOut] = lambda: SessionActivityOut(
        agent_turn=False, workflow_run=False
    ),
    cookie: CookieRules | None = None,
    deployment: str = "desktop",
    on_sign_out: Callable[[User], None] | None = None,
) -> None:
    """`POST /api/session/end`: End session, from the toolbar's session menu.
    Only a signed-in window can call it (ApiProtection checks the cookie).
    `GET /api/session/activity`: what's still going, which carries on after
    End session but stops when DataLab is restarted to sign in again."""

    rules = cookie or CookieRules()

    @app.get("/api/session/activity", tags=["session"])
    def session_activity() -> SessionActivityOut:
        return activity()

    @app.get("/api/session/me", tags=["session"])
    def who_am_i(request: Request) -> SessionUserOut:
        user = current_user(request)
        return SessionUserOut(
            login=user.login, name=user.display_name, admin=user.admin, deployment=deployment
        )

    @app.post("/api/session/end", status_code=204, tags=["session"])
    def end_session(request: Request) -> Response:
        """End session on a person's computer (every window); sign out on
        the lab's server (this browser, and the person's GitHub tokens)."""
        if on_sign_out is not None:
            on_sign_out(current_user(request))
        session.end(request.cookies.get(session.cookie_name))
        response = Response(status_code=204)
        response.delete_cookie(session.cookie_name, **rules.kwargs())  # type: ignore[arg-type]
        return response


# The tab's icons (frontend/public): kept at fixed names, so never cached for long.
_ICON_NAMES = ("favicon", "apple-touch-icon")


def with_base(html: str, base: str) -> str:
    """index.html with `<base href>` naming where DataLab lives: "/" on a
    person's computer, "/datalab/" on the lab's server. The build's paths
    are relative (vite.config.ts), and the frontend reads this tag
    (src/lib/base.ts), so one build serves at either."""
    tag = f'<base href="{base}/" />'
    return html.replace("<head>", f"<head>\n    {tag}", 1)


def mount_web_ui(
    app: FastAPI,
    session: BrowserSession,
    dist: Path | None,
    base: str = "",
    *,
    link: bool = True,
    web_sessions: WebSessions | None = None,
    cookie: CookieRules | None = None,
) -> None:
    """The web UI. `base` is where DataLab lives on its site ("" at the root,
    "/datalab" on the lab's server, where Apache strips it before passing
    requests on): what the page and redirects name.

    `link`: whether the launcher's one-time link signs in (never on the real
    server, where only GitHub does). With `web_sessions` (the lab's server),
    it signs in the local user as a server sign-in."""
    rules = cookie or CookieRules(path=f"{base}/")

    if link:

        @app.get("/sign-in", include_in_schema=False)
        def sign_in(token: str) -> RedirectResponse:
            redeemed = session.redeem(token)
            if redeemed is None:
                return RedirectResponse(f"{base}/signed-out", status_code=303)
            name, value = session.cookie_name, redeemed
            if web_sessions is not None:
                name, value = web_sessions.cookie_name, web_sessions.start(LOCAL)
            response = RedirectResponse(f"{base}/", status_code=303)
            response.set_cookie(name, value, **rules.kwargs())  # type: ignore[arg-type]
            return response

    if dist is None or not (dist / "index.html").exists():
        return

    index = dist / "index.html"
    page = with_base(index.read_text(encoding="utf-8"), base)

    def index_page() -> HTMLResponse:
        # The page always revalidates, so an update is picked up on the next
        # load; the files it names are content-hashed and can be cached.
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    def web_ui(path: str) -> Response:
        # Built files if they exist; otherwise the app's index (client-side routes).
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            if candidate == index.resolve():
                return index_page()
            if candidate.parent == dist.resolve() and candidate.name.startswith(_ICON_NAMES):
                # The page names the icons with their content's hash (?v=), and
                # they revalidate too, so a new icon shows after an update.
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
            return FileResponse(candidate)
        return index_page()
