"""Conversations API, manager, and event log, with a fake Codex runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.credentials import MissingCredential
from datalab.sessions.runtime import TurnResult
from tests.conftest import FakeDatabase, live_server


class FakeContainers:
    def __init__(self) -> None:
        self.paused = 0

    async def pause(self) -> bool:
        self.paused += 1
        return True

    async def unpause(self) -> None:
        pass


class FakeRuntime:
    """Replays a scripted turn instead of running Codex in Docker."""

    def __init__(
        self, emit, *, hold: asyncio.Event | None = None, outcomes: list[str] | None = None
    ) -> None:
        self.emit = emit
        self.hold = hold
        self.outcomes = outcomes if outcomes is not None else []  # each turn's; then completed
        self.sent: list[str] = []
        self.stopped = False
        self.containers = FakeContainers()
        self.reviews: list[str] = []
        self.evidence = ["rows: 12"]

    def begin_turn(self) -> None:
        pass

    async def send(self, text: str, *, effort: str | None = None) -> TurnResult:
        self.sent.append(text)
        await self.emit("turn_started", {"turn_id": "t1"})
        await self.emit("answer_delta", {"text": "Hello"})
        if self.hold:
            await self.hold.wait()
        status = "interrupted" if self.stopped else (self.outcomes or ["completed"]).pop(0)
        await self.emit("turn_finished", {"turn_id": "t1", "status": status})
        return TurnResult("t1", status)

    def take_evidence(self) -> list[str]:
        # The turn did some work; its review ran nothing.
        evidence, self.evidence = self.evidence, []
        return evidence

    def ran_commands(self) -> bool:
        return False

    @property
    def stop_requested(self) -> bool:
        return self.stopped

    async def review(self, instructions: str) -> TurnResult:
        self.reviews.append(instructions)
        await self.emit("review", {"id": "r1", "text": "1. Traced claims: yes."})
        return TurnResult("t2", "completed")

    async def stop_turn(self) -> None:
        self.stopped = True
        if self.hold:
            self.hold.set()

    async def close(self) -> None:
        pass


def no_key() -> str:
    raise MissingCredential("no key in tests")  # and no calls to U-M GPT


@pytest.fixture
def app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
        model_key=no_key,
    )


def use_fake_runtime(
    app, hold: asyncio.Event | None = None, outcomes: list[str] | None = None
) -> list[FakeRuntime]:
    made: list[FakeRuntime] = []
    manager = app.state.services.sessions
    store = app.state.services.conversations

    def fake(conversation):
        async def emit(kind, data):
            store.append(conversation.id, kind, data)

        runtime = FakeRuntime(emit, hold=hold, outcomes=outcomes)
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
        events = wait_for(client, cid, "turn_done")
        assert client.get(f"/api/conversations/{cid}").json()["busy"] is False
    # An Analysis conversation: the rigor review runs after the answer. (The
    # title is written alongside, whenever it's ready: see the next test.)
    assert [e["type"] for e in events if e["type"] != "title_changed"] == [
        "user_message",
        "turn_started",
        "answer_delta",
        "turn_finished",
        "checkpoint",
        "review_started",
        "review",
        "review_finished",
        "turn_done",
    ]
    assert made[0].sent == ["hi"]
    assert "Traced claims" in made[0].reviews[0]
    # Review mode has no history, so the question is passed in.
    assert "<question>\nhi\n</question>" in made[0].reviews[0]
    assert made[0].containers.paused == 1  # frozen while the files were saved


def test_the_first_question_names_the_conversation(app):
    """No key in tests, so the title is the question's first words; with one,
    the model writes it (tests/test_titles.py)."""
    use_fake_runtime(app)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "extraction"}).json()["id"]
        assert client.get(f"/api/conversations/{cid}").json()["title"] == "New conversation"
        client.post(
            f"/api/conversations/{cid}/messages",
            json={"text": "Which tables hold PHQ-9 scores? And how do they differ?"},
        )
        events = wait_for(client, cid, "title_changed")
        assert client.get(f"/api/conversations/{cid}").json()["title"] == (
            "Which tables hold PHQ-9 scores"
        )
        wait_for(client, cid, "turn_done")
        # Later questions don't rename it; the person can.
        client.post(f"/api/conversations/{cid}/messages", json={"text": "Now for 2026 only"})
        time.sleep(0.2)  # time enough for a title to be (wrongly) written
        assert client.get(f"/api/conversations/{cid}").json()["title"] == (
            "Which tables hold PHQ-9 scores"
        )
        renamed = client.patch(f"/api/conversations/{cid}", json={"title": "  PHQ-9   tables "})
        assert renamed.json()["title"] == "PHQ-9 tables"
        assert client.patch(f"/api/conversations/{cid}", json={"title": ""}).status_code == 422
    assert [e["data"]["title"] for e in events if e["type"] == "title_changed"] == [
        "Which tables hold PHQ-9 scores"
    ]


def test_typed_titles_are_one_line_of_visible_text(app):
    """Creating and renaming share one rule: no line breaks, control
    characters, or bidi overrides, and not blank."""
    with TestClient(app) as client:
        made = client.post("/api/conversations", json={"title": " Sleep\n\u202epilot\u202c\x07 "})
        assert made.status_code == 201 and made.json()["title"] == "Sleep pilot"
        assert client.post("/api/conversations", json={"title": " \n\u2066 "}).status_code == 422
        cid = made.json()["id"]
        renamed = client.patch(
            f"/api/conversations/{cid}", json={"title": "Mood\u2067\r\nscores\u2069"}
        )
        assert renamed.json()["title"] == "Mood scores"
        blank = client.patch(f"/api/conversations/{cid}", json={"title": "\u202e"})
        assert blank.status_code == 422
        # Left out, it's the default until the first question names it.
        assert client.post("/api/conversations", json={}).json()["title"] == "New conversation"


def test_a_name_chosen_before_the_title_arrives_wins(app):
    store = app.state.services.conversations
    conversation = store.create(kind="data", mode="analysis", title="New conversation", model="m")
    store.rename(conversation.id, "My name")
    assert not store.rename_if(conversation.id, "Model title", current="New conversation")
    assert store.get(conversation.id).title == "My name"


def test_renaming_tells_other_windows_and_refuses_a_blank_title(app):
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        assert client.patch(f"/api/conversations/{cid}", json={"title": "   "}).status_code == 422
        client.patch(f"/api/conversations/{cid}", json={"title": "Sleep pilot"})
        events = client.get(f"/api/conversations/{cid}/events").json()
    assert [e["data"] for e in events if e["type"] == "title_changed"] == [{"title": "Sleep pilot"}]


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
    # turn_done may already follow it: look for the turn's own end.
    finished = [e for e in events if e["type"] == "turn_finished"]
    assert finished[-1]["data"]["status"] == "interrupted"
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


def test_deleting_a_busy_conversation_is_refused(app):
    """Deleting would stop the agent mid-turn: the person stops it first."""
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "answer_delta")
        refused = client.delete(f"/api/conversations/{cid}")
        assert refused.status_code == 409 and "Stop it first" in refused.json()["detail"]
        assert client.get(f"/api/conversations/{cid}").status_code == 200
        hold.set()
        wait_for(client, cid, "turn_done")


def test_conversations_use_only_approved_models(app):
    with TestClient(app) as client:
        assert client.post("/api/conversations", json={"model": "claude-opus-5"}).status_code == 422
        created = client.post("/api/conversations", json={"model": "gpt-5.4"}).json()
        assert created["model"] == "gpt-5.4"
        assert client.get("/api/models").json() == {"default": "gpt-5.5", "available": []}


def test_modes_come_with_descriptions_and_starters(app):
    with TestClient(app) as client:
        modes = {m["id"]: m for m in client.get("/api/modes").json()}
    assert set(modes) == {
        "analysis", "extraction", "engineering", "workflows", "knowledge", "research"
    }  # fmt: skip
    assert all(m["description"] and m["starters"] for m in modes.values())
    assert modes["research"]["kind"] == "research"
    # Docked by the Workflows and Knowledge tabs only; Knowledge writing can't query.
    assert {i for i, m in modes.items() if m["tab_only"]} == {"workflows", "knowledge"}
    assert {i for i, m in modes.items() if not m["queries"]} == {"knowledge"}


def test_a_conversations_mode_sets_its_sessions_data_access(app):
    """The manager gives each session its mode's instructions and data access."""
    from datalab.sessions.modes import CATALOG_TOOLS, DATA_TOOLS

    with TestClient(app) as client:
        made = {
            mode: client.post("/api/conversations", json={"mode": mode}).json()
            for mode in ("knowledge", "workflows")
        }
        assert all(m["kind"] == "data" and not m["rigor_review"] for m in made.values())
        sessions = app.state.services.sessions
        store = app.state.services.conversations
        knowledge = sessions._runtime(store.get(made["knowledge"]["id"]))
        workflows = sessions._runtime(store.get(made["workflows"]["id"]))
    assert knowledge._tools == CATALOG_TOOLS
    assert set(knowledge._tools_off) == set(DATA_TOOLS) - CATALOG_TOOLS
    assert (workflows._tools, workflows._tools_off) == (None, ())
    assert "Knowledge writing mode" in knowledge._instructions
    assert "Workflow authoring mode" in workflows._instructions


def test_the_plan_card_gets_the_plan_types_and_sections(app):
    with TestClient(app) as client:
        schema = client.get("/api/plan-schema").json()
    kinds = {s["kind"] for s in schema["sections"]}
    assert schema["schema_version"] == 2 and len(schema["core"]) == 4
    assert {t["id"] for t in schema["types"]} == {
        "describe",
        "association",
        "prediction",
        "data_quality",
        "other",
    }
    assert all(set(t["required"]) <= kinds for t in schema["types"])


def test_the_rigor_review_can_be_switched_off(app):
    made = use_fake_runtime(app)
    with TestClient(app) as client:
        created = client.post("/api/conversations", json={"mode": "analysis"}).json()
        assert created["rigor_review"] is True
        extraction = client.post("/api/conversations", json={"mode": "extraction"}).json()
        assert extraction["rigor_review"] is False
        cid = created["id"]
        changed = client.patch(f"/api/conversations/{cid}", json={"rigor_review": False})
        assert changed.json()["rigor_review"] is False
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        events = wait_for(client, cid, "checkpoint")
    assert "review_started" not in [e["type"] for e in events]
    assert made[0].reviews == []


def test_numbers_in_an_answer_are_traced(app):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    store.append(conversation.id, "user_message", {"text": "how many?"})
    store.append(
        conversation.id,
        "answer",
        {"id": "m1", "phase": "final_answer", "text": "There were 20,592 days and 81 people."},
    )
    asyncio.run(manager._trace(conversation.id, 0, ['{"row_count": 20592} at 12:45:00']))
    [traced] = store.events_of_types_after(conversation.id, 0, ("trace",))
    assert traced.data == {"answer": "m1", "numbers": 2, "untraced": ["81"]}


def test_a_review_that_couldnt_finish_can_be_run_again_without_redoing_the_turn(app):
    made = use_fake_runtime(app)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        # Nothing to run again yet.
        assert client.post(f"/api/conversations/{cid}/review").status_code == 409
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "review_finished")
        wait_for(client, cid, "turn_done")
        runtime = made[0]
        first = runtime.reviews[0]

        # Say the model service was busy: the review failed.
        store = app.state.services.conversations
        store.append(cid, "review_started", {})
        store.append(cid, "review", {"id": "r2", "text": "partial"})
        store.append(cid, "review_finished", {"status": "failed"})

        assert client.post(f"/api/conversations/{cid}/review").status_code == 202
        deadline = time.time() + 5
        events: list[dict] = []
        while time.time() < deadline:
            events = client.get(f"/api/conversations/{cid}/events").json()
            if sum(e["type"] == "turn_done" for e in events) == 2:
                break
            time.sleep(0.02)
        rerun = made[-1]
        # The same answer and question, not the failed review's text; the turn
        # itself wasn't sent again.
        assert rerun.reviews == [first]
        assert len(runtime.sent) == 1 and (rerun is runtime or rerun.sent == [])
        assert [e["data"].get("status") for e in events if e["type"] == "review_finished"][-1] == (
            "completed"
        )
        # Finished now: nothing left to run again.
        assert client.post(f"/api/conversations/{cid}/review").status_code == 409


def test_continue_picks_up_in_the_same_thread_and_the_review_reads_the_original_question(app):
    from datalab.sessions.manager import CONTINUE_TEXT

    made = use_fake_runtime(app, outcomes=["failed"])
    store = app.state.services.conversations
    manager = app.state.services.sessions
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "turn_done")
        assert client.post(f"/api/conversations/{cid}/continue").status_code == 202
        deadline = time.time() + 5
        while time.time() < deadline:
            events = client.get(f"/api/conversations/{cid}/events").json()
            if sum(e["type"] == "turn_done" for e in events) == 2:
                break
            time.sleep(0.02)
        asked = [e for e in events if e["type"] == "user_message"]
        assert asked[-1]["data"] == {"text": CONTINUE_TEXT, "continues": True}
        assert CONTINUE_TEXT in made[-1].sent[-1]
        # The continued turn's review reads the question it picks up, not "please continue".
        last = store.last(cid, "user_message")
        assert manager._work_began(cid, last).data["text"] == "how many?"
        assert "<question>\nhow many?\n</question>" in made[-1].reviews[-1]


def test_a_review_cut_short_by_a_restart_can_be_run_again(app):
    use_fake_runtime(app)
    store = app.state.services.conversations
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "turn_done")
        # DataLab stopped mid-review: a start, and no finish.
        store.append(cid, "review_started", {})
        assert client.post(f"/api/conversations/{cid}/review").status_code == 202


def test_continue_needs_a_turn_that_failed(app):
    """The chat's rule for offering Continue, checked here too."""
    use_fake_runtime(app)
    store = app.state.services.conversations
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        # Nothing asked yet.
        refused = client.post(f"/api/conversations/{cid}/continue")
        assert refused.status_code == 409 and "nothing to continue" in refused.json()["detail"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "turn_done")
        # It finished: nothing to pick up.
        assert client.post(f"/api/conversations/{cid}/continue").status_code == 409
        # Stopped by the person: not failed.
        store.append(cid, "user_message", {"text": "and now?"})
        store.append(cid, "turn_finished", {"status": "interrupted"})
        assert client.post(f"/api/conversations/{cid}/continue").status_code == 409
        # Failed, but with a used-up allowance: waiting won't fix it.
        store.append(cid, "user_message", {"text": "and now?"})
        store.append(cid, "model_status", {"state": "failed", "kind": "quota"})
        store.append(cid, "turn_finished", {"status": "failed"})
        assert client.post(f"/api/conversations/{cid}/continue").status_code == 409


@pytest.mark.parametrize(
    ("events", "continuable"),
    [
        ([("model_status", {"state": "failed", "kind": "busy"})], True),
        ([("model_status", {"state": "failed", "kind": "auth"})], False),
        # The agent worked again after the trouble: the trouble is over.
        (
            [
                ("model_status", {"state": "failed", "kind": "quota"}),
                ("answer_delta", {"text": "x"}),
            ],
            True,
        ),
        ([("model_status", {"state": "recovered"})], True),
    ],
)
def test_continue_follows_the_turns_model_trouble(app, events, continuable):
    store = app.state.services.conversations
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    for kind, data in events:
        store.append(cid, kind, data)
    store.append(cid, "turn_finished", {"status": "failed"})
    assert app.state.services.sessions._last_turn_failed(cid) is continuable


def test_continue_reads_the_turn_not_its_review_or_later_notices(app):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    store.append(cid, "turn_finished", {"status": "completed"})
    # The review's own turn failed: the review didn't finish, the turn did.
    store.append(cid, "review_started", {})
    store.append(cid, "turn_finished", {"status": "failed"})
    store.append(cid, "review_finished", {"status": "failed"})
    assert not manager._last_turn_failed(cid)

    store.append(cid, "user_message", {"text": "and now?"})
    store.append(cid, "turn_finished", {"status": "failed"})
    assert manager._last_turn_failed(cid)
    # The chat shows an attachment as its own entry after the failed turn.
    store.append(cid, "input_attached", {"items": []})
    assert not manager._last_turn_failed(cid)


def test_continue_isnt_blocked_by_an_export(app):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    # Exported while the agent worked, while DataLab saved its checkpoint,
    # and after the turn: the chat shows each in the turn, which keeps its Continue.
    store.append(cid, "exported", {"folder": "/x", "files": 1})
    store.append(cid, "turn_finished", {"status": "failed"})
    store.append(cid, "exported", {"folder": "/x", "files": 1})
    store.append(cid, "turn_done", {})
    store.append(cid, "exported", {"folder": "/x", "files": 1})
    assert manager._last_turn_failed(cid)


def events_of(store, cid: str) -> list[tuple[str, dict]]:
    return [(e.type, e.data) for e in store.all_events_after(cid, 0)]


def logged(settings, cid: str) -> list[tuple[str, dict]]:
    """The events as logged, read after DataLab has closed its database."""
    with contextlib.closing(sqlite3.connect(settings.database_file)) as db:
        rows = db.execute(
            "SELECT type, data_json FROM events WHERE conversation_id = ? ORDER BY seq", (cid,)
        ).fetchall()
    return [(kind, json.loads(data)) for kind, data in rows]


CLOSED = ("notice", {"text": "DataLab closed while the agent was working."})


def test_a_turn_cut_off_by_a_restart_is_ended_at_startup(app, settings):
    store = app.state.services.conversations
    cut_off = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cut_off, "user_message", {"text": "how many?"})
    store.append(cut_off, "command_started", {"id": "c1", "command": "Rscript a.R"})
    finished = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(finished, "user_message", {"text": "how many?"})
    store.append(finished, "turn_finished", {"status": "completed"})
    store.append(finished, "turn_done", {})
    with TestClient(app):
        pass
    assert logged(settings, cut_off)[2:] == [
        CLOSED,
        ("turn_finished", {"status": "interrupted"}),
        ("turn_done", {}),
    ]
    assert len(logged(settings, finished)) == 3  # nothing to end


def test_a_turn_is_ended_only_once_and_like_a_stopped_one(app):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    manager.end_cut_off_turns()
    manager.end_cut_off_turns()
    assert [kind for kind, _ in events_of(store, cid)] == [
        "user_message", "notice", "turn_finished", "turn_done"
    ]  # fmt: skip
    # As after Stop, Continue isn't offered for it.
    assert not manager._last_turn_failed(cid)


def test_a_turn_cut_off_after_it_finished_keeps_its_end(app):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    store.append(cid, "turn_finished", {"status": "failed"})
    # Cut off while DataLab saved the checkpoint.
    manager.end_cut_off_turns()
    assert events_of(store, cid)[-2:] == [
        ("turn_finished", {"status": "failed"}),
        ("turn_done", {}),
    ]
    assert manager._last_turn_failed(cid)


@pytest.mark.parametrize("review_turn_finished", [False, True])
def test_a_review_cut_off_by_a_restart_is_ended_but_not_the_answer(app, review_turn_finished):
    store = app.state.services.conversations
    manager = app.state.services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="gpt-5.5").id
    store.append(cid, "user_message", {"text": "how many?"})
    store.append(cid, "turn_finished", {"status": "completed"})
    store.append(cid, "review_started", {})
    store.append(cid, "turn_started", {})
    if review_turn_finished:
        store.append(cid, "turn_finished", {"status": "completed"})
    manager.end_cut_off_turns()
    added = events_of(store, cid)[4 + review_turn_finished :]
    # The review's own turn is ended, not the answer's: that one finished.
    ended = [] if review_turn_finished else [("turn_finished", {"status": "interrupted"})]
    assert added == [*ended, ("turn_done", {})]


def test_a_turn_cut_off_by_shutdown_says_so_once(app, settings):
    hold = asyncio.Event()  # never set: the turn is still going when DataLab closes
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "answer_delta")
    ends = [e for e in logged(settings, cid) if e[0] in ("notice", "turn_finished", "turn_done")]
    assert ends == [CLOSED, ("turn_finished", {"status": "interrupted"}), ("turn_done", {})]


def test_a_stopped_turn_isnt_ended_twice_at_shutdown(app, settings):
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "answer_delta")
        client.post(f"/api/conversations/{cid}/stop")
        wait_for(client, cid, "turn_done")
    ends = [e[0] for e in logged(settings, cid) if e[0] in ("turn_finished", "turn_done")]
    assert ends == ["turn_finished", "turn_done"]


def test_continue_while_busy_is_refused(app):
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold, outcomes=["failed"])
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "answer_delta")
        refused = client.post(f"/api/conversations/{cid}/continue")
        assert refused.status_code == 409 and "still working" in refused.json()["detail"]
        hold.set()
        wait_for(client, cid, "turn_done")


def test_a_review_needs_an_approved_model_and_the_review_switched_on(app):
    use_fake_runtime(app)
    store = app.state.services.conversations
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "how many?"})
        wait_for(client, cid, "turn_done")
        store.append(cid, "review_started", {})  # cut short: it could be run again

        client.patch(f"/api/conversations/{cid}", json={"rigor_review": False})
        refused = client.post(f"/api/conversations/{cid}/review")
        assert refused.status_code == 409 and "switched off" in refused.json()["detail"]

        # A model that was approved when the conversation began, and isn't now.
        old = store.create(kind="data", mode="analysis", title="t", model="claude-opus-5")
        store.append(old.id, "user_message", {"text": "how many?"})
        store.append(old.id, "review_started", {})
        refused = client.post(f"/api/conversations/{old.id}/review")
        assert refused.status_code == 409 and "isn't approved" in refused.json()["detail"]


def open_plan_approval(client, app):
    """A conversation with a plan waiting for the person, as mid-turn."""
    from datalab.sessions.plan_schema import clean_plan
    from tests.test_plans import plan

    conversation = client.post("/api/conversations", json={"mode": "analysis"}).json()
    proposed = clean_plan(plan("describe", {"measures": "Sleep minutes."}))

    async def open_it():
        return app.state.services.sessions._approvals.open(
            conversation["id"], kind="analysis_plan", plan=proposed
        )

    pending = client.portal.call(open_it)
    return f"/api/conversations/{conversation['id']}/approvals/{pending.id}", proposed, pending


def test_a_plan_edit_that_isnt_valid_is_refused_and_the_plan_stays_waiting(app):
    with TestClient(app) as client:
        url, proposed, pending = open_plan_approval(client, app)
        emptied = {**proposed, "sections": proposed["sections"][1:]}  # no question
        refused = client.post(url, json={"approve": True, "plan": emptied})
        assert refused.status_code == 422 and "Question and purpose" in refused.json()["detail"]
        unknown = client.post(url, json={"approve": True, "plan": {**proposed, "secret": "x"}})
        assert unknown.status_code == 422 and "no part called 'secret'" in unknown.json()["detail"]
        huge = {**proposed, "rationale": "x" * 200_000}
        assert client.post(url, json={"approve": True, "plan": huge}).status_code == 422
        assert not pending.decision.done()
        # Then approved as it was proposed.
        assert client.post(url, json={"approve": True, "plan": proposed}).status_code == 204
        assert pending.decision.done() and pending.decision.result()[0] is True


def test_a_plan_sent_back_as_another_type_over_http(app):
    with TestClient(app) as client:
        url, proposed, pending = open_plan_approval(client, app)
        both = {"approve": True, "plan": proposed, "change_type": "prediction"}
        assert client.post(url, json=both).status_code == 422
        assert not pending.decision.done()
        # A type that doesn't exist isn't a request; the "no" still counts.
        assert client.post(url, json={"approve": False, "change_type": "causal"}).status_code == 204
        assert pending.decision.result() == (False, "")


def test_an_approved_plans_answer_carries_the_plan_as_approved(app):
    with TestClient(app) as client:
        url, proposed, _ = open_plan_approval(client, app)
        edited = {**proposed, "rationale": "Edited by the person."}
        assert client.post(url, json={"approve": True, "plan": edited}).status_code == 204
        cid = url.split("/")[3]
        [answered] = [
            e
            for e in client.get(f"/api/conversations/{cid}/events").json()
            if e["type"] == "approval_answered"
        ]
    assert answered["data"]["plan"]["rationale"] == "Edited by the person."
