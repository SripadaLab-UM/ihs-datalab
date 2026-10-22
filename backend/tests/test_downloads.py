"""Exports as downloads on the lab's server (downloads.py, api/exports.py)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from datalab import downloads
from datalab.downloads import Downloads
from tests.test_route_access import JSON, signed_in
from tests.test_uploads import conversation, server_app

REPORT = {"html": "<h1>Synthetic</h1><p>Mean steps 8,000.</p>"}


@pytest.fixture
def app(settings, catalog):
    return server_app(settings, catalog)


def test_an_export_is_a_download_for_its_maker_only(app):
    with TestClient(app) as a:
        b = TestClient(app)
        signed_in(app, a, 1001, admin=True)
        signed_in(app, b, 1002, admin=False)
        [only] = a.get("/api/export-destinations").json()
        assert (only["id"], only["name"]) == ("download", "Download")
        cid = conversation(a)
        made = a.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": "download", "report": REPORT},
            headers=JSON,
        )
        assert made.status_code == 201, made.text
        url = made.json()["download_url"]
        assert (
            url.startswith("/api/downloads/dl_") and made.json()["saved_to"] == "Ready to download"
        )
        got = a.get(url)
        assert got.status_code == 200
        assert got.headers["content-type"] == "application/zip"
        assert "attachment" in got.headers["content-disposition"]
        names = zipfile.ZipFile(io.BytesIO(got.content)).namelist()
        assert any(n.endswith("/conversation.html") for n in names)
        assert b.get(url).status_code == 404  # someone else's
        # Nothing is left half-made in the staging folder.
        staging = app.state.services.settings.data_dir / "downloads" / "user-2" / "staging"
        assert list(staging.iterdir()) == []
        audit = app.state.services.settings.data_dir / "logs" / "audit.jsonl"
        downloaded = [
            json.loads(line) for line in audit.read_text().splitlines() if '"download"' in line
        ]
        assert downloaded[-1]["files"] == 1 and downloaded[-1]["user"]["login"] == "person1001"


def test_no_folder_can_be_named_on_the_server(app):
    with TestClient(app) as a:
        signed_in(app, a, 1001, admin=True)
        cid = conversation(a)
        elsewhere = a.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": "dest_anything", "report": REPORT},
            headers=JSON,
        )
        assert elsewhere.status_code == 404
        assert a.post("/api/export-destinations", json={}, headers=JSON).status_code == 409
        assert a.get("/api/export-destinations/places").json()["can_add"] is False
        key = a.put("/api/workflows/destinations/lab", json={"destination_id": "x"}, headers=JSON)
        assert key.status_code == 409


def test_a_download_is_kept_for_a_day(tmp_path, monkeypatch):
    store = Downloads(tmp_path)
    exported = store.staging(7) / "2026-10-22 Synthetic"
    (exported / "files").mkdir(parents=True)
    (exported / "files" / "a.csv").write_text("x\n")
    made = store.make(7, exported, files=1, session_id="c_1")
    assert not exported.exists()
    assert store.get(7, made.id) is not None and store.get(8, made.id) is None
    clock = downloads.time.time() + downloads.KEEP_SECONDS + 1
    monkeypatch.setattr(downloads.time, "time", lambda: clock)
    assert store.get(7, made.id) is None and not made.path.exists()
