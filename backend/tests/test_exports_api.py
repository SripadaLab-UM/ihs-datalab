"""Exporting over the API: destinations, files from checkpoints, and reports."""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.exports import MANIFEST
from datalab.sessions import picker
from tests.conftest import FakeDatabase


def make_app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


@pytest.fixture
def real_settings(settings):
    return dataclasses.replace(settings, profile="real")


def prepare(app, client):
    cid = client.post("/api/conversations", json={"title": "Sleep study"}).json()["id"]
    work = app.state.services.sessions.paths(cid).work
    (work / "outputs").mkdir(parents=True)
    (work / "outputs" / "table.csv").write_text("a\n1\n")
    app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
    (work / "outputs" / "table.csv").write_text("changed after the checkpoint")
    return cid


def test_practice_exports_only_to_its_own_folder(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        [destination] = client.get("/api/export-destinations").json()
        assert destination["id"] == "practice"
        assert client.post("/api/export-destinations").status_code == 403
        cid = prepare(app, client)
        result = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "table.csv"}],
                "report": {"html": "<h1>Hi</h1><a href='https://x'>x</a><script>1</script>"},
            },
        )
        assert result.status_code == 201
        folder = settings.data_dir / "practice-exports"
        [export] = list(folder.iterdir())
        assert (export / "files" / "outputs" / "table.csv").read_text() == "a\n1\n"  # checkpoint
        report = (export / "conversation.html").read_text()
        assert "Content-Security-Policy" in report
        assert "https://x" not in report and "<script" not in report.split("</head>")[1]
        manifest = json.loads((export / MANIFEST).read_text())
        assert manifest["conversation"]["title"] == "Sleep study"
        assert manifest["contains_study_data"] is False  # practice data is synthetic
        events = client.get(f"/api/conversations/{cid}/events").json()
        assert events[-1]["type"] == "exported"


def test_real_destinations_come_from_the_picker(real_settings, catalog, monkeypatch, tmp_path):
    dropbox = tmp_path / "Dropbox" / "IHS"
    dropbox.mkdir(parents=True)
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())

    async def chosen(kind):
        assert kind == "folder"
        return [dropbox]

    monkeypatch.setattr(picker, "pick", chosen)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        assert client.get("/api/export-destinations").json() == []
        added = client.post("/api/export-destinations")
        assert added.status_code == 201 and added.json()["path"] == str(dropbox)
        assert client.post("/api/export-destinations").status_code == 409  # already added
        cid = prepare(app, client)
        exported = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": added.json()["id"],
                "files": [{"root": "outputs", "path": "table.csv"}],
            },
        ).json()
        assert exported["folder"].startswith(str(dropbox))
        manifest = json.loads((dropbox / exported["folder"].split("/")[-1] / MANIFEST).read_text())
        assert manifest["contains_study_data"] is True
        # Unknown files and made-up destinations are refused.
        missing = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": added.json()["id"],
                "files": [{"root": "outputs", "path": "nope.csv"}],
            },
        )
        assert missing.status_code == 404
        bogus = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": "dest_x", "files": [{"root": "outputs", "path": "table.csv"}]},
        )
        assert bogus.status_code == 404
        assert client.delete(f"/api/export-destinations/{added.json()['id']}").status_code == 204
        assert (dropbox / exported["folder"].split("/")[-1]).exists()  # untouched


def test_datalab_folders_cant_be_destinations(real_settings, catalog, monkeypatch):
    async def chosen(kind):
        return [real_settings.data_dir]

    monkeypatch.setattr(picker, "pick", chosen)
    real_settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        assert client.post("/api/export-destinations").status_code == 422


def test_a_destination_replaced_by_a_link_is_refused(real_settings, catalog, monkeypatch, tmp_path):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    monkeypatch.setattr("datalab.sessions.inputs._CONTAINERS_POSIX", ())

    async def chosen(kind):
        return [dropbox]

    monkeypatch.setattr(picker, "pick", chosen)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        destination = client.post("/api/export-destinations").json()["id"]
        cid = prepare(app, client)
        dropbox.rmdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        dropbox.symlink_to(elsewhere)
        refused = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": destination,
                "files": [{"root": "outputs", "path": "table.csv"}],
            },
        )
        assert refused.status_code == 422
        assert list(elsewhere.iterdir()) == []


def test_an_exported_page_keeps_its_own_images(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        outputs = app.state.services.sessions.paths(cid).work / "outputs"
        (outputs / "figures").mkdir(parents=True)
        (outputs / "figures" / "a.png").write_bytes(b"\x89PNG")
        (outputs / "report.html").write_text(
            "<img src='figures/a.png'><img src='../../etc/passwd.png'><img src='https://x/y.png'>"
        )
        app.state.services.sessions.checkpoints(cid).take("t")
        client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "report.html"}],
            },
        )
        [export] = list((settings.data_dir / "practice-exports").iterdir())
        page = (export / "files" / "outputs" / "report.html").read_text()
        assert page.count("data:image/png;base64,iVBORw") == 1
        assert "https://x" not in page and "passwd" not in page
