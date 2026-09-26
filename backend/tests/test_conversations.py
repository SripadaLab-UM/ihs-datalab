"""Conversations API, manager, and event log, with a fake Codex runtime."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions.runtime import TurnResult
from tests.conftest import FakeDatabase, live_server


class FakeRuntime:
    """Replays a scripted turn instead of running Codex in Docker."""

    def __init__(self, emit, *, hold: asyncio.Event | None = None) -> None:
        self.emit = emit
        self.hold = hold
        self.sent: list[str] = []
        self.stopped = False

    async def send(self, text: str, *, effort: str | None = None) -> TurnResult:
        self.sent.append(text)
        await self.emit("turn_started", {"turn_id": "t1"})
        await self.emit("answer_delta", {"text": "Hello"})
        if self.hold:
            await self.hold.wait()
        status = "interrupted" if self.stopped else "completed"
        await self.emit("turn_finished", {"turn_id": "t1", "status": status})
        return TurnResult("t1", status)

    async def stop_turn(self) -> None:
        self.stopped = True
        if self.hold:
            self.hold.set()

    async def close(self) -> None:
        pass


@pytest.fixture
def app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


def use_fake_runtime(app, hold: asyncio.Event | None = None) -> list[FakeRuntime]:
    made: list[FakeRuntime] = []
    manager = app.state.services.sessions
    store = app.state.services.conversations

    def fake(conversation):
        async def emit(kind, data):
            store.append(conversation.id, kind, data)

        runtime = FakeRuntime(emit, hold=hold)
        made.append(runtime)
        manager._runtimes[conversation.id] = runtime
        return runtime

    manager._runtime = fake
    return made


def wait_for(client, conversation_id: str, event_type: str, timeout: float = 5) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/api/conversations/{conversation_id}/events").json()
        if any(e["type"] == event_type for e in events):
            return events
        time.sleep(0.02)
    raise AssertionError(f"no {event_type} event")


def test_modes_set_the_session_kind(app):
    with TestClient(app) as client:
        data = client.post("/api/conversations", json={"mode": "analysis"}).json()
        research = client.post("/api/conversations", json={"mode": "research"}).json()
        assert (data["kind"], research["kind"]) == ("data", "research")
        assert client.post("/api/conversations", json={"mode": "nope"}).status_code == 422
        assert {c["id"] for c in client.get("/api/conversations").json()} == {
            data["id"],
            research["id"],
        }


def test_a_message_runs_a_turn_and_logs_its_events(app):
    made = use_fake_runtime(app)
    with TestClient(app) as client:
        conversation = client.post("/api/conversations", json={}).json()
        cid = conversation["id"]
        assert (
            client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"}).status_code
            == 202
        )
        events = wait_for(client, cid, "turn_finished")
    assert [e["type"] for e in events] == [
        "user_message",
        "turn_started",
        "answer_delta",
        "turn_finished",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert made[0].sent == ["hi"]


def test_only_one_message_at_a_time(app):
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "first"})
        wait_for(client, cid, "answer_delta")
        second = client.post(f"/api/conversations/{cid}/messages", json={"text": "second"})
        assert second.status_code == 409
        client.post(f"/api/conversations/{cid}/stop")
        events = wait_for(client, cid, "turn_finished")
    assert events[-1]["data"]["status"] == "interrupted"
    assert "stop_requested" in [e["type"] for e in events]


def test_events_can_be_read_from_any_point(app):
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        store = app.state.services.conversations
        for i in range(5):
            store.append(cid, "answer_delta", {"text": str(i)})
        later = client.get(f"/api/conversations/{cid}/events", params={"after": 3}).json()
    assert [e["data"]["text"] for e in later] == ["3", "4"]


async def test_waiting_for_events_wakes_on_append(app):
    store = app.state.services.conversations
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    waiter = asyncio.create_task(store.wait_for_events(conversation.id, 0, timeout=5))
    await asyncio.sleep(0.05)
    store.append(conversation.id, "answer_delta", {"text": "x"})
    await asyncio.wait_for(waiter, 1)  # woke up well before the 5 s timeout


async def test_waiting_returns_at_once_if_events_already_exist(app):
    store = app.state.services.conversations
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    store.append(conversation.id, "answer_delta", {"text": "x"})
    await asyncio.wait_for(store.wait_for_events(conversation.id, 0, timeout=5), 0.5)


def test_stream_replays_then_follows(app):
    store = app.state.services.conversations
    cid = store.create(kind="data", mode="analysis", title="t", model="m").id
    store.append(cid, "answer_delta", {"text": "a"})
    store.append(cid, "answer_delta", {"text": "b"})
    with (
        live_server(app) as base_url,
        httpx.stream(
            "GET", f"{base_url}/api/conversations/{cid}/stream", params={"after": 1}, timeout=10
        ) as response,
    ):
        assert response.headers["content-type"].startswith("text/event-stream")
        payloads = (
            json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
        )
        first = next(payloads)
    assert first["seq"] == 2 and first["data"]["text"] == "b"


def test_deleting_removes_the_conversation(app, monkeypatch):
    removed = []

    async def fake_delete(conversation_id):
        removed.append(conversation_id)
        app.state.services.conversations.delete(conversation_id)

    monkeypatch.setattr(app.state.services.sessions, "delete", fake_delete)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        assert client.delete(f"/api/conversations/{cid}").status_code == 204
        assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert removed == [cid]
