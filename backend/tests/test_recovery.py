"""Model request recovery: what a failure is, and when the relay retries it."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.relay import build_relay_router, recovery
from datalab.sessions.tokens import SessionAccess, SessionTokens
from tests.test_relay import codex_request


def error_body(code: str | None = None, message: str = "") -> bytes:
    return json.dumps({"error": {"code": code, "message": message}}).encode()


class TestClassify:
    @pytest.mark.parametrize(
        ("status", "body", "kind"),
        [
            (429, error_body("rate_limit_exceeded", "Slow down"), "busy"),
            (429, error_body("insufficient_quota", "You exceeded your quota"), "quota"),
            (429, error_body("spend_limit_reached", "Monthly spend limit reached"), "quota"),
            # Azure's ordinary rate limit: its message links to "quotaincrease".
            (
                429,
                error_body(
                    "429",
                    "Requests have exceeded the token rate limit. Please retry after 6 seconds. "
                    "Please go here: https://aka.ms/oai/quotaincrease if you would like more.",
                ),
                "busy",
            ),
            (404, error_body(None, "azure-openai error: Resource not found"), "model_unavailable"),
            (401, error_body("invalid_api_key"), "auth"),
            (403, error_body("permission_denied"), "auth"),
            (503, b"<html>busy</html>", "busy"),
            (500, b"", "busy"),
            (400, error_body("context_length_exceeded"), "request"),
        ],
    )
    def test_kinds(self, status, body, kind):
        assert recovery.classify(status, httpx.Headers(), body).kind == kind

    def test_keeps_metadata_never_the_message(self):
        trouble = recovery.classify(
            429,
            httpx.Headers({"x-request-id": "req_123", "retry-after": "7"}),
            error_body("rate_limit_exceeded", "participant P-0001 in your prompt"),
        )
        record = trouble.record()
        assert record == {
            "kind": "busy",
            "status": 429,
            "code": "rate_limit_exceeded",
            "retry_after": 7.0,
            "request_id": "req_123",
        }
        assert "P-0001" not in json.dumps(record)

    def test_odd_codes_are_dropped(self):
        body = error_body("<script>alert(1)</script>")
        assert recovery.classify(429, httpx.Headers(), body).code is None


class TestRetryAfter:
    def test_seconds_milliseconds_and_dates(self):
        now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        date = format_datetime(now + timedelta(seconds=20), usegmt=True)
        assert recovery.retry_after(httpx.Headers({"retry-after": "12"})) == 12
        assert recovery.retry_after(httpx.Headers({"retry-after-ms": "1500"})) == 1.5
        assert recovery.retry_after(httpx.Headers({"retry-after": date}), now=now) == 20

    @pytest.mark.parametrize("value", ["soon", "", "-5"])
    def test_unreadable_or_negative(self, value):
        assert recovery.retry_after(httpx.Headers({"retry-after": value})) in (None, 0.0)

    def test_absent(self):
        assert recovery.retry_after(httpx.Headers()) is None


class TestNextDelay:
    def busy(self, retry_after=None):
        return recovery.Trouble("busy", 429, None, retry_after, None)

    def test_the_servers_wait_is_honoured(self):
        assert recovery.next_delay(self.busy(20), attempt=1, waited=0) == 20

    def test_a_wait_past_the_budget_stops_rather_than_retrying_early(self):
        assert recovery.next_delay(self.busy(120), attempt=1, waited=0) is None
        assert recovery.next_delay(self.busy(30), attempt=2, waited=40) is None

    def test_backoff_without_a_hint(self):
        first = recovery.next_delay(self.busy(), attempt=1, waited=0)
        second = recovery.next_delay(self.busy(), attempt=2, waited=0)
        assert first is not None and 1.6 <= first <= 2.4
        assert second is not None and 3.2 <= second <= 4.8

    def test_bounded_attempts(self):
        assert recovery.next_delay(self.busy(0), attempt=recovery.MAX_ATTEMPTS, waited=0) is None

    @pytest.mark.parametrize("kind", ["quota", "model_unavailable", "auth", "request"])
    def test_no_retry_for_what_waiting_cant_fix(self, kind):
        trouble = recovery.Trouble(kind, 400, None, 1, None)
        assert recovery.next_delay(trouble, attempt=1, waited=0) is None


def relay_app(replies: list[httpx.Response], stopped=None):
    """A relay whose upstream answers with `replies`, in order."""
    sent: list[httpx.Request] = []
    statuses: list[tuple[str, dict]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return replies[min(len(sent), len(replies)) - 1]

    tokens = SessionTokens()
    token = tokens.issue(SessionAccess("c_1", "data", results_dir=Path(".")))
    app = FastAPI()
    app.include_router(
        build_relay_router(
            tokens,
            lambda: "sk-real",
            "https://umgpt.example/v1",
            httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
            on_status=lambda session, data: statuses.append((session, data)),
            stopped=stopped,
        )
    )
    client = TestClient(app)

    def call() -> httpx.Response:
        return client.post(
            "/relay/v1/responses",
            headers={"Authorization": f"Bearer {token}"},
            content=json.dumps(codex_request()),
        )

    return call, sent, statuses


OK = httpx.Response(
    200, content=b"event: done\ndata: {}\n\n", headers={"content-type": "text/event-stream"}
)


def test_a_busy_service_is_retried_after_its_wait_and_the_session_is_told():
    busy = httpx.Response(
        429, content=error_body("rate_limit_exceeded"), headers={"retry-after": "0"}
    )
    call, sent, statuses = relay_app([busy, OK])
    response = call()
    assert response.status_code == 200
    assert b"event: done" in response.content
    # The same checked request, sent twice.
    assert len(sent) == 2 and sent[0].content == sent[1].content
    assert [(s, d["state"]) for s, d in statuses] == [("c_1", "retrying"), ("c_1", "recovered")]
    assert statuses[0][1]["kind"] == "busy" and statuses[0][1]["model"] == "gpt-5.5"


def test_a_used_up_allowance_isnt_retried():
    quota = httpx.Response(429, content=error_body("insufficient_quota"))
    call, sent, statuses = relay_app([quota, OK])
    response = call()
    assert response.status_code == 429
    assert len(sent) == 1
    assert statuses[-1][1]["state"] == "failed" and statuses[-1][1]["kind"] == "quota"


def test_the_error_and_the_servers_wait_reach_codex_when_retries_run_out(monkeypatch):
    monkeypatch.setattr(recovery, "FIRST_DELAY", 0.01)
    busy = httpx.Response(503, content=error_body("server_busy"), headers={"retry-after-ms": "10"})
    call, sent, statuses = relay_app([busy])
    response = call()
    assert response.status_code == 503
    assert response.headers["retry-after-ms"] == "10"
    assert len(sent) == recovery.MAX_ATTEMPTS
    assert [d["state"] for _, d in statuses] == ["retrying", "retrying", "failed"]


def test_nothing_is_retried_once_codex_has_stopped_waiting():
    from datalab.relay import _wait_unless_gone

    class Gone:
        async def is_disconnected(self) -> bool:
            return True

    assert asyncio.run(_wait_unless_gone(Gone(), 30)) is False  # returns at once


def test_nothing_is_retried_after_the_person_presses_stop():
    """Stop may not close Codex's connection through the gateway, so the
    relay also asks the session whether its turn was stopped."""
    busy = httpx.Response(
        429, content=error_body("rate_limit_exceeded"), headers={"retry-after": "1"}
    )
    call, sent, statuses = relay_app([busy, OK], stopped=lambda session: session == "c_1")
    response = call()
    assert response.status_code == 429
    assert len(sent) == 1
    assert [d["state"] for _, d in statuses] == ["retrying"]


def test_a_server_wait_means_busy_even_with_a_quota_code():
    headers = httpx.Headers({"retry-after": "20"})
    assert recovery.classify(429, headers, error_body("insufficient_quota")).kind == "busy"
