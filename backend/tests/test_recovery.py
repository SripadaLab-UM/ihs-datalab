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

    def test_never_again_at_once(self, monkeypatch):
        # "Retry-After: 0" (or none) still waits a moment.
        assert recovery.next_delay(self.busy(0), attempt=1, waited=0) == recovery.MIN_DELAY
        monkeypatch.setattr(recovery, "FIRST_DELAY", 0.01)
        assert recovery.next_delay(self.busy(), attempt=1, waited=0) == recovery.MIN_DELAY

    def test_bounded_attempts(self):
        assert recovery.next_delay(self.busy(0), attempt=recovery.MAX_ATTEMPTS, waited=0) is None

    @pytest.mark.parametrize("kind", ["quota", "model_unavailable", "auth", "request"])
    def test_no_retry_for_what_waiting_cant_fix(self, kind):
        trouble = recovery.Trouble(kind, 400, None, 1, None)
        assert recovery.next_delay(trouble, attempt=1, waited=0) is None


@pytest.fixture
def quick(monkeypatch):
    """Retries without the real waits."""
    monkeypatch.setattr(recovery, "MIN_DELAY", 0.01)
    monkeypatch.setattr(recovery, "FIRST_DELAY", 0.01)


def relay_app(
    replies: list[httpx.Response | httpx.HTTPError],
    *,
    watch_turn=None,
    during=None,
    tokens: SessionTokens | None = None,
    session: str = "c_1",
):
    """A relay whose upstream answers with `replies`, in order (an error is
    raised, as a connection failure would be). `during(data)` runs as each
    model status is reported, before any wait."""
    sent: list[httpx.Request] = []
    statuses: list[tuple[str, dict]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        reply = replies[min(len(sent), len(replies)) - 1]
        if isinstance(reply, httpx.HTTPError):
            raise reply
        return reply

    def on_status(session_id: str, data: dict) -> None:
        statuses.append((session_id, data))
        if during is not None:
            during(data)

    tokens = tokens or SessionTokens()
    token = tokens.issue(SessionAccess(session, "data", results_dir=Path(".")))
    app = FastAPI()
    app.include_router(
        build_relay_router(
            tokens,
            lambda: "sk-real",
            "https://umgpt.example/v1",
            httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
            on_status=on_status,
            watch_turn=watch_turn,
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


def busy_for(seconds: str = "1") -> httpx.Response:
    return httpx.Response(
        429, content=error_body("rate_limit_exceeded"), headers={"retry-after": seconds}
    )


def test_a_busy_service_is_retried_after_its_wait_and_the_session_is_told(quick):
    call, sent, statuses = relay_app([busy_for("0"), OK])
    response = call()
    assert response.status_code == 200
    assert b"event: done" in response.content
    # The same checked request, sent twice.
    assert len(sent) == 2 and sent[0].content == sent[1].content
    assert [(s, d["state"]) for s, d in statuses] == [("c_1", "retrying"), ("c_1", "recovered")]
    assert statuses[0][1]["kind"] == "busy" and statuses[0][1]["model"] == "gpt-5.5"


def test_a_retry_is_the_same_request_and_the_key_stays_out_of_the_status(quick):
    call, sent, statuses = relay_app([busy_for("0"), OK])
    assert call().status_code == 200
    assert len(sent) == 2
    assert sent[0].url == sent[1].url == "https://umgpt.example/v1/responses"
    assert sent[0].content == sent[1].content
    assert all(r.headers["authorization"] == "Bearer sk-real" for r in sent)
    # What the chat is told has nothing secret in it.
    shown = json.dumps(statuses)
    assert "sk-real" not in shown and "dls_" not in shown


def test_a_connection_failure_is_retried(quick):
    call, sent, statuses = relay_app([httpx.ConnectError("refused"), OK])
    response = call()
    assert response.status_code == 200
    assert len(sent) == 2 and sent[0].content == sent[1].content
    assert [d["state"] for _, d in statuses] == ["retrying", "recovered"]
    assert statuses[0][1]["kind"] == "connection" and statuses[0][1]["code"] == "ConnectError"


def test_a_used_up_allowance_isnt_retried():
    quota = httpx.Response(429, content=error_body("insufficient_quota"))
    call, sent, statuses = relay_app([quota, OK])
    response = call()
    assert response.status_code == 429
    assert len(sent) == 1
    assert statuses[-1][1]["state"] == "failed" and statuses[-1][1]["kind"] == "quota"


def test_the_error_and_the_servers_wait_reach_codex_when_retries_run_out(quick):
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


class Turns:
    """Stands in for the session manager's view of one conversation's turns."""

    def __init__(self) -> None:
        self.stopped = False
        self.number = 1
        self.shut_down = False

    def watch_turn(self, session_id: str):
        number = self.number
        return lambda: self.shut_down or self.stopped or self.number != number

    def stop(self) -> None:
        self.stopped = True

    def begin_turn(self) -> None:
        self.stopped = False
        self.number += 1


def test_nothing_is_retried_after_the_person_presses_stop():
    """Stop may not close Codex's connection through the gateway, so the
    relay also asks the session whether its turn was stopped. The wait ends
    at once, and nothing more is sent."""
    turns = Turns()
    call, sent, statuses = relay_app(
        [busy_for("30"), OK], watch_turn=turns.watch_turn, during=lambda _: turns.stop()
    )
    response = call()
    assert response.status_code == 429
    assert len(sent) == 1
    assert [d["state"] for _, d in statuses] == ["retrying"]


def test_nothing_is_sent_for_a_turn_already_stopped():
    turns = Turns()
    turns.stop()
    call, sent, statuses = relay_app([OK], watch_turn=turns.watch_turn)
    response = call()
    assert response.status_code == 409 and "turn is over" in response.text
    assert sent == [] and statuses == []


def test_a_new_turn_after_stop_doesnt_revive_the_old_retries():
    """Stop, then a new message, both during the wait: beginning the new turn
    clears Stop, but the old request still belongs to the stopped turn."""
    turns = Turns()

    def stop_and_start_again(data: dict) -> None:
        if data["state"] == "retrying":
            turns.stop()
            turns.begin_turn()

    call, sent, _ = relay_app(
        [busy_for("30"), OK], watch_turn=turns.watch_turn, during=stop_and_start_again
    )
    assert call().status_code == 429
    assert len(sent) == 1
    # The new turn's own requests go through.
    assert call().status_code == 200 and len(sent) == 2


def test_a_revoked_token_during_a_wait_stops_the_retries():
    """Revoking a session's token is its kill switch: a helper cancelled, or
    a conversation closed. Nothing more is sent for it."""
    tokens = SessionTokens()
    call, sent, statuses = relay_app(
        [busy_for("30"), OK], tokens=tokens, during=lambda _: tokens.revoke_session("c_1")
    )
    assert call().status_code == 429
    assert len(sent) == 1
    assert [d["state"] for _, d in statuses] == ["retrying"]


def test_a_conversation_deleted_during_a_wait_stops_the_retries(settings):
    """A deleted, shut-down or reaped conversation has no runtime any more:
    the manager's check says its turn is over, even with the token still
    valid (it's revoked too, as the runtime closes)."""
    from datalab.sessions.manager import SessionManager

    class Runtime:
        stop_requested = False
        turn_number = 1

    tokens = SessionTokens()
    manager = SessionManager(settings, None, tokens)  # type: ignore[arg-type]  # no store needed
    manager._runtimes["c_1"] = Runtime()  # type: ignore[assignment]
    call, sent, _ = relay_app(
        [busy_for("30"), OK],
        tokens=tokens,
        watch_turn=manager.watch_turn,
        during=lambda _: manager._runtimes.pop("c_1"),
    )
    assert call().status_code == 429
    assert len(sent) == 1


def test_the_managers_check_follows_the_turn_the_request_arrived_in(settings):
    from datalab.sessions.manager import SessionManager

    class Runtime:
        stop_requested = False
        turn_number = 1

    manager = SessionManager(settings, None, SessionTokens())  # type: ignore[arg-type]
    runtime = Runtime()
    manager._runtimes["c_1"] = runtime  # type: ignore[assignment]
    over = manager.watch_turn("c_1")
    assert not over()
    runtime.stop_requested = True
    assert over()
    # A new turn clears Stop; the old request's turn is still over.
    runtime.stop_requested, runtime.turn_number = False, 2
    assert over() and not manager.watch_turn("c_1")()
    # Sessions without a runtime here (helpers) rely on their token.
    assert not manager.watch_turn("h_1")()


def test_a_real_runtimes_turns_decide_what_the_relay_sends(settings, tmp_path):
    """The smallest real path: a SessionRuntime's own begin_turn and
    stop_turn, the manager's watch_turn, and the relay."""
    from datalab.sessions.manager import SessionManager
    from tests.test_runtime import FakeContainers, make

    tokens = SessionTokens()
    runtime, _ = make(tmp_path, FakeContainers(tmp_path / "log.jsonl"), tokens)
    manager = SessionManager(settings, None, tokens)  # type: ignore[arg-type]  # no store needed
    manager._runtimes[runtime.session_id] = runtime
    runtime.begin_turn()
    call, sent, _ = relay_app(
        [busy_for("30"), OK],
        tokens=tokens,
        session=runtime.session_id,
        watch_turn=manager.watch_turn,
        during=lambda _: runtime.begin_turn(),  # a new turn, while the old request waits
    )
    assert call().status_code == 429 and len(sent) == 1  # the old turn's: not sent again
    assert call().status_code == 200 and len(sent) == 2  # the current turn's goes through
    asyncio.run(runtime.stop_turn())
    assert call().status_code == 409 and len(sent) == 2  # stopped: nothing sent
    runtime.begin_turn()
    assert call().status_code == 200 and len(sent) == 3


def test_a_server_wait_means_busy_even_with_a_quota_code():
    headers = httpx.Headers({"retry-after": "20"})
    assert recovery.classify(429, headers, error_body("insufficient_quota")).kind == "busy"
