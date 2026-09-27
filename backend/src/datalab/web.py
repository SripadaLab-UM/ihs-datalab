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
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
        "base-uri 'none'",
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


# Set in the request's scope state once ApiProtection has let it through its
# sign-in check (the api's routes, not /api/health): middleware inside that
# reads bodies (api/textguard.py) reads only these.
SIGNED_IN = "datalab.signed_in"


class ApiProtection:
    """Require the session cookie on /api (except /api/health), and add security headers."""

    def __init__(self, app: ASGIApp, session: BrowserSession, *, enforce: bool = True) -> None:
        self._app = app
        self._session = session
        self._enforce = enforce

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path: str = scope["path"]
        if path.startswith("/api/") and path != "/api/health":
            if self._enforce and not self._session.valid(
                Request(scope).cookies.get(self._session.cookie_name)
            ):
                response = JSONResponse(
                    {"detail": "Open DataLab from its launcher to sign in."}, status_code=401
                )
                await response(scope, receive, _with_headers(send))
                return
            # Past the sign-in check: what runs inside may read the body.
            scope.setdefault("state", {})[SIGNED_IN] = True
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


def mount_web_ui(app: FastAPI, session: BrowserSession, dist: Path | None) -> None:
    @app.get("/sign-in", include_in_schema=False)
    def sign_in(token: str) -> RedirectResponse:
        cookie = session.redeem(token)
        if cookie is None:
            return RedirectResponse("/signed-out", status_code=303)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(session.cookie_name, cookie, httponly=True, samesite="strict", path="/")
        return response

    if dist is None or not (dist / "index.html").exists():
        return

    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def web_ui(path: str) -> FileResponse:
        # Built files if they exist; otherwise the app's index (client-side routes).
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            if candidate == index.resolve():
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
            return FileResponse(candidate)
        # The page always revalidates, so an update is picked up on the next
        # load; the files it names are content-hashed and can be cached.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
