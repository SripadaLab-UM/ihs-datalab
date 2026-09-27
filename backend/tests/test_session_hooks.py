"""The manager's extension points: after-turn hooks and mount providers."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions import manager as manager_module
from datalab.sessions.containers import Mount
from datalab.sessions.hooks import TurnInfo
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
    monkeypatch.setattr(manager_module, "AFTER_TURN_SECONDS", 0.2)
    ran: list[str] = []

    async def broken(_: str, __: TurnInfo) -> None:
        raise RuntimeError("boom")

    async def slow(_: str, __: TurnInfo) -> None:
        await asyncio.sleep(10)

    async def fine(_: str, __: TurnInfo) -> None:
        await asyncio.sleep(0.1)
        ran.append("fine")

    for hook in (broken, slow, fine, fine):
        manager.register_after_turn(hook)
    started = time.monotonic()
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        events = wait_for(client, cid, "turn_done")
        assert client.get(f"/api/conversations/{cid}").json()["busy"] is False
    # All at once, within one shared limit: both 0.1 s hooks finished.
    assert ran == ["fine", "fine"]
    assert time.monotonic() - started < 3
    assert [e["data"]["status"] for e in events if e["type"] == "turn_finished"] == ["completed"]
    assert any("after-turn hook failed" in r.message for r in caplog.records)
    assert any("took too long" in r.message for r in caplog.records)


def test_a_hook_that_swallows_cancellation_doesnt_hold_up_the_turn(app, monkeypatch, caplog):
    use_fake_runtime(app)
    manager = app.state.services.sessions
    monkeypatch.setattr(manager_module, "AFTER_TURN_SECONDS", 0.05)
    finished = asyncio.Event()

    async def stubborn(_: str, __: TurnInfo) -> None:
        # What a hook must not do (hooks.py): catch the cancellation and carry on.
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)
        finished.set()

    manager.register_after_turn(stubborn)
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        started = time.monotonic()
        wait_for(client, cid, "turn_done")
        # The turn ended at the limit, not when the hook gave up.
        assert time.monotonic() - started < 0.45
        assert not finished.is_set()
        assert len(manager._detached) == 1
        deadline = time.monotonic() + 5
        while manager._detached and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not manager._detached  # it ended on its own, and was let go


async def test_cancelling_a_turn_cancels_its_hooks_without_waiting(app):
    manager = app.state.services.sessions
    cancelled = asyncio.Event()

    async def waiting(_: str, __: TurnInfo) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    manager.register_after_turn(waiting)
    run = asyncio.create_task(manager._run_after_turn("c_1", TurnInfo(1, "completed", 0, None)))
    await asyncio.sleep(0.05)
    run.cancel()  # DataLab closing mid-turn
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 1)
    await asyncio.wait_for(cancelled.wait(), 1)


def test_a_workspace_seed_runs_once_per_conversation_and_records_its_base(app):
    use_fake_runtime(app)
    manager = app.state.services.sessions
    seeded: list[str] = []

    def seed(conversation, work):
        seeded.append(conversation.id)
        (work / "kb").mkdir()
        (work / "kb" / "index.md").write_text("# Index\n")
        return "abc123"

    manager.register_workspace_seed("kb", seed)
    with pytest.raises(ValueError):
        manager.register_workspace_seed("kb", seed)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        wait_for(client, cid, "turn_done")
        assert manager.workspace_base(cid, "kb") == "abc123"
        # The agent edits its copy; then the conversation is idle and reaped.
        (manager.paths(cid).work / "kb" / "index.md").write_text("# Edited\n")
        asyncio.run(manager._shutdown(cid))
        client.post(f"/api/conversations/{cid}/messages", json={"text": "again"})
        deadline = time.monotonic() + 5
        while client.get(f"/api/conversations/{cid}").json()["busy"] or (
            sum(
                e["type"] == "turn_done"
                for e in client.get(f"/api/conversations/{cid}/events").json()
            )
            < 2
        ):
            assert time.monotonic() < deadline
            time.sleep(0.02)
    assert seeded == [cid]
    assert (manager.paths(cid).work / "kb" / "index.md").read_text() == "# Edited\n"
    assert manager.workspace_base(cid, "kb") == "abc123"
    assert manager.workspace_base(cid, "other") is None


def test_a_seed_that_fails_is_tried_again_before_the_next_turn(app, caplog):
    use_fake_runtime(app)
    manager = app.state.services.sessions
    calls: list[int] = []

    def seed(conversation, work):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no clone yet")
        return None

    manager.register_workspace_seed("kb", seed)
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        for text in ("one", "two", "three"):
            client.post(f"/api/conversations/{cid}/messages", json={"text": text})
            deadline = time.monotonic() + 5
            while client.get(f"/api/conversations/{cid}").json()["busy"]:
                assert time.monotonic() < deadline
                time.sleep(0.02)
            wait_for(client, cid, "turn_done")
    assert len(calls) == 2
    assert any("workspace seed kb failed" in r.message for r in caplog.records)


# Mount providers ------------------------------------------------------------


@pytest.fixture
def repos(settings):
    """A folder a provider owns, like a repo clone, in DataLab's data folder."""
    root = settings.data_dir / "repos" / "ihs-knowledge"
    (root / "skills").mkdir(parents=True)
    return root


def test_mount_providers_add_read_only_mounts_when_a_container_starts(app, repos):
    manager = app.state.services.sessions
    store = app.state.services.conversations
    asked: list[str] = []

    def provider(conversation):
        asked.append(conversation.kind)
        return [Mount(repos / "skills", "/mnt/skills")] if conversation.kind == "data" else []

    manager.register_mounts(provider, roots=[repos])
    data = store.create(kind="data", mode="analysis", title="t", model="m")
    research = store.create(kind="research", mode="research", title="t", model="m")
    containers = manager._containers(data)
    assert containers._extra_mounts is not None
    real = (repos / "skills").resolve()
    assert containers._extra_mounts() == [
        "--mount",
        f"type=bind,source={real},target=/mnt/skills,readonly",
    ]
    assert manager._extra_mounts(research.id) == []
    assert asked == ["data", "research"]


def mounted(app, repos, *mounts: Mount) -> list[str]:
    manager = app.state.services.sessions
    manager.register_mounts(lambda _: list(mounts), roots=[repos])
    conversation = app.state.services.conversations.create(
        kind="research", mode="research", title="t", model="m"
    )
    return manager._extra_mounts(conversation.id)


@pytest.mark.parametrize(
    "target",
    [
        "/./work",
        "/./data/oracle",
        "/mnt/../work",
        "/mnt/x/../../codex-home",
        "/etc",
        "/usr/local/bin",
        "/mnt",
        "/mnt/",
        "mnt/x",
        "/mnt/a\\b",
    ],
)
def test_targets_outside_mnt_are_refused_however_theyre_written(app, repos, target):
    assert mounted(app, repos, Mount(repos / "skills", target)) == []


def test_targets_are_normalised_before_docker_sees_them(app, repos):
    args = mounted(app, repos, Mount(repos / "skills", "/mnt/./kb//skills/"))
    assert args[1].endswith("target=/mnt/kb/skills,readonly")


def test_a_relative_source_is_refused(app, repos, monkeypatch):
    monkeypatch.chdir(repos)
    assert mounted(app, repos, Mount(Path("skills"), "/mnt/skills")) == []


def test_a_source_linked_out_of_its_root_is_refused(app, repos, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (repos / "link").symlink_to(outside)
    assert mounted(app, repos, Mount(repos / "link", "/mnt/x")) == []
    # So is one outside it in the first place, or missing.
    assert mounted(app, repos, Mount(outside, "/mnt/x")) == []
    assert mounted(app, repos, Mount(repos / "missing", "/mnt/x")) == []


def test_a_source_in_a_conversations_folder_is_refused(app, settings, repos):
    other = settings.data_dir / "sessions" / "c_other" / "work"
    other.mkdir(parents=True)
    (repos / "sneaky").symlink_to(other)
    manager = app.state.services.sessions
    # Even when its provider claims the whole data folder.
    manager.register_mounts(
        lambda _: [Mount(other, "/mnt/a"), Mount(repos / "sneaky", "/mnt/b")],
        roots=[settings.data_dir],
    )
    conversation = app.state.services.conversations.create(
        kind="data", mode="analysis", title="t", model="m"
    )
    assert manager._extra_mounts(conversation.id) == []


def test_credentials_are_refused_even_inside_a_root(app, repos):
    (repos / "credentials.json").write_text("{}")
    assert mounted(app, repos, Mount(repos / "credentials.json", "/mnt/c")) == []


def test_duplicate_targets_are_mounted_once(app, repos):
    (repos / "other").mkdir()
    args = mounted(
        app,
        repos,
        Mount(repos / "skills", "/mnt/x"),
        Mount(repos / "other", "/mnt/./x"),
        Mount(repos / "other", "/mnt/y"),
    )
    assert [a.split("target=")[1] for a in args if a.startswith("type=")] == [
        "/mnt/x,readonly",
        "/mnt/y,readonly",
    ]


def test_a_provider_that_fails_is_skipped(app, repos, caplog):
    manager = app.state.services.sessions

    def broken(_):
        raise RuntimeError("boom")

    manager.register_mounts(broken, roots=[repos])
    with caplog.at_level(logging.WARNING):
        args = mounted(app, repos, Mount(repos / "skills", "/mnt/good"))
    assert len(args) == 2
    assert any("mount provider failed" in r.message for r in caplog.records)


@pytest.mark.parametrize("root", [Path("/"), Path.home(), Path("relative")])
def test_a_provider_cant_declare_a_whole_drive_or_home_folder(app, root):
    with pytest.raises(ValueError):
        app.state.services.sessions.register_mounts(lambda _: [], roots=[root])
