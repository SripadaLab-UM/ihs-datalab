"""Uploading files into a conversation on the lab's server (sessions/uploads.py,
api/inputs.py): stored in the conversation's folder, then attached read-only."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.config import ServerSettings
from datalab.sessions.inputs import AttachmentStore
from datalab.sessions.mounts import input_mounts
from tests.conftest import FakeDatabase
from tests.test_inputs_api import FakeContainers
from tests.test_route_access import JSON, SITE, signed_in


def server_app(settings, catalog):
    on_server = replace(
        settings,
        profile="real",
        deployment="server",
        server=ServerSettings(public_url=SITE, admins=(1001,)),
    )
    app = create_app(on_server, database=FakeDatabase(), catalog=catalog, manage_containers=False)
    app.state.services.sessions._containers = lambda conversation: FakeContainers()
    return app


@pytest.fixture
def app(settings, catalog):
    return server_app(settings, catalog)


def send(client: TestClient, cid: str, upload: str, path: str, body: bytes):
    return client.put(
        f"/api/conversations/{cid}/uploads/{upload}/files",
        content=body,
        headers={
            "origin": SITE,
            "content-type": "application/octet-stream",
            "x-datalab-path": quote(path),
        },
    )


def start(client: TestClient, cid: str, kind: str) -> str:
    made = client.post(f"/api/conversations/{cid}/uploads", json={"kind": kind}, headers=JSON)
    assert made.status_code == 201, made.text
    return made.json()["id"]


def conversation(client: TestClient) -> str:
    return client.post("/api/conversations", json={"mode": "analysis"}, headers=JSON).json()["id"]


def test_uploaded_files_are_attached_and_mounted_read_only(app):
    with TestClient(app) as client:
        signed_in(app, client, 1001, admin=True)
        cid = conversation(client)
        upload = start(client, cid, "files")
        assert send(client, cid, upload, "visits.csv", b"id,visit\n1,2\n").json()["bytes"] == 13
        assert send(client, cid, upload, "notes é.txt", b"synthetic").status_code == 200
        attached = client.post(f"/api/conversations/{cid}/uploads/{upload}/attach", headers=JSON)
        assert attached.status_code == 200, attached.text
        names = sorted(a["container_path"] for a in attached.json()["added"])
        assert names == ["/inputs/notes é.txt", "/inputs/visits.csv"]
        listed = client.get(f"/api/conversations/{cid}/inputs").json()
        assert all(item["available"] for item in listed)
        services = app.state.services
        mounts = " ".join(
            input_mounts(
                services.conversations,
                services.settings,
                AttachmentStore(services.conversations._db),
                cid,
            )
        )
        assert "/inputs/visits.csv" in mounts and "readonly" in mounts
        audit = (services.settings.data_dir / "logs" / "audit.jsonl").read_text().splitlines()
        uploaded = [json.loads(line) for line in audit if '"upload"' in line]
        assert uploaded[-1]["files"] == 2 and uploaded[-1]["user"]["login"] == "person1001"
        assert "visits" not in uploaded[-1].__repr__()  # no names in the audit


def test_a_folder_uploads_as_one_attachment(app):
    with TestClient(app) as client:
        signed_in(app, client, 1001, admin=True)
        cid = conversation(client)
        upload = start(client, cid, "folder")
        for path in ("survey/a.csv", "survey/waves/b.csv"):
            assert send(client, cid, upload, path, b"x").status_code == 200
        added = client.post(f"/api/conversations/{cid}/uploads/{upload}/attach", headers=JSON)
        [folder] = added.json()["added"]
        assert (folder["container_path"], folder["kind"]) == ("/inputs/survey", "folder")
        # Removed: its files go too.
        host = Path(folder["host_path"])
        assert (host / "waves" / "b.csv").is_file()
        gone = client.delete(f"/api/conversations/{cid}/inputs/{folder['id']}", headers=JSON)
        assert gone.status_code == 204 and not host.exists()


def test_odd_names_credentials_and_oversized_files_are_refused(app, monkeypatch):
    monkeypatch.setattr("datalab.sessions.uploads.MAX_FILE_BYTES", 10)
    with TestClient(app) as client:
        signed_in(app, client, 1001, admin=True)
        cid = conversation(client)
        upload = start(client, cid, "folder")
        for path in (
            "../escape.csv",
            "a/../../b.csv",
            "/etc/passwd",
            "data/.netrc",
            "data/\x07.csv",
        ):
            refused = send(client, cid, upload, path, b"x")
            assert refused.status_code == 422, path
        assert send(client, cid, upload, "data/big.csv", b"x" * 11).status_code == 413
        files = start(client, cid, "files")
        assert send(client, cid, files, "folder/inside.csv", b"x").status_code == 422
        empty = client.post(f"/api/conversations/{cid}/uploads/{upload}/attach", headers=JSON)
        assert empty.status_code == 422
        uploads = app.state.services.settings.data_dir / "sessions" / cid / "uploads"
        assert not any(p.name in ("escape.csv", "b.csv", "big.csv") for p in uploads.rglob("*"))


def test_nobody_else_can_upload_into_a_conversation(app):
    with TestClient(app) as a:
        b = TestClient(app)
        signed_in(app, a, 1001, admin=True)
        signed_in(app, b, 1002, admin=False)
        cid = conversation(a)
        upload = start(a, cid, "files")
        assert (
            b.post(
                f"/api/conversations/{cid}/uploads", json={"kind": "files"}, headers=JSON
            ).status_code
            == 404
        )
        assert send(b, cid, upload, "x.csv", b"x").status_code == 404
        assert (
            b.post(f"/api/conversations/{cid}/uploads/{upload}/attach", headers=JSON).status_code
            == 404
        )


def test_another_page_cant_send_a_file(app):
    with TestClient(app) as client:
        signed_in(app, client, 1001, admin=True)
        cid = conversation(client)
        upload = start(client, cid, "files")
        url = f"/api/conversations/{cid}/uploads/{upload}/files"
        # What a form on another page could send: no custom header.
        plain = client.put(
            url,
            content=b"x",
            headers={"origin": SITE, "content-type": "application/octet-stream"},
        )
        assert plain.status_code == 403
        elsewhere = client.put(
            url,
            content=b"x",
            headers={
                "origin": "https://evil.example",
                "content-type": "application/octet-stream",
                "x-datalab-path": "x.csv",
            },
        )
        assert elsewhere.status_code == 403


def test_a_computer_attaches_from_its_own_disk_instead(settings, catalog):
    app = create_app(
        replace(settings, profile="real"),
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        refused = client.post(f"/api/conversations/{cid}/uploads", json={"kind": "files"})
        assert refused.status_code == 403 and "this computer" in refused.json()["detail"]


def test_practice_takes_no_uploads(settings, catalog):
    """Practice keeps to synthetic samples on a server too, so nothing real reaches it."""
    on_server = replace(
        settings, deployment="server", server=ServerSettings(public_url=SITE, admins=(1001,))
    )
    practice = create_app(
        on_server, database=FakeDatabase(), catalog=catalog, manage_containers=False
    )
    with TestClient(practice) as client:
        signed_in(practice, client, 1001, admin=True)
        cid = conversation(client)
        refused = client.post(
            f"/api/conversations/{cid}/uploads", json={"kind": "files"}, headers=JSON
        )
        assert refused.status_code == 403 and "samples" in refused.json()["detail"]
