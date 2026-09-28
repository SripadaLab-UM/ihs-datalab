"""/api refuses JSON with a lone surrogate (`"\\ud800"`) with a 422, never a 500."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from datalab.api.textguard import MAX_BODY_BYTES, RefuseNonText
from datalab.app import create_app
from tests.conftest import FakeDatabase
from tests.test_workflow_runner import WEEKLY


@pytest.fixture
def client(settings, catalog):
    app = create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)
    with TestClient(app) as client:
        client.get(app.state.browser.sign_in_path())
        yield client


def send(client: TestClient, method: str, url: str, body: bytes | str, **headers: str):
    """Written out by hand: httpx's own JSON encoding fails on a lone surrogate."""
    headers = {"content-type": "application/json", **headers}
    return client.request(method, url, content=body, headers=headers)


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        # Before, a 500: FastAPI's 422 echoed the text back and couldn't write it.
        ("POST", "/api/workflows/validate", {"text": WEEKLY + "# \ud800\n"}),
        ("POST", "/api/workflows/saves", {"text": WEEKLY.replace("weekly", "weekly\udfff")}),
        ("POST", "/api/workflows/drafts/check", {"text": "\ud800"}),
        # Plain str fields, which Pydantic lets a surrogate into.
        ("POST", "/api/workflows/validate", {"path": "x\ud800.yaml"}),
        ("POST", "/api/workflows/runs", {"path": "w.yaml", "params": {"since": "\ud800"}}),
        ("POST", "/api/workflows/drafts", {"name": "x", "description": "\ud800", "queries": []}),
        ("PUT", "/api/knowledge/proposals/p/edits", {"files": {"qc/a.md": "\ud800"}}),
        # In a key, deep down.
        ("PUT", "/api/knowledge/proposals/p/edits", {"files": {"qc/\udc00.md": None}}),
    ],
)
def test_a_lone_surrogate_anywhere_in_the_json_is_refused(client, method, url, body):
    refused = send(client, method, url, json.dumps(body))
    assert refused.status_code == 422, refused.text
    assert set(refused.json()) == {"detail"}
    assert "isn't text (U+D" in refused.json()["detail"]
    assert "\\ud" not in refused.text  # nothing sent back


def test_written_raw_or_without_a_content_type_it_is_refused_too(client):
    raw = b'{"text": "a: \xed\xa0\x80"}'  # a surrogate UTF-8 can't have, as json reads it
    assert send(client, "POST", "/api/workflows/validate", raw).status_code == 422
    # Anything but application/json is refused before its body is read
    # (web.refuse_cross_site: what another local page could send).
    bare = client.post("/api/workflows/validate", content=json.dumps({"text": "\ud800"}))
    assert bare.status_code == 403 and "JSON requests only" in bare.json()["detail"]
    json_ld = send(
        client, "POST", "/api/workflows/validate", json.dumps({"text": "\ud800"}),
        **{"content-type": "application/merge-patch+json"},
    )  # fmt: skip
    assert json_ld.status_code == 403


def test_other_json_gets_through_whole(client):
    ok = send(client, "POST", "/api/workflows/validate", json.dumps({"text": WEEKLY}))
    assert ok.status_code == 200 and ok.json()["valid"] is True
    # A whole pair is one character; not JSON at all is FastAPI's to say.
    emoji = send(client, "POST", "/api/workflows/validate", json.dumps({"text": "# \U0001f600\n"}))
    assert emoji.status_code == 200
    broken = send(client, "POST", "/api/workflows/validate", b'{"text": ')
    assert broken.status_code == 422 and broken.json()["detail"][0]["type"] == "json_invalid"


def test_signing_in_is_checked_first(settings, catalog):
    app = create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)
    with TestClient(app) as client:
        refused = send(client, "POST", "/api/workflows/validate", json.dumps({"text": "\ud800"}))
        # /api/health needs no sign-in, so its body is never read here: a
        # POST as a cross-origin form can send (no JSON content type) is
        # refused unread, however large or odd the body; as JSON, it's the
        # route's own 405.
        big = b'{"a": "\\ud800", "b": "' + b"x" * (MAX_BODY_BYTES + 1) + b'"}'
        health = client.post("/api/health", content=big)
        as_json = send(client, "POST", "/api/health", big)
    assert refused.status_code == 401
    assert health.status_code == 403
    assert as_json.status_code == 405


def test_the_body_guard_reads_only_signed_in_requests():
    # Not the flag ApiProtection sets: passed through unread.
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        pass

    guard = RefuseNonText(app)
    scope = {"type": "http", "path": "/api/x", "method": "POST", "headers": [], "state": {}}
    asyncio.run(guard(scope, receive, send))
    assert seen == ["/api/x"]


def test_a_body_over_the_limit_is_refused_unread(client):
    big = b'{"text": "' + b"x" * MAX_BODY_BYTES + b'"}'
    declared = send(client, "POST", "/api/workflows/validate", big)
    assert declared.status_code == 413
    assert declared.json() == {"detail": "The request is larger than 16 MB."}

    def chunks():  # no content-length: the count is kept as it's read
        yield b'{"text": "'
        for _ in range(17):
            yield b"x" * 1024**2
        yield b'"}'

    streamed = send(client, "POST", "/api/workflows/validate", chunks())  # type: ignore[arg-type]
    assert streamed.status_code == 413
    # Under the limit it's read as usual.
    under = send(client, "POST", "/api/workflows/validate", b'{"text": "' + b"x" * 1000 + b'"}')
    assert under.status_code == 200
