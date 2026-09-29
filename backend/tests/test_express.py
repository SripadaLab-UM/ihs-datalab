"""The Express switch: quick answers, low effort, no plans or confirmations.

Express changes how the agent works, never what it may access: every mode
keeps its data tools, the SQL check and the query log; only the tools that
wait for the person (a plan to approve, a research-helper question to
review) are refused, by DataLab's data tools, for a turn asked with it on.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions import modes
from datalab.sessions.modes import DATA_TOOLS, EXPRESS_OFF_TOOLS, MODES
from datalab.sessions.provenance import turns_from_events
from datalab.sessions.runtime import TurnResult
from datalab.sessions.tokens import SessionAccess
from tests.conftest import FakeDatabase, live_server
from tests.test_agent_tools import PLAN_ARGS, mcp_session, payload
from tests.test_conversations import FakeRuntime, no_key


@pytest.fixture
def app(settings, catalog):
    # The tests' settings are the practice profile's.
    assert settings.profile == "practice"
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
        model_key=no_key,
    )


class EffortRuntime(FakeRuntime):
    """The fake Codex runtime, keeping each turn's effort too."""

    def __init__(self, emit) -> None:
        super().__init__(emit)
        self.efforts: list[str | None] = []

    async def send(self, text: str, *, effort: str | None = None) -> TurnResult:
        self.efforts.append(effort)
        return await super().send(text, effort=effort)


def fake_runtimes(app) -> list[EffortRuntime]:
    made: list[EffortRuntime] = []
    manager = app.state.services.sessions
    store = app.state.services.conversations

    def fake(conversation):
        runtime = manager._runtimes.get(conversation.id)
        if runtime is None:

            async def emit(kind, data):
                store.append(conversation.id, kind, data)

            runtime = EffortRuntime(emit)
            made.append(runtime)
            manager._runtimes[conversation.id] = runtime
        return runtime

    manager._runtime = fake
    return made


def ask(client, cid: str, text: str, effort: str | None = None) -> list[dict]:
    """Send a message and wait for its turn to end: the turn's events."""
    before = len(client.get(f"/api/conversations/{cid}/events").json())
    body = {"text": text, **({"effort": effort} if effort else {})}
    assert client.post(f"/api/conversations/{cid}/messages", json=body).status_code == 202
    deadline = time.time() + 5
    while time.time() < deadline:
        events = client.get(f"/api/conversations/{cid}/events").json()[before:]
        if any(e["type"] == "turn_done" for e in events):
            return events
        time.sleep(0.02)
    raise AssertionError("the turn didn't finish")


# --- The switch -------------------------------------------------------------


def test_express_is_off_in_every_new_conversation(app):
    with TestClient(app) as client:
        made = [client.post("/api/conversations", json={"mode": m}).json() for m in MODES]
    assert all(c["express"] is False for c in made)


def test_express_and_the_rigor_review_are_never_both_on(app):
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        url = f"/api/conversations/{cid}"
        assert client.get(url).json()["rigor_review"] is True  # Analysis's default

        on = client.patch(url, json={"express": True}).json()
        assert (on["express"], on["rigor_review"]) == (True, False)

        rigor = client.patch(url, json={"rigor_review": True}).json()
        assert (rigor["express"], rigor["rigor_review"]) == (False, True)

        # Switching one on while switching the other off, in one request.
        both = client.patch(url, json={"express": True, "rigor_review": False}).json()
        assert (both["express"], both["rigor_review"]) == (True, False)
        back = client.patch(url, json={"express": False, "rigor_review": True}).json()
        assert (back["express"], back["rigor_review"]) == (False, True)

        refused = client.patch(url, json={"express": True, "rigor_review": True})
        assert refused.status_code == 422 and "choose one" in refused.json()["detail"]
        # Switching Express off leaves the rigor review as it was.
        off = client.patch(url, json={"express": False}).json()
        assert (off["express"], off["rigor_review"]) == (False, True)
        assert client.get("/api/conversations").json()[0]["express"] is False


def test_the_store_keeps_them_apart_too(app):
    store = app.state.services.conversations
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    store.set_express(conversation.id, True)
    store.set_rigor_review(conversation.id, False)  # off: Express stays on
    assert store.get(conversation.id).express is True  # type: ignore[union-attr]
    store.set_rigor_review(conversation.id, True)
    after = store.get(conversation.id)
    assert after is not None and (after.express, after.rigor_review) == (False, True)


# --- A turn asked with Express on --------------------------------------------


def test_an_express_turn_is_quick_recorded_and_not_reviewed(app):
    made = fake_runtimes(app)
    tokens = app.state.services.tokens
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.patch(f"/api/conversations/{cid}", json={"express": True})

        first = ask(client, cid, "How many interns in 2025?")
        assert tokens.express(cid)
        second = ask(client, cid, "And in 2024?", effort="high")  # the person's choice holds

        client.patch(f"/api/conversations/{cid}", json={"express": False})
        third = ask(client, cid, "Now plan a proper analysis.")
        assert not tokens.express(cid)
        fourth = ask(client, cid, "Go on.")

    runtime = made[0]
    assert runtime.efforts == ["low", "high", None, None]
    sent = runtime.sent
    assert sent[0].startswith(modes.EXPRESS) and sent[0].endswith("How many interns in 2025?")
    assert modes.EXPRESS_REVIEWED not in sent[0]  # Analysis's answer isn't a proposal
    assert sent[1].startswith(modes.EXPRESS)
    # Switched off: said once, then nothing.
    assert sent[2] == modes.EXPRESS_OFF + "Now plan a proper analysis."
    assert sent[3] == "Go on."

    asked = [e for e in first + second + third + fourth if e["type"] == "user_message"]
    assert [e["data"].get("express", False) for e in asked] == [True, True, False, False]
    assert asked[0]["data"]["text"] == "How many interns in 2025?"  # as typed
    # Express switched the rigor review off, and switching Express off doesn't
    # switch it back on: no review at all.
    assert all(e["type"] != "review_started" for e in first + second + third + fourth)


def test_the_express_block_is_only_in_messages_asked_with_it(app):
    for mode in MODES:
        assert "Express" not in modes.instructions(mode)
    reviewed = {m for m in MODES if modes.EXPRESS_REVIEWED in modes.express_note(m)}
    assert reviewed == {"sql", "pipelines", "workflows", "knowledge"}
    for mode in MODES:
        note = modes.express_note(mode)
        assert note.startswith("[DataLab: Express is on") and note.endswith("]\n\n")
        assert "propose_plan is off" in note and "Ask only if" in note


def test_express_works_in_a_research_session_too(app):
    made = fake_runtimes(app)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "research"}).json()["id"]
        assert client.patch(f"/api/conversations/{cid}", json={"express": True}).json()["express"]
        ask(client, cid, "What is lme4?")
    assert made[0].efforts == ["low"] and made[0].sent[0].startswith(modes.EXPRESS)


# --- What the data tools allow -----------------------------------------------


@pytest.fixture
def server(settings, catalog):
    database = FakeDatabase()
    app = create_app(
        settings, database=database, catalog=catalog, manage_containers=False, protect_api=False
    )
    with live_server(app) as base_url:
        yield base_url, app.state.services, database


CALLS = {
    "query": {"sql": "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA"},
    "ask_research_helper": {"question": "What is a mixed model?"},
    "propose_plan": PLAN_ARGS,
}


@pytest.mark.parametrize("mode", [m for m in MODES.values() if m.kind == "data"], ids=str)
async def test_in_every_mode_express_refuses_only_the_tools_that_wait(server, tmp_path, mode):
    """With Express on, DataLab's own data tools refuse propose_plan and
    ask_research_helper; the mode's other tools, participant-level queries
    included, work as they do without it."""
    base_url, services, database = server
    session_id = f"s_{mode.id}"
    token = services.tokens.issue(
        SessionAccess(session_id, "data", tmp_path / "oracle", tools=mode.allowed_tools)
    )
    services.tokens.set_express(session_id, True)
    async with mcp_session(base_url, token) as session:
        results = {name: await session.call_tool(name, args) for name, args in CALLS.items()}
        catalog = await session.call_tool("search_catalog", {"query": "mood"})
    assert not catalog.is_error
    for name, result in results.items():
        text = result.content[0].text
        if name not in mode.allowed_tools:
            assert result.is_error and "isn't available in this mode" in text, name
        elif name in EXPRESS_OFF_TOOLS:
            assert result.is_error and f"{name} is off" in text and "Express" in text, name
        else:
            assert payload(result)["row_count"] == 2, name  # the same rows as ever
    assert bool(database.calls) == ("query" in mode.allowed_tools)


async def test_without_express_the_plan_tool_is_there_as_before(server, tmp_path):
    base_url, services, _ = server
    token = services.tokens.issue(SessionAccess("s1", "data", tmp_path / "oracle"))
    services.tokens.set_express("s1", True)
    services.tokens.set_express("s1", False)
    async with mcp_session(base_url, token) as session:
        result = payload(await session.call_tool("propose_plan", PLAN_ARGS))
    assert result["status"] == "not approved" and "during a turn" in result["note"]


def test_express_refuses_only_tools_that_exist():
    assert set(DATA_TOOLS) >= EXPRESS_OFF_TOOLS


# --- Provenance and History --------------------------------------------------


def event(type, **data):
    return SimpleNamespace(type=type, created_at="2026-09-28T10:00:00", data=data)


def test_each_turn_knows_whether_it_was_an_express_one():
    turns = turns_from_events(
        [
            event("user_message", text="quick", express=True),
            event("answer", text="12"),
            event("user_message", text="slow"),
        ]
    )
    assert (turns[1].express, turns[2].express) == (True, False)


def test_history_and_how_was_this_made_say_express(app):
    services = app.state.services
    store = services.conversations
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        work: Path = services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True, exist_ok=True)
        store.append(cid, "user_message", {"text": "Plot mood.", "express": True})
        (work / "outputs" / "mood.png").write_bytes(b"one")
        services.sessions.checkpoints(cid).take("After turn 1", turn=1)
        store.append(cid, "user_message", {"text": "Plot steps."})
        (work / "outputs" / "steps.png").write_bytes(b"two")
        services.sessions.checkpoints(cid).take("After turn 2", turn=2)

        history = client.get(f"/api/conversations/{cid}/checkpoints").json()
        mood = client.get(f"/api/conversations/{cid}/provenance/outputs/mood.png").json()
        steps = client.get(f"/api/conversations/{cid}/provenance/outputs/steps.png").json()
        assert store.express_turns(cid) == {1}
    assert [(c["turn"], c["express"]) for c in history] == [(2, False), (1, True)]
    assert (mood["turn"], mood["express"]) == (1, True)
    assert (steps["turn"], steps["express"]) == (2, False)
