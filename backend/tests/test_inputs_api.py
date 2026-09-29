"""Attaching inputs over the API: practice samples, the real picker, and refusals."""

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions import picker
from datalab.sessions.containers import DockerError
from datalab.sessions.manager import Busy
from tests.conftest import FakeDatabase
from tests.test_conversations import use_fake_runtime, wait_for


class FakeContainers:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def stop_and_confirm(self):
        if self.fail:
            raise DockerError("DataLab couldn't stop the agent's container.")


def make_app(settings, catalog, *, stop_fails: bool = False):
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )
    app.state.services.sessions._containers = lambda conversation: FakeContainers(stop_fails)
    return app


@pytest.fixture
def real_settings(settings):
    return dataclasses.replace(settings, profile="real")


def test_practice_attaches_only_samples(settings, catalog, monkeypatch):
    async def never(kind):
        raise AssertionError("practice must never open the computer's picker")

    monkeypatch.setattr(picker, "pick", never)
    app = make_app(settings, catalog)
    made = use_fake_runtime(app)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        samples = client.get("/api/input-samples").json()
        assert "sleep_diary_sample.csv" in samples and "r_helpers" in samples
        refused = client.post(f"/api/conversations/{cid}/inputs", json={"source": "folder"})
        assert refused.status_code == 403

        added = client.post(
            f"/api/conversations/{cid}/inputs",
            json={"source": "sample", "sample": "r_helpers"},
        ).json()["added"]
        assert [(a["container_path"], a["kind"]) for a in added] == [
            ("/inputs/r_helpers", "folder")
        ]
        assert (
            client.post(
                f"/api/conversations/{cid}/inputs", json={"source": "sample", "sample": "../etc"}
            ).status_code
            == 404
        )

        # The container starts with it mounted read-only, and the agent is told.
        manager = app.state.services.sessions
        args = manager._containers_for(cid, "data")._extra_mounts()
        assert args[0] == "--mount" and args[1].endswith("target=/inputs/r_helpers,readonly")
        client.post(f"/api/conversations/{cid}/messages", json={"text": "look"})
        wait_for(client, cid, "checkpoint")  # the turn ends once its files are saved
        assert made[-1].sent[-1].startswith('[DataLab: the user attached "/inputs/r_helpers"')

        [item] = client.get(f"/api/conversations/{cid}/inputs").json()
        assert client.delete(f"/api/conversations/{cid}/inputs/{item['id']}").status_code == 204
        assert client.get(f"/api/conversations/{cid}/inputs").json() == []
        assert manager._containers_for(cid, "data")._extra_mounts() == []


def test_nothing_can_be_attached_in_knowledge_writing(settings, catalog):
    """Metadata only: the API refuses, and nothing already stored is mounted."""
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "knowledge"}).json()["id"]
        refused = client.post(
            f"/api/conversations/{cid}/inputs", json={"source": "sample", "sample": "r_helpers"}
        )
        assert refused.status_code == 403
        assert "can't be attached in Knowledge writing mode" in refused.json()["detail"]
        assert client.get(f"/api/conversations/{cid}/inputs").json() == []
        # Even one put in the store some other way is never mounted.
        services = app.state.services
        manager = services.sessions
        other = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        client.post(
            f"/api/conversations/{other}/inputs", json={"source": "sample", "sample": "r_helpers"}
        )
        assert manager._input_mounts(other)
        [stored] = manager._attachments.list(other)
        manager._attachments._db.execute(
            "UPDATE attachments SET conversation_id = ? WHERE id = ?", (cid, stored.id)
        )
        assert manager._attachments.list(cid) and manager._input_mounts(cid) == []


def test_real_profile_uses_the_picker_and_refuses_private_places(
    real_settings, catalog, monkeypatch, tmp_path
):
    study = tmp_path / "study"
    study.mkdir()
    (study / "data.csv").write_text("a\n1\n")

    async def chosen(kind, **_):
        assert kind == "files"
        return [study / "data.csv", real_settings.data_dir]

    monkeypatch.setattr(picker, "pick", chosen)
    # The test's files live under the system temp folder, which is refused
    # on a Mac; the real rules are tested in test_inputs.py.
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        assert client.get("/api/input-samples").json() == []
        cid = client.post("/api/conversations", json={}).json()["id"]
        refused_sample = client.post(
            f"/api/conversations/{cid}/inputs", json={"source": "sample", "sample": "x"}
        )
        assert refused_sample.status_code == 403
        result = client.post(f"/api/conversations/{cid}/inputs", json={"source": "files"}).json()
        assert [a["container_path"] for a in result["added"]] == ["/inputs/data.csv"]
        assert [r["path"] for r in result["refused"]] == [str(real_settings.data_dir)]


def test_nothing_is_attached_while_the_agent_works(settings, catalog):
    app = make_app(settings, catalog)
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "busy"})
        wait_for(client, cid, "answer_delta")
        busy = client.post(
            f"/api/conversations/{cid}/inputs",
            json={"source": "sample", "sample": "r_helpers"},
        )
        assert busy.status_code == 409
        client.post(f"/api/conversations/{cid}/stop")
        wait_for(client, cid, "checkpoint")
        assert client.get(f"/api/conversations/{cid}/inputs").json() == []


def test_a_removed_input_stays_listed_if_the_container_wont_stop(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        client.post(
            f"/api/conversations/{cid}/inputs", json={"source": "sample", "sample": "r_helpers"}
        )
        [item] = client.get(f"/api/conversations/{cid}/inputs").json()
        app.state.services.sessions._containers = lambda conversation: FakeContainers(True)
        assert client.delete(f"/api/conversations/{cid}/inputs/{item['id']}").status_code == 503
        assert len(client.get(f"/api/conversations/{cid}/inputs").json()) == 1  # still true


async def test_no_turn_starts_while_inputs_change(settings, catalog):
    app = make_app(settings, catalog)
    manager = app.state.services.sessions
    conversation = app.state.services.conversations.create(
        kind="data", mode="analysis", title="t", model="m"
    )
    async with manager.changing_inputs(conversation):
        assert manager.is_busy(conversation.id)
        with pytest.raises(Busy):
            await manager.send(conversation, "hi", None)
    assert not manager.is_busy(conversation.id)


def test_nothing_can_be_attached_in_workflow_authoring(settings, catalog):
    """It's opened only from the Workflows tab, which attaches nothing."""
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "workflows"}).json()["id"]
        refused = client.post(
            f"/api/conversations/{cid}/inputs", json={"source": "sample", "sample": "r_helpers"}
        )
        assert refused.status_code == 403
        assert refused.json()["detail"] == (
            "Files can't be attached in Workflow authoring mode. "
            "Attach them in a Workspace conversation instead."
        )
