"""Send feedback's support reports: the bundle, what never goes in it, saving,
the two ways to send it, and retrying without duplicates (docs/SUPPORT.md)."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import __version__, credentials, db, support
from datalab.api.support import SupportServices, build_support_router
from datalab.app import create_app
from datalab.config import RepoSettings
from datalab.data.access_log import AccessLog
from datalab.exports import DestinationStore
from datalab.repos.github import GitHubAuth, Tokens, TokenStore
from datalab.sessions.store import ConversationStore
from datalab.web import BrowserSession
from tests.conftest import FakeDatabase

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
REPO = "SripadaLab-UM/ihs-support"
CONTACT = "Ali <ali@example.org>"


def now(delta_minutes: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=delta_minutes)).isoformat(timespec="seconds")


def draft(**change: Any) -> dict[str, Any]:
    body = {
        "kind": "bug",
        "happened": "The results table stayed empty after the query finished.",
        "expected": "The rows to show.",
        "steps": "1. Ask for steps by day\n2. Wait",
        "route": "/workspace",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/129.0 Safari/537.36",
        "client_trail": [],
        "attachments": [{"name": "screenshot.png", "data_base64": base64.b64encode(PNG).decode()}],
    }
    return {**body, **change}


def make_app(settings, catalog, **kwargs):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=kwargs.pop("protect_api", False),
        **kwargs,
    )


def save_report(client: TestClient, **change: Any) -> dict[str, Any]:
    shown = client.post("/api/support/preview", json=draft(**change))
    assert shown.status_code == 200, shown.text
    saved = client.post("/api/support/reports", json={"draft_id": shown.json()["draft_id"]})
    assert saved.status_code == 201, saved.text
    return saved.json()


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ------------------------------------------------------------------ the bundle


def test_the_bundle_holds_the_summary_diagnostics_manifest_and_attachments(settings, catalog):
    with TestClient(make_app(settings, catalog)) as client:
        shown = client.post("/api/support/preview", json=draft()).json()
        report_id = shown["report_id"]
        assert support.REPORT_ID.fullmatch(report_id)
        assert report_id.startswith("DL-" + datetime.now(UTC).strftime("%Y%m%d") + "-")
        # Previewing saves nothing yet.
        assert client.get("/api/support/reports").json() == []
        saved = client.post("/api/support/reports", json={"draft_id": shown["draft_id"]}).json()
        # The very draft that was shown.
        assert saved["report_id"] == report_id
        assert saved["zip_sha256"] == shown["zip_sha256"]
        assert saved["contents"] == shown["contents"]
        assert saved["states"] == ["saved_locally"]
        # A draft is saved once.
        again = client.post("/api/support/reports", json={"draft_id": shown["draft_id"]})
        assert again.status_code == 404

        response = client.get(f"/api/support/reports/{report_id}/bundle")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="{report_id}.zip"'
        )
        data = response.content
    assert hashlib.sha256(data).hexdigest() == saved["zip_sha256"]
    files = unzip(data)
    assert list(files) == [
        "summary.md",
        "diagnostics.json",
        "attachments/attachment-1.png",
        "manifest.json",
    ]
    assert files["attachments/attachment-1.png"] == PNG
    manifest = json.loads(files["manifest.json"])
    assert manifest["report_id"] == report_id
    assert manifest["schema_version"] == support.SCHEMA_VERSION
    assert manifest["app_version"] == __version__
    assert manifest["created_at"] == shown["created_at"]
    assert manifest["kind"] == "bug"
    assert manifest["files"] == [
        {"path": path, "bytes": len(files[path]), "sha256": hashlib.sha256(files[path]).hexdigest()}
        for path in ("summary.md", "diagnostics.json", "attachments/attachment-1.png")
    ]
    assert files["manifest.json"].decode() == shown["contents"]["manifest"]
    assert files["diagnostics.json"].decode() == shown["contents"]["diagnostics"]
    summary = files["summary.md"].decode()
    assert summary == shown["contents"]["summary"]
    assert summary.startswith(f"# DataLab report {report_id}")
    assert "## What happened\n\nThe results table stayed empty" in summary
    assert "## What I expected\n\nThe rows to show." in summary
    assert "attachments/attachment-1.png" in summary
    # Never the person's own file name.
    assert all(b"screenshot" not in content for content in files.values())
    diagnostics = json.loads(files["diagnostics.json"])
    assert diagnostics["app"] == {"version": __version__, "profile": "practice"}
    assert diagnostics["context"] == {"tab": "workspace", "route": "/workspace"}
    assert set(diagnostics["system"]) == {"os", "os_version", "machine", "python", "docker"}
    assert diagnostics["browser"]["user_agent"].startswith("Mozilla/5.0")


def test_building_a_report_again_gives_the_same_bytes(settings, catalog):
    with TestClient(make_app(settings, catalog)) as client:
        saved = save_report(client)
    store = support.ReportStore(settings.data_dir / "support")
    report_id = saved["report_id"]
    first = store.bundle(report_id)
    report = store.report(report_id)
    assert support.preview(report, store.attachments(report)).zip == first
    # Gone from the folder: rebuilt, byte for byte.
    (store.folder(report_id) / f"{report_id}.zip").unlink()
    assert store.bundle(report_id) == first
    # A changed attachment is noticed, never packaged as if it were the saved one.
    (store.folder(report_id) / "attachments" / "attachment-1.png").write_bytes(b"other")
    (store.folder(report_id) / f"{report_id}.zip").unlink()
    with pytest.raises(support.SupportError):
        store.bundle(report_id)


def test_a_suggestion_has_no_expected_and_saved_reports_reopen(settings, catalog):
    with TestClient(make_app(settings, catalog)) as client:
        first = save_report(client, kind="suggestion", happened="Dark charts", attachments=[])
        second = save_report(client)
        listed = client.get("/api/support/reports").json()
        assert {r["report_id"] for r in listed} == {first["report_id"], second["report_id"]}
        reopened = client.get(f"/api/support/reports/{first['report_id']}").json()
        assert reopened["kind"] == "suggestion" and reopened["headline"] == "Dark charts"
        assert "What I expected" not in reopened["contents"]["summary"]
        assert "## Suggestion\n\nDark charts" in reopened["contents"]["summary"]
        assert client.delete(f"/api/support/reports/{first['report_id']}").status_code == 204
        assert [r["report_id"] for r in client.get("/api/support/reports").json()] == [
            second["report_id"]
        ]
        assert client.get(f"/api/support/reports/{first['report_id']}").status_code == 404
        assert client.get("/api/support/reports/../../etc").status_code == 404


def test_attachments_are_limited_and_named_safely(settings, catalog):
    with TestClient(make_app(settings, catalog)) as client:
        one = {"name": "x.png", "data_base64": base64.b64encode(PNG).decode()}
        too_many = client.post("/api/support/preview", json=draft(attachments=[one] * 6))
        assert too_many.status_code == 422
        big = base64.b64encode(b"0" * (support.MAX_ATTACHMENT_BYTES + 1)).decode()
        too_big = client.post(
            "/api/support/preview", json=draft(attachments=[{"name": "b.bin", "data_base64": big}])
        )
        assert too_big.status_code == 422
        bad = client.post(
            "/api/support/preview",
            json=draft(attachments=[{"name": "b.txt", "data_base64": "not base64!"}]),
        )
        assert bad.status_code == 422
        empty = client.post("/api/support/preview", json=draft(happened="   "))
        assert empty.status_code == 422
        shown = client.post(
            "/api/support/preview",
            json=draft(
                attachments=[
                    {"name": "../../evil.sh", "data_base64": base64.b64encode(b"echo").decode()},
                    {"name": "C:\\Users\\me\\shot.PNG", "data_base64": one["data_base64"]},
                    {"name": "shot.png", "data_base64": one["data_base64"]},
                    {"name": "no extension", "data_base64": one["data_base64"]},
                    {"name": "odd.p\u202eng", "data_base64": one["data_base64"]},
                ]
            ),
        ).json()
        paths = [f["path"] for f in shown["contents"]["files"]]
        assert paths[2:7] == [
            "attachments/attachment-1.sh.txt",
            "attachments/attachment-2.png",
            "attachments/attachment-3.png",
            "attachments/attachment-4",
            "attachments/attachment-5.png",
        ]
        assert shown["warnings"][-1].startswith(
            "You chose 5 files: they go in as attachment-1 to attachment-5, without their own "
            "names, but DataLab doesn't look inside them."
        )


def test_lines_that_look_like_identifiers_are_pointed_out(settings, catalog):
    with TestClient(make_app(settings, catalog)) as client:
        shown = client.post(
            "/api/support/preview",
            json=draft(happened="It broke\nfor participant P-00123 on 2026-09-01", attachments=[]),
        ).json()
    assert shown["warnings"] == [
        "What happened, line 2, may hold an ID, a date or an email address. "
        "Check it isn't participant data."
    ]


# ------------------------------------------------------------------ what's collected

CANARIES = {
    "sql": "CANARY_SQL_TABLE",
    "bind": "canary-bind-771",
    "query message": "canary-query-message",
    "result": "canary-result-row-81",
    "message text": "canary-message-text",
    "answer text": "canary-answer-text",
    "turn error": "canary-turn-error",
    "model message": "canary-model-message",
    "title": "Canary title words",
    "workflow name": "Canary workflow name",
    "workflow text": "canary-workflow-text",
    "person email": "someone.canary@example.org",
    "repo error": "canary-repo-error",
    "log value": "canary-log-value",
    "exception message": "canary-exception-message",
    "other library": "canary-third-party",
    "template id": "P-00456",
    "template email": "template.canary@example.org",
    "safety detail": "canary-safety-detail",
    "env password": "canary-env-pw-44",
    "model key": "canary-model-key-9Zr",
    "route query": "canary-route-query",
    "route file": "canary-route-file.csv",
    "api file path": "canary-api-file.csv",
    "api query": "canary-api-query",
    "agent email": "ua.canary@example.org",
    "destination": "canary-destination-folder",
    "page error message": "canary-page-error",
}


def test_nothing_but_allowlisted_metadata_reaches_the_bundle(
    settings, catalog, tmp_path, monkeypatch, memory_keychain
):
    monkeypatch.setenv("DATALAB_ORACLE_PASSWORD", CANARIES["env password"])
    memory_keychain.set_password(
        credentials.MODEL_KEY_SERVICE, credentials.MODEL_KEY_ACCOUNT, CANARIES["model key"]
    )
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        connection = db.connect(settings.database_file)
        store = ConversationStore(connection)
        talk = store.create(kind="data", mode="explore", title=CANARIES["title"], model="gpt-5.5")
        store.append(talk.id, "user_message", {"text": CANARIES["message text"]})
        store.append(talk.id, "agent_message", {"text": CANARIES["answer text"]})
        store.append(talk.id, "error", {"message": CANARIES["turn error"]})
        store.append(
            talk.id, "turn_finished", {"status": "failed", "error": CANARIES["turn error"]}
        )
        store.append(
            talk.id,
            "model_status",
            {
                "state": "retrying", "kind": "busy", "status": 429, "code": "rate_limit",
                "request_id": "req_abc123", "message": CANARIES["model message"],
            },
        )  # fmt: skip
        log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
        result = settings.data_dir / "sessions" / talk.id / "oracle" / "q.csv"
        result.parent.mkdir(parents=True)
        result.write_text(f"A\n{CANARIES['result']}\n")
        log.started(
            query_id="q_20260928T100000_aaaaaa", session_id=talk.id,
            sql=f"SELECT * FROM {CANARIES['sql']} WHERE x = :p", binds={"p": CANARIES["bind"]},
            tables=[CANARIES["sql"]], result_path=result,
        )  # fmt: skip
        log.finished("q_20260928T100000_aaaaaa", status="failed", message=CANARIES["query message"])
        connection.execute("UPDATE queries SET started_at = ?", (now(-5),))
        _add_run(connection, "run_20260928T100000_bbbbbb")
        connection.execute(
            "INSERT INTO repo_sync (repo, head, synced_at, error, error_at) "
            "VALUES ('knowledge', NULL, NULL, ?, ?)",
            (CANARIES["repo error"], now(-3)),
        )
        DestinationStore(connection).add(
            CANARIES["destination"], tmp_path / CANARIES["destination"]
        )
        (settings.data_dir / "logs" / "safety-last.json").write_text(
            json.dumps(
                {
                    "finished_at": now(-60),
                    "passed": False,
                    "results": [
                        {
                            "id": "db_read_only",
                            "status": "fail",
                            "detail": CANARIES["safety detail"],
                        }
                    ],
                }
            )
        )
        logging.getLogger("datalab.sessions.manager").warning(
            "A turn failed: %s", CANARIES["log value"]
        )
        try:
            raise RuntimeError(CANARIES["exception message"])
        except RuntimeError:
            logging.getLogger("datalab.api.files").exception(
                f"Couldn't open {CANARIES['template id']} for {CANARIES['template email']}: %s",  # noqa: G004
                CANARIES["log value"],
            )
        logging.getLogger("httpx").warning(f"formatted {CANARIES['other library']}")  # noqa: G004

        body = draft(
            route=f"/workspace/{talk.id}/files/{CANARIES['route file']}"
            f"?q={CANARIES['route query']}",
            user_agent=f"Mozilla/5.0 {CANARIES['agent email']} 123456789012",
            client_trail=[
                {
                    "at": now(-1),
                    "kind": "request",
                    "method": "GET",
                    "path": f"/api/conversations/{talk.id}/files/outputs/"
                    f"{CANARIES['api file path']}?x={CANARIES['api query']}",
                    "status": 404,
                },
                {
                    "at": datetime.now(UTC).timestamp() * 1000,
                    "kind": "request",
                    "method": "POST",
                    "path": "/api/nowhere/at/all",
                    "status": 0,
                },
                {"at": now(-1), "kind": "page_error", "error": CANARIES["page error message"]},
                {"at": now(-1), "kind": "page_error", "error": "TypeError"},
            ],
        )
        shown = client.post("/api/support/preview", json=body)
        assert shown.status_code == 200, shown.text
        preview_text = shown.text
        saved = client.post("/api/support/reports", json={"draft_id": shown.json()["draft_id"]})
        data = client.get(f"/api/support/reports/{saved.json()['report_id']}/bundle").content
        connection.close()

    everything = [preview_text, saved.text]
    everything += [content.decode("utf-8", "replace") for content in unzip(data).values()]
    folder = settings.data_dir / "support" / saved.json()["report_id"]
    everything += [(folder / name).read_text() for name in ("report.json", "status.json")]
    for text in everything:
        for what, canary in CANARIES.items():
            assert canary not in text, what
        assert str(settings.data_dir) not in text
        # The database's user and server, from the settings.
        assert settings.oracle.user not in text and "FREEPDB1" not in text
        assert str(Path.home()) not in text

    diagnostics = json.loads(unzip(data)["diagnostics.json"])
    # What is there: the IDs, statuses, classes and codes.
    assert diagnostics["context"] == {"tab": "workspace", "route": "/workspace/{conversation}/…"}
    operations = diagnostics["operations"]
    assert operations["conversation_id"] == talk.id
    assert operations["queries"] == ["q_20260928T100000_aaaaaa"]
    assert operations["workflow_runs"] == ["run_20260928T100000_bbbbbb"]
    assert operations["model_requests"] == ["req_abc123"]
    entries = diagnostics["trail"]["entries"]
    events = {(e["source"], e["event"]) for e in entries}
    assert ("database", "query") in events and ("workflows", "workflow_run") in events
    assert ("conversation", "turn_finished") in events and ("knowledge", "repo_sync") in events
    model = next(e for e in entries if e["event"] == "model_status")
    assert (model["state"], model["kind"], model["status"]) == ("retrying", "busy", 429)
    assert model["request_id"] == "req_abc123" and model["code"] == "rate_limit"
    request = next(e for e in entries if e["event"] == "request_failed")
    assert request["route"] == "/api/conversations/{…}/files/{…}/{…}"
    assert request["status"] == 404
    assert any(e["event"] == "request_unreachable" and e["route"] == "(other)" for e in entries)
    page_errors = [e for e in entries if e["event"] == "page_error"]
    assert [e.get("error_class") for e in page_errors] == [None, "TypeError"]
    # The template, with the ID and the email address in it taken out.
    logged = [
        e for e in entries if e["event"] == "log_error" and e.get("logger") == "datalab.api.files"
    ]
    assert any(
        e["error_class"] == "RuntimeError" and e["template"].startswith("Couldn't open for")
        for e in logged
    )
    assert diagnostics["browser"]["user_agent"] == "Mozilla/5.0 … …"
    assert diagnostics["safety_check"]["not_passed"] == [{"id": "db_read_only", "status": "fail"}]


def _add_run(connection, run_id: str) -> None:
    connection.execute(
        "INSERT INTO workflow_runs (id, workflow_name, mode, status, started_at, finished_at, "
        "delivery_status, started_by, workflow_path, workflow_source, workflow_blob, "
        "workflow_text, image_ref, image_digest, image_platform, host_platform, "
        "r_packages_sha256, runner_version, runtime_json, params_json, seed, reads_json, run_dir) "
        "VALUES (?, ?, 'run', 'failed', ?, ?, 'none', ?, 'w.yaml', 'file', 'sha256:0', ?, 'img', "
        "'sha256:1', 'linux/arm64', 'linux/arm64', '0', 'x', '{}', '{}', 1, '[]', 'runs/x')",
        (
            run_id,
            CANARIES["workflow name"],
            now(-10),
            now(-9),
            f"Someone <{CANARIES['person email']}>",
            CANARIES["workflow text"],
        ),
    )


def test_the_trail_is_bounded_in_entries_and_time(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        connection = db.connect(settings.database_file)
        log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
        for number in range(80):
            query_id = f"q_{number:03d}"
            log.started(
                query_id=query_id, session_id="pg_1", sql="SELECT 1 FROM dual", binds={},
                tables=[], result_path=settings.data_dir / "x.csv", origin="playground",
            )  # fmt: skip
            connection.execute(
                "UPDATE queries SET started_at = ?, status = 'succeeded' WHERE id = ?",
                (now(-100 + number), query_id),
            )
        for number in range(5):
            query_id = f"q_old_{number}"
            log.started(
                query_id=query_id, session_id="pg_1", sql="SELECT 1 FROM dual", binds={},
                tables=[], result_path=settings.data_dir / "x.csv", origin="playground",
            )  # fmt: skip
            connection.execute(
                "UPDATE queries SET started_at = ? WHERE id = ?", (now(-60 * 30), query_id)
            )
        connection.close()
        shown = client.post("/api/support/preview", json=draft(attachments=[])).json()
    diagnostics = json.loads(shown["contents"]["diagnostics"])
    entries = diagnostics["trail"]["entries"]
    assert len(entries) <= support.TRAIL_MAX
    ids = [e.get("id") for e in entries if e["event"] == "query"]
    assert "q_079" in ids and not any(i.startswith("q_old") for i in ids)
    assert len(diagnostics["operations"]["queries"]) == support.IDS_MAX
    cutoff = (datetime.now(UTC) - timedelta(hours=support.TRAIL_HOURS)).isoformat()
    assert all(e["at"] >= cutoff[:19] for e in entries)
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)


@pytest.mark.parametrize(
    ("route", "tab", "shown"),
    [
        ("/workspace", "workspace", "/workspace"),
        ("/workspace/c_0123456789abcdef?x=1#y", "workspace", "/workspace/{conversation}"),
        ("/workspace/P-00123", "workspace", "/workspace/…"),
        ("/sql/saved/My%20query", "sql", "/sql/…"),
        ("/settings/connections", "settings", "/settings/connections"),
        ("/help/getting-started/x", "help", "/help/getting-started/…"),
        ("/etc/passwd", "other", None),
        (None, "other", None),
    ],
)
def test_only_the_tab_and_a_conversation_id_are_kept_of_the_route(route, tab, shown):
    assert support.safe_route(route)[:2] == (tab, shown)


# ------------------------------------------------------------------ A: to a folder, then email


@pytest.fixture
def lab_settings(settings, monkeypatch):
    # The test's folders are in the system's temporary folder, which export
    # folders otherwise can't be.
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    monkeypatch.setattr("datalab.sessions.inputs._CONTAINERS_POSIX", ())
    return dataclasses.replace(settings, profile="real", repos=RepoSettings(access_contact=CONTACT))


def test_saving_to_a_folder_is_idempotent_and_never_says_delivered(lab_settings, catalog, tmp_path):
    folder = tmp_path / "Dropbox (Lab)" / "reports"
    folder.mkdir(parents=True)
    (tmp_path / "Dropbox (Lab)" / ".dropbox").write_text("")
    with TestClient(make_app(lab_settings, catalog)) as client:
        connection = db.connect(lab_settings.database_file)
        destination = DestinationStore(connection).add("Lab Dropbox", folder)
        connection.close()
        saved = save_report(client)
        report_id = saved["report_id"]
        name = f"{report_id}.zip"
        # A partial file an interrupted save left behind.
        (folder / f".{report_id}.zip.partial-0badf00d").write_bytes(b"half")
        url = f"/api/support/reports/{report_id}/save-to-folder"
        first = client.post(url, json={"destination_id": destination.id})
        assert first.status_code == 200, first.text
        shown = first.json()
        assert shown["states"] == ["saved_locally", "saved_to_folder"]
        [copy] = shown["folders"]
        assert copy["saved_to"] == "Saved to Lab Dropbox (on this computer)"
        assert copy["file"] == str(folder / name)
        assert copy["sync_note"].startswith("Dropbox will upload it")
        assert "can't confirm" in copy["sync_note"]
        assert "deliver" not in first.text.lower()
        assert sorted(p.name for p in folder.iterdir()) == [name]
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == saved["zip_sha256"]
        # Again: nothing new is written, and it's listed once.
        before = (folder / name).stat().st_mtime_ns
        again = client.post(url, json={"destination_id": destination.id}).json()
        assert len(again["folders"]) == 1
        assert (folder / name).stat().st_mtime_ns == before
        assert sorted(p.name for p in folder.iterdir()) == [name]
        # A different file with the name is left alone.
        (folder / name).write_bytes(b"someone else's file")
        clash = client.post(url, json={"destination_id": destination.id})
        assert clash.status_code == 422 and "left it as it is" in clash.json()["detail"]
        assert (folder / name).read_bytes() == b"someone else's file"


def test_an_unavailable_folder_keeps_the_report(lab_settings, catalog, tmp_path):
    folder = tmp_path / "USB" / "reports"
    folder.mkdir(parents=True)
    with TestClient(make_app(lab_settings, catalog)) as client:
        connection = db.connect(lab_settings.database_file)
        destination = DestinationStore(connection).add("USB drive", folder)
        connection.close()
        saved = save_report(client)
        folder.rmdir()
        url = f"/api/support/reports/{saved['report_id']}/save-to-folder"
        failed = client.post(url, json={"destination_id": destination.id})
        assert failed.status_code == 422
        assert "The report is kept" in failed.json()["detail"]
        kept = client.get(f"/api/support/reports/{saved['report_id']}").json()
        assert kept["states"] == ["saved_locally"] and kept["folders"] == []
        # Back again (the drive is connected), but read-only for now.
        folder.mkdir()
        folder.chmod(0o500)
        try:
            locked = client.post(url, json={"destination_id": destination.id})
        finally:
            folder.chmod(0o700)
        assert locked.status_code == 422
        assert "Your account can't save files" in locked.json()["detail"]
        assert "The report is kept" in locked.json()["detail"]
        assert list(folder.iterdir()) == []
        assert client.post(url, json={"destination_id": destination.id}).status_code == 200


def test_the_email_names_the_report_and_the_file_and_carries_no_diagnostics(
    lab_settings, catalog, tmp_path
):
    folder = tmp_path / "reports"
    folder.mkdir()
    with TestClient(make_app(lab_settings, catalog)) as client:
        connection = db.connect(lab_settings.database_file)
        destination = DestinationStore(connection).add("Reports", folder)
        log = AccessLog(connection, lab_settings.data_dir / "logs" / "audit.jsonl")
        log.started(
            query_id="q_trail_only", session_id="pg_1", sql="SELECT 1 FROM dual", binds={},
            tables=[], result_path=lab_settings.data_dir / "x.csv", origin="playground",
        )  # fmt: skip
        connection.execute("UPDATE queries SET started_at = ?", (now(-1),))
        connection.close()
        saved = save_report(client)
        report_id = saved["report_id"]
        shown = client.post(
            f"/api/support/reports/{report_id}/save-to-folder",
            json={"destination_id": destination.id},
        ).json()
    email = shown["email"]
    assert email["to"] == "ali@example.org" and email["contact"] == CONTACT
    assert email["subject"] == f"DataLab report {report_id}"
    assert f"Attach the file: {report_id}.zip, saved in Reports." in email["body"]
    assert "The results table stayed empty" in email["body"]
    mailto = email["mailto"]
    assert mailto.startswith("mailto:ali@example.org?subject=DataLab%20report%20")
    assert len(mailto) <= 1800
    text = unquote(mailto)
    assert report_id in text and "attach" in text.lower()
    # The diagnostics stay in the file.
    assert "q_trail_only" in json.dumps(saved["contents"]["diagnostics"])
    for diagnostic in ("q_trail_only", "schema_version", '"trail"', "## Diagnostics"):
        assert diagnostic not in text


def test_a_long_report_is_cut_short_in_the_email_link(lab_settings, catalog):
    with TestClient(make_app(lab_settings, catalog)) as client:
        saved = save_report(client, happened="word " * 1500, attachments=[])
    email = saved["email"]
    assert len(email["mailto"]) <= 1800
    assert "cut short here" in unquote(email["mailto"])


# ------------------------------------------------------------------ B: the private support repo


class FakeGitHub:
    """GitHub's API for one repository, in memory."""

    def __init__(self, *, private: bool = True, push: bool = True) -> None:
        self.private = private
        self.push = push
        self.files: dict[str, bytes] = {}
        self.commits: list[tuple[str, str]] = []  # (message, path)
        self.offline = False
        self.repo_status = 200
        self.fail_put: dict[str, int] = {}  # path -> status to answer once
        self.rate_limited = False
        self.calls: list[tuple[str, str]] = []
        self.tokens_seen: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        self.tokens_seen.add(request.headers.get("authorization", ""))
        path = request.url.path
        self.calls.append((request.method, path))
        if self.rate_limited:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
        prefix = f"/repos/{REPO}"
        if path == prefix:
            if self.repo_status != 200:
                return httpx.Response(self.repo_status, json={"message": "Not Found"})
            return httpx.Response(
                200, json={"private": self.private, "permissions": {"push": self.push}}
            )
        if path == f"{prefix}/commits":
            wanted = request.url.params["path"]
            shas = [f"c{n}" for n, (_, p) in enumerate(self.commits) if p == wanted]
            return httpx.Response(200, json=[{"sha": shas[-1]}] if shas else [])
        if path.startswith(f"{prefix}/contents/"):
            file = path.removeprefix(f"{prefix}/contents/")
            if request.method == "GET":
                if file not in self.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(
                    200,
                    json={
                        "sha": support.git_blob_sha(self.files[file]),
                        "html_url": f"https://github.com/{REPO}/blob/main/{file}",
                    },
                )
            if request.method == "PUT":
                if file in self.fail_put:
                    return httpx.Response(self.fail_put.pop(file), json={})
                body = json.loads(request.content)
                if file in self.files or "sha" in body:
                    return httpx.Response(422, json={"message": "sha wasn't supplied"})
                self.files[file] = base64.b64decode(body["content"])
                self.commits.append((body["message"], file))
                return httpx.Response(
                    201,
                    json={
                        "content": {
                            "sha": support.git_blob_sha(self.files[file]),
                            "html_url": f"https://github.com/{REPO}/blob/main/{file}",
                        },
                        "commit": {"sha": f"c{len(self.commits) - 1}"},
                    },
                )
        return httpx.Response(404, json={})


TOKEN = "ghu_canary_token_value"


def github_app(settings, fake: FakeGitHub, *, signed_in: bool = True):
    if signed_in:
        TokenStore().save(Tokens(TOKEN, None, None, None))
    auth = GitHubAuth(
        "Iv23liTestClient", http=httpx.Client(transport=httpx.MockTransport(fake.handler))
    )
    connection = db.connect(settings.database_file)
    router = build_support_router(
        SupportServices(settings, connection, DestinationStore(connection), auth, lambda: "27.1")
    )
    app = FastAPI()
    app.include_router(router)
    return app, router


@pytest.fixture
def repo_settings(settings):
    return dataclasses.replace(
        settings,
        profile="real",
        repos=RepoSettings(support=REPO, client_id="Iv23liTestClient", access_contact=CONTACT),
    )


def send(client: TestClient, report_id: str) -> dict[str, Any]:
    response = client.post(f"/api/support/reports/{report_id}/send", json={"confirmed": True})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_send_is_confirmed_by_githubs_commit(repo_settings, caplog):
    fake = FakeGitHub()
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        state = client.get("/api/support/github", params={"check": True}).json()
        assert state["available"] and state["private"] is True and state["repo"] == REPO
        saved = save_report(client)
        report_id = saved["report_id"]
        unconfirmed = client.post(f"/api/support/reports/{report_id}/send", json={})
        assert unconfirmed.status_code == 400
        shown = send(client, report_id)
    github = shown["github"]
    assert shown["states"] == ["saved_locally", "confirmed_delivery"]
    assert github["state"] == "confirmed" and github["commit_sha"] == "c0"
    year, month = saved["created_at"][:4], saved["created_at"][5:7]
    zip_path = f"reports/{year}/{month}/{report_id}.zip"
    assert github["path"] == zip_path
    assert github["html_url"] == f"https://github.com/{REPO}/blob/main/{zip_path}"
    assert hashlib.sha256(fake.files[zip_path]).hexdigest() == saved["zip_sha256"]
    md = fake.files[f"reports/{year}/{month}/{report_id}.md"].decode()
    assert md == saved["contents"]["summary"]
    assert fake.commits == [
        (f"Report {report_id} (bug)", zip_path),
        (f"Report {report_id} (bug)", zip_path.replace(".zip", ".md")),
    ]
    assert fake.tokens_seen == {f"Bearer {TOKEN}"}
    assert TOKEN not in caplog.text
    assert TOKEN not in json.dumps(shown)
    status = (repo_settings.data_dir / "support" / report_id / "status.json").read_text()
    assert TOKEN not in status


def test_a_public_repository_is_refused(repo_settings):
    fake = FakeGitHub(private=False)
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        state = client.get("/api/support/github", params={"check": True}).json()
        assert state["private"] is False and not state["available"]
        assert "public repository" in state["message"]
        saved = save_report(client)
        shown = send(client, saved["report_id"])
    assert shown["github"]["state"] == "refused"
    assert "public repository" in shown["github"]["reason"]
    assert shown["states"] == ["saved_locally", "refused"]
    assert not any(method == "PUT" for method, _ in fake.calls)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda f: setattr(f, "repo_status", 404), "can't open"),
        (lambda f: setattr(f, "repo_status", 403), "can't open"),
        (lambda f: setattr(f, "push", False), "not add files"),
        (lambda f: setattr(f, "rate_limited", True), "limiting requests"),
        (lambda f: setattr(f, "offline", True), "couldn't be reached"),
    ],
)
def test_a_send_that_cant_happen_now_is_pending_with_the_reason(repo_settings, setup, reason):
    fake = FakeGitHub()
    setup(fake)
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        saved = save_report(client)
        shown = send(client, saved["report_id"])
    assert shown["states"] == ["saved_locally", "pending_retry"]
    assert shown["github"]["state"] == "pending" and reason in shown["github"]["reason"]
    assert shown["github"]["attempts"] == 1
    assert fake.commits == []
    # The report is kept.
    assert (repo_settings.data_dir / "support" / saved["report_id"] / "report.json").is_file()


def test_retrying_never_makes_a_second_commit(repo_settings):
    fake = FakeGitHub()
    fake.offline = True
    app, router = github_app(repo_settings, fake)
    with TestClient(app) as client:
        saved = save_report(client)
        report_id = saved["report_id"]
        assert send(client, report_id)["github"]["state"] == "pending"
        # Back online, but the summary fails: the ZIP is there, the send still pending.
        fake.offline = False
        year, month = saved["created_at"][:4], saved["created_at"][5:7]
        md_path = f"reports/{year}/{month}/{report_id}.md"
        fake.fail_put[md_path] = 502
        half = send(client, report_id)
        assert half["github"]["state"] == "pending" and half["github"]["attempts"] == 2
        assert len(fake.commits) == 1
        # At the next start: the identical ZIP counts as there, only the summary is added.
        assert router.retry_pending() == 1
        shown = client.get(f"/api/support/reports/{report_id}").json()
        assert shown["github"]["state"] == "confirmed" and shown["github"]["attempts"] == 3
        assert shown["github"]["commit_sha"] == "c0"  # the ZIP's commit, found again
        assert [path for _, path in fake.commits] == [md_path.replace(".md", ".zip"), md_path]
        # Confirmed: sending again asks GitHub nothing.
        calls = len(fake.calls)
        assert send(client, report_id)["github"]["state"] == "confirmed"
        assert len(fake.calls) == calls and len(fake.commits) == 2
        assert router.retry_pending() == 0


def test_a_different_file_at_the_path_is_never_replaced(repo_settings):
    fake = FakeGitHub()
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        saved = save_report(client)
        report_id = saved["report_id"]
        year, month = saved["created_at"][:4], saved["created_at"][5:7]
        path = f"reports/{year}/{month}/{report_id}.zip"
        fake.files[path] = b"a partial upload"
        shown = send(client, report_id)
    assert shown["github"]["state"] == "refused"
    assert "different file" in shown["github"]["reason"]
    assert fake.files[path] == b"a partial upload"
    assert fake.commits == []


def test_without_a_sign_in_only_the_folder_is_offered(repo_settings):
    fake = FakeGitHub()
    app, router = github_app(repo_settings, fake, signed_in=False)
    with TestClient(app) as client:
        state = client.get("/api/support/status").json()["github"]
        assert state["configured"] and not state["available"] and not state["signed_in"]
        assert "Sign in to GitHub" in state["message"]
        saved = save_report(client)
        refused = client.post(
            f"/api/support/reports/{saved['report_id']}/send", json={"confirmed": True}
        )
        assert refused.status_code == 409
    assert router.retry_pending() == 0
    assert fake.calls == []


def test_without_the_support_repo_the_maintainer_is_asked_to_set_it_up(lab_settings, catalog):
    with TestClient(make_app(lab_settings, catalog)) as client:
        state = client.get("/api/support/status").json()
    assert not state["github"]["configured"] and not state["github"]["available"]
    assert "Ask the maintainer to set up the support repo" in state["github"]["message"]
    assert state["contact"] == {"contact": CONTACT, "email": "ali@example.org"}


# ------------------------------------------------------------------ practice


def test_practice_saves_to_its_own_folder_and_never_uses_github(settings, catalog):
    practice = dataclasses.replace(
        settings, repos=RepoSettings(support=REPO, client_id="Iv23liTestClient")
    )
    TokenStore().save(Tokens(TOKEN, None, None, None))
    with TestClient(make_app(practice, catalog)) as client:
        state = client.get("/api/support/status").json()
        assert state["practice"] and not state["github"]["available"]
        assert state["github"]["repo"] is None
        saved = save_report(client)
        report_id = saved["report_id"]
        assert (
            client.post(
                f"/api/support/reports/{report_id}/send", json={"confirmed": True}
            ).status_code
            == 409
        )
        other = client.post(
            f"/api/support/reports/{report_id}/save-to-folder", json={"destination_id": "dest_x"}
        )
        assert other.status_code == 404
        url = f"/api/support/reports/{report_id}/save-to-folder"
        own_folder = settings.data_dir / "practice-exports"
        own_folder.mkdir(exist_ok=True)
        own_folder.chmod(0o500)
        try:
            locked = client.post(url, json={"destination_id": "practice"})
        finally:
            own_folder.chmod(0o700)
        assert locked.status_code == 422
        assert locked.json()["detail"].startswith(
            "Your account can't save files in Practice exports now. The report is kept"
        )
        assert list(own_folder.iterdir()) == []
        own = client.post(url, json={"destination_id": "practice"}).json()
    [copy] = own["folders"]
    assert copy["practice"] and copy["name"] == "Practice exports"
    assert Path(copy["file"]) == settings.data_dir / "practice-exports" / f"{report_id}.zip"
    assert own["states"] == ["saved_locally", "saved_to_folder"]


# ------------------------------------------------------------------ sign-in and JSON


def test_the_routes_need_the_sign_in_and_json_from_datalabs_own_page(settings, catalog, tmp_path):
    browser = BrowserSession(settings.port)
    app = make_app(settings, catalog, protect_api=True, browser=browser)
    with TestClient(app) as client:
        assert client.get("/api/support/reports").status_code == 401
        assert client.post("/api/support/preview", json=draft()).status_code == 401
        signed_in = client.get(browser.sign_in_path(), follow_redirects=False)
        client.cookies.set(browser.cookie_name, signed_in.cookies[browser.cookie_name])
        assert client.get("/api/support/reports").status_code == 200
        body = json.dumps(draft())
        as_form = client.post(
            "/api/support/preview", content=body, headers={"content-type": "text/plain"}
        )
        assert as_form.status_code == 403 and "JSON" in as_form.json()["detail"]
        cross_site = client.post(
            "/api/support/preview",
            content=body,
            headers={"content-type": "application/json", "sec-fetch-site": "same-site"},
        )
        assert cross_site.status_code == 403
        other_origin = client.post(
            "/api/support/preview",
            content=body,
            headers={"content-type": "application/json", "origin": "http://127.0.0.1:1"},
        )
        assert other_origin.status_code == 403
        shown = client.post(
            "/api/support/preview", content=body, headers={"content-type": "application/json"}
        )
        assert shown.status_code == 200
        saved = client.post("/api/support/reports", json={"draft_id": shown.json()["draft_id"]})
        report_id = saved.json()["report_id"]
        deleting = client.delete(f"/api/support/reports/{report_id}")
        assert deleting.status_code == 403  # no JSON content type
        assert (
            client.delete(
                f"/api/support/reports/{report_id}", headers={"content-type": "application/json"}
            ).status_code
            == 204
        )


# ------------------------------------------------------------------ review fixes


@pytest.mark.parametrize(
    "path",
    [
        ".evil.example/x",
        "@evil.example/x",
        "/x@evil.example/y",
        "//evil.example/x",
        "/repos/a/../../x",
        "/repos\\evil",
        "/repos/a?x=1",
        "/repos/a#x",
        "https://evil.example/x",
        "",
        "/repos/a b",
    ],
)
def test_the_github_token_only_ever_goes_to_api_github_com(path):
    seen: list[httpx.Request] = []
    TokenStore().save(Tokens(TOKEN, None, None, None))
    auth = GitHubAuth(
        "Iv23liTestClient",
        http=httpx.Client(
            transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))
        ),
    )
    with pytest.raises(ValueError):
        auth.request("GET", path)
    assert seen == []


def test_the_github_request_takes_no_headers_auth_or_redirects_of_its_own():
    seen: list[httpx.Request] = []
    TokenStore().save(Tokens(TOKEN, None, None, None))
    auth = GitHubAuth(
        "Iv23liTestClient",
        http=httpx.Client(
            transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))
        ),
    )
    for extra in ({"headers": {"x": "1"}}, {"auth": ("a", "b")}, {"follow_redirects": True}):
        with pytest.raises(ValueError):
            auth.request("GET", "/repos/a/b", **extra)
    assert seen == []
    auth.request("GET", "/repos/a/b/contents/reports/x.zip", params={"per_page": 1})
    [request] = seen
    assert request.url.scheme == "https" and request.url.host == "api.github.com"
    assert request.url.path == "/repos/a/b/contents/reports/x.zip"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_attachment_names_never_reach_the_report(repo_settings, tmp_path):
    fake = FakeGitHub()
    app, _ = github_app(repo_settings, fake)
    name = "participant P-00123 jane.doe@example.org 2026-09-01.png"
    with TestClient(app) as client:
        saved = save_report(
            client, attachments=[{"name": name, "data_base64": base64.b64encode(PNG).decode()}]
        )
        report_id = saved["report_id"]
        data = client.get(f"/api/support/reports/{report_id}/bundle").content
        send(client, report_id)
    folder = repo_settings.data_dir / "support" / report_id
    texts = [
        saved["email"]["mailto"] or "",
        unquote(saved["email"]["mailto"] or ""),
        saved["email"]["body"],
    ]
    texts += [c.decode("utf-8", "replace") for c in unzip(data).values()]
    texts += [(folder / f).read_text() for f in ("report.json", "status.json")]
    texts += [c.decode("utf-8", "replace") for c in fake.files.values()]
    texts += [p.name for p in folder.rglob("*")]
    for text in texts:
        for part in ("P-00123", "jane.doe", "2026-09-01", "participant"):
            assert part not in text
    assert "attachments/attachment-1.png" in unzip(data)


def test_names_the_disk_treats_as_one_dont_break_a_report(settings, catalog):
    nfc, nfd = "café.png", "café.png"
    one = base64.b64encode(PNG).decode()
    other = base64.b64encode(PNG + b"x").decode()
    with TestClient(make_app(settings, catalog)) as client:
        saved = save_report(
            client,
            attachments=[{"name": nfc, "data_base64": one}, {"name": nfd, "data_base64": other}],
        )
        reopened = client.get(f"/api/support/reports/{saved['report_id']}")
    assert reopened.status_code == 200
    assert [a["path"] for a in reopened.json()["attachments"]] == [
        "attachments/attachment-1.png",
        "attachments/attachment-2.png",
    ]


def test_a_report_that_doesnt_read_back_leaves_no_folder(settings, catalog, monkeypatch):
    real = support.ReportStore._attachments_in

    def changed(folder, report):
        found = real(folder, report)
        return {name: data + b"!" for name, data in found.items()}

    with TestClient(make_app(settings, catalog)) as client:
        shown = client.post("/api/support/preview", json=draft()).json()
        monkeypatch.setattr(support.ReportStore, "_attachments_in", staticmethod(changed))
        failed = client.post("/api/support/reports", json={"draft_id": shown["draft_id"]})
        assert failed.status_code == 422
        assert list((settings.data_dir / "support").iterdir()) == []
        monkeypatch.setattr(support.ReportStore, "_attachments_in", staticmethod(real))
        saved = save_report(client)
        # A saved report whose file was changed since: 409, not a 500.
        folder = settings.data_dir / "support" / saved["report_id"]
        (folder / "attachments" / "attachment-1.png").write_bytes(b"changed")
        reopened = client.get(f"/api/support/reports/{saved['report_id']}")
    assert reopened.status_code == 409


def test_a_long_non_ascii_summary_is_cut_to_the_longest_start_that_fits(lab_settings, catalog):
    with TestClient(make_app(lab_settings, catalog)) as client:
        saved = save_report(client, happened="été " * 1200, attachments=[])
    mailto = saved["email"]["mailto"]
    assert len(mailto) <= 1800
    text = unquote(mailto)
    assert "What happened" in text and "été été" in text
    assert "cut short here" in text
    # The longest that fits: a few more characters wouldn't.
    assert len(mailto) > 1800 - 60


def test_the_summary_is_stored_and_sent_as_it_was(repo_settings, monkeypatch):
    fake = FakeGitHub()
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        saved = save_report(client)
        report_id = saved["report_id"]
        stored = support.ReportStore(repo_settings.data_dir / "support").report(report_id)
        assert stored["summary_md"] == saved["contents"]["summary"]
        monkeypatch.setattr(support, "summary_text", lambda report: "rendered anew")
        (repo_settings.data_dir / "support" / report_id / f"{report_id}.zip").unlink()
        data = client.get(f"/api/support/reports/{report_id}/bundle").content
        send(client, report_id)
    assert hashlib.sha256(data).hexdigest() == saved["zip_sha256"]
    md = next(v for k, v in fake.files.items() if k.endswith(".md"))
    assert md.decode() == saved["contents"]["summary"]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}


@pytest.mark.parametrize("status", [204, 302, 500])
def test_an_unexpected_answer_about_the_file_is_pending_and_writes_nothing(repo_settings, status):
    fake = FakeGitHub()
    real = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/contents/" in request.url.path:
            fake.calls.append((request.method, request.url.path))
            return httpx.Response(status, headers={"location": "https://evil.example/"})
        return real(request)

    fake.handler = handler  # type: ignore[method-assign]
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        shown = send(client, save_report(client)["report_id"])
    assert shown["github"]["state"] == "pending"
    assert not any(method == "PUT" for method, _ in fake.calls)


def test_a_403_with_retry_after_is_a_rate_limit(repo_settings):
    fake = FakeGitHub()
    real = fake.handler
    fake.handler = lambda request: (  # type: ignore[method-assign]
        httpx.Response(403, headers={"retry-after": "60"}, json={})
        if request.url.path.endswith("ihs-support")
        else real(request)
    )
    app, _ = github_app(repo_settings, fake)
    with TestClient(app) as client:
        shown = send(client, save_report(client)["report_id"])
    assert "limiting requests" in shown["github"]["reason"]


def test_retrying_at_start_stops_once_github_is_out_of_reach(repo_settings):
    fake = FakeGitHub()
    fake.offline = True
    app, router = github_app(repo_settings, fake)
    with TestClient(app) as client:
        ids = [save_report(client, attachments=[])["report_id"] for _ in range(3)]
        for report_id in ids:
            send(client, report_id)
        assert router.retry_pending() == 0
        attempts = [
            client.get(f"/api/support/reports/{i}").json()["github"]["attempts"] for i in ids
        ]
    assert sorted(attempts) == [1, 1, 2]  # one was tried, then the rest waited


def test_only_a_github_com_link_is_kept():
    assert (
        support.github_link("https://github.com/o/r/blob/main/x")
        == "https://github.com/o/r/blob/main/x"
    )
    for url in (
        "javascript:alert(1)",
        "https://github.com.evil.example/x",
        "http://github.com/x",
        None,
    ):
        assert support.github_link(url) is None


def test_a_link_into_place_never_replaces_a_file(tmp_path):
    from datalab.exports import Folder

    (tmp_path / "part").write_text("new")
    (tmp_path / "taken").write_text("old")
    with Folder.at(tmp_path) as folder:
        with pytest.raises(FileExistsError):
            folder.link_new("part", "taken")
        folder.link_new("part", "free")
        assert sorted(folder.names()) == ["free", "part", "taken"]
    assert (tmp_path / "taken").read_text() == "old"
    assert (tmp_path / "free").read_text() == "new"
