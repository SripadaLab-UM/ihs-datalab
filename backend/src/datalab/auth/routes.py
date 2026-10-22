"""`/auth/github/login` and `/auth/github/callback`: signing in to the lab's
shared DataLab with GitHub (github_web.py). Outside /api: they're page
loads (a redirect to GitHub and back), not the page's JSON calls.

A sign-in that doesn't work ends on the signed-out page with a reason
code, which the page explains (frontend SignedOut).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from datalab.auth.github_web import STATE_COOKIE, STATE_SECONDS, GitHubWebSignIn, Refused
from datalab.auth.sessions import WebSessions
from datalab.web import CookieRules

log = logging.getLogger(__name__)


def mount_github_sign_in(
    app: FastAPI,
    sign_in: GitHubWebSignIn | None,
    sessions: WebSessions,
    *,
    base: str,
    cookie: CookieRules,
) -> None:
    """`sign_in` is None when the server's GitHub client secret or token key
    isn't in place yet: the sign-in page says it isn't set up."""
    state_rules = CookieRules(path=f"{base}/auth/", secure=cookie.secure, samesite="lax")

    def signed_out(reason: str) -> RedirectResponse:
        response = RedirectResponse(f"{base}/signed-out?reason={reason}", status_code=303)
        response.delete_cookie(STATE_COOKIE, **state_rules.kwargs())  # type: ignore[arg-type]
        return response

    @app.get("/auth/github/login", include_in_schema=False)
    def login() -> RedirectResponse:
        if sign_in is None:
            return signed_out("not-set-up")
        started = sign_in.start()
        response = RedirectResponse(started.url, status_code=303)
        response.set_cookie(
            STATE_COOKIE,
            started.cookie,
            max_age=STATE_SECONDS,
            **state_rules.kwargs(),  # type: ignore[arg-type]
        )
        return response

    @app.get("/auth/github/callback", include_in_schema=False)
    def callback(
        request: Request, code: str = "", state: str = "", error: str = ""
    ) -> RedirectResponse:
        # A plain def: FastAPI runs it on a worker thread (it calls GitHub).
        if sign_in is None:
            return signed_out("not-set-up")
        if error or not code:
            return signed_out("denied")
        try:
            user = sign_in.finish(code, state, request.cookies.get(STATE_COOKIE))
        except Refused as refused:
            log.info("GitHub sign-in refused: %s", refused.reason)
            return signed_out(refused.reason)
        log.info("signed in with GitHub: %s", user.login)
        response = RedirectResponse(f"{base}/", status_code=303)
        response.delete_cookie(STATE_COOKIE, **state_rules.kwargs())  # type: ignore[arg-type]
        response.set_cookie(sessions.cookie_name, sessions.start(user), **cookie.kwargs())  # type: ignore[arg-type]
        return response
