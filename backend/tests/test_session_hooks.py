"""The manager's extension points: after-turn hooks and mount providers."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions import manager as manager_module
from datalab.sessions.containers import Mount
from datalab.sessions.hooks import TurnInfo, mount_problem
from tests.conftest import FakeDatabase
from tests.test_conversations import no_key, use_fake_runtime, wait_for


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


def test_after_turn_hooks_run_once_the_turns_work_is_done(app):
    use_fake_runtime(app)
    manager = app.state.services.sessions
    store = app.state.services.conversations
    seen: list[tuple[str, TurnInfo, list[str]]] = []

    async def hook(conversation_id: str, info: TurnInfo) -> None:
        # What the log holds when the hook runs.
        types = [e.type for e in store.all_events_after(conversation_id, 0)]
        seen.append((conversation_id, info, types))

    manager.register_after_turn(hook)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        events = wait_for(client, cid, "turn_done")

    [(conversation_id, info, types)] = seen
    assert conversation_id == cid
    assert (info.turn, info.status, info.review_only) == (1, "completed", False)
    [asked] = [e for e in events if e["type"] == "user_message"]
    [checkpoint] = [e for e in events if e["type"] == "checkpoint"]
    assert info.since == asked["seq"]
    # After the checkpoint and the review, before the chat hears it's done.
    assert info.checkpoint == checkpoint["data"]["number"]
    assert "review_finished" in types and "turn_done" not in types


def test_hooks_hear_about_turns_that_didnt_complete(app):
    use_fake_runtime(app, outcomes=["failed"])
    manager = app.state.services.sessions
    statuses: list[str] = []

    async def hook(_: str, info: TurnInfo) -> None:
        statuses.append(info.status)

    manager.register_after_turn(hook)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        wait_for(client, cid, "turn_done")
    assert statuses == ["failed"]


def test_a_failing_or_slow_hook_cant_break_the_turn(app, monkeypatch, caplog):
    use_fake_runtime(app)
    manager = app.state.services.sessions
    monkeypatch.setattr(manager_module, "AFTER_TURN_SECONDS", 0.05)
    ran: list[str] = []

    async def broken(_: str, __: TurnInfo) -> None:
        raise RuntimeError("boom")

    async def slow(_: str, __: TurnInfo) -> None:
        await asyncio.sleep(10)

    async def fine(_: str, __: TurnInfo) -> None:
        ran.append("fine")

    for hook in (broken, slow, fine):
        manager.register_after_turn(hook)
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        events = wait_for(client, cid, "turn_done")
        assert client.get(f"/api/conversations/{cid}").json()["busy"] is False
    assert ran == ["fine"]
    assert [e["data"]["status"] for e in events if e["type"] == "turn_finished"] == ["completed"]
    assert sum("after-turn hook failed" in r.message for r in caplog.records) == 2


def test_mount_providers_add_read_only_mounts_when_a_container_starts(app, tmp_path):
    manager = app.state.services.sessions
    store = app.state.services.conversations
    kb = tmp_path / "skills"
    kb.mkdir()
    asked: list[str] = []

    def provider(conversation):
        asked.append(conversation.kind)
        return [Mount(kb, "/lab/skills")] if conversation.kind == "data" else []

    manager.register_mounts(provider)
    data = store.create(kind="data", mode="analysis", title="t", model="m")
    research = store.create(kind="research", mode="research", title="t", model="m")
    containers = manager._containers(data)
    assert containers._extra_mounts is not None
    assert containers._extra_mounts() == [
        "--mount",
        f"type=bind,source={kb},target=/lab/skills,readonly",
    ]
    assert manager._extra_mounts(research.id) == []
    assert asked == ["data", "research"]


def test_a_provider_that_fails_or_mounts_over_datalabs_folders_is_skipped(app, tmp_path, caplog):
    manager = app.state.services.sessions
    store = app.state.services.conversations
    good = tmp_path / "good"
    good.mkdir()

    def broken(_):
        raise RuntimeError("boom")

    def greedy(_):
        return [
            Mount(good, "/codex-home/config.toml"),
            Mount(good, "/work"),
            Mount(good, "/data/oracle/x"),
            Mount(tmp_path / "missing", "/lab/missing"),
            Mount(good, "/lab/good"),
        ]

    manager.register_mounts(broken)
    manager.register_mounts(greedy)
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    with caplog.at_level(logging.WARNING):
        args = manager._extra_mounts(conversation.id)
    assert args == ["--mount", f"type=bind,source={good},target=/lab/good,readonly"]
    assert any("mount provider failed" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "target",
    ["relative", "/", "/lab/../work", "/lab//x", "/lab/x/", "/inputs/a", "/data", "C:\\lab"],
)
def test_only_plain_paths_outside_datalabs_folders_can_be_mounted(tmp_path, target):
    assert mount_problem(Mount(tmp_path, target)) is not None


def test_a_plain_path_elsewhere_can(tmp_path):
    assert mount_problem(Mount(tmp_path, "/lab/kb-skills")) is None
