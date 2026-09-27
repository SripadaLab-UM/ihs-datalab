"""Settings: Connections, Storage, Updates and Copy diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import __version__, credentials, db, setup, updates
from datalab.api.settings import SettingsServices, build_settings_router
from datalab.config import OracleSettings, Settings
from datalab.data.access_log import AccessLog
from datalab.db import backups
from datalab.sessions.store import ConversationStore
from tests.conftest import MemoryKeychain

PASSWORD = "canary-pw-7Qx"
MODEL_KEY = "canary-key-9Zr"
LAB_ORACLE = OracleSettings(
    host="db.canary-host.example",
    port=1521,
    service="CANARYSVC",
    user="SVC_CANARY_USER",
    keychain_service="datalab-oracle",
    read_only_roles=("IHS_2025_RO",),
    allowed_schemas=frozenset({"IHS_2025"}),
)


@pytest.fixture
def keychain(monkeypatch) -> MemoryKeychain:
    """The OS keychain, in memory: no test writes to this computer's."""
    fake = MemoryKeychain()
    monkeypatch.setattr(credentials, "keyring", fake)
    monkeypatch.setattr(
        credentials, "_from_keychain", lambda service, account: fake.saved.get((service, account))
    )
    monkeypatch.delenv("DATALAB_ORACLE_PASSWORD", raising=False)
    monkeypatch.delenv("DATALAB_MODEL_API_KEY", raising=False)
    return fake


def real_settings(tmp_path: Path, oracle: OracleSettings | None = LAB_ORACLE) -> Settings:
    return Settings(profile="real", data_dir=tmp_path / "data", oracle=oracle)


class Harness:
    def __init__(self, settings: Settings, **services) -> None:
        self.settings = settings
        self.connection = db.connect(settings.database_file)
        self.password_changes = 0
        self.busy: set[str] = set()

        def changed() -> None:
            self.password_changes += 1

        services.setdefault("password_changed", changed)
        services.setdefault("turn_running", lambda cid: cid in self.busy)
        services.setdefault("docker_version", lambda: "27.1.1 (linux/arm64)")
        app = FastAPI()
        app.include_router(
            build_settings_router(SettingsServices(settings, self.connection, **services))
        )
        self.client = TestClient(app)

    @property
    def root(self) -> Path:
        return self.settings.data_dir

    def remove(self, kind: str, item_id: str, **extra):
        return self.client.post(
            "/api/settings/storage/remove", json={"kind": kind, "id": item_id, **extra}
        )

    def group(self, group_id: str) -> dict:
        usage = self.client.get("/api/settings/storage").json()
        return next(g for g in usage["groups"] if g["id"] == group_id)


@pytest.fixture
def real(tmp_path, keychain) -> Harness:
    return Harness(real_settings(tmp_path))


@pytest.fixture
def practice(settings, keychain) -> Harness:
    return Harness(settings)


# ------------------------------------------------------------ connections


def test_connections_show_the_database_and_whether_secrets_are_saved(real, keychain):
    shown = real.client.get("/api/settings/connections").json()
    assert shown["profile"] == "real"
    assert shown["oracle"]["dsn"] == "db.canary-host.example:1521/CANARYSVC"
    assert shown["oracle"]["user"] == "SVC_CANARY_USER"
    assert shown["oracle"]["read_only_roles"] == ["IHS_2025_RO"]
    assert (shown["oracle"]["password"], shown["oracle"]["can_set_password"]) == ("missing", True)
    assert (shown["model"]["key"], shown["model"]["can_set_key"]) == ("missing", True)
    assert shown["read_only_because"] is None


def test_a_keychain_that_refuses_is_a_plain_503(real, keychain, monkeypatch):
    from keyring.errors import KeyringLocked

    def locked(*args):
        raise KeyringLocked("locked")

    monkeypatch.setattr(keychain, "set_password", locked)
    for path, body in (
        ("database-password", {"password": PASSWORD}),
        ("model-key", {"key": MODEL_KEY}),
    ):
        refused = real.client.put(f"/api/settings/connections/{path}", json=body)
        assert refused.status_code == 503 and "keychain" in refused.json()["detail"]
        assert PASSWORD not in refused.text and MODEL_KEY not in refused.text
    assert real.password_changes == 0


def test_secrets_are_saved_to_the_keychain_and_never_returned(real, keychain):
    saved = real.client.put(
        "/api/settings/connections/database-password", json={"password": PASSWORD}
    )
    assert saved.status_code == 204 and saved.content == b""
    assert keychain.saved[("datalab-oracle", "SVC_CANARY_USER")] == PASSWORD
    assert real.password_changes == 1  # the data service reconnects with it
    key = real.client.put("/api/settings/connections/model-key", json={"key": f"  {MODEL_KEY}\n"})
    assert key.status_code == 204
    assert (
        keychain.saved[(credentials.MODEL_KEY_SERVICE, credentials.MODEL_KEY_ACCOUNT)] == MODEL_KEY
    )

    shown = real.client.get("/api/settings/connections").json()
    assert (shown["oracle"]["password"], shown["model"]["key"]) == ("keychain", "keychain")
    # Replacing works the same way.
    assert (
        real.client.put(
            "/api/settings/connections/database-password", json={"password": PASSWORD + "2"}
        ).status_code
        == 204
    )
    for path in ("connections", "storage", "updates", "diagnostics"):
        body = real.client.get(f"/api/settings/{path}").text
        assert PASSWORD not in body and MODEL_KEY not in body, path


def test_bad_secrets_are_refused_without_echoing_them(real, keychain):
    for body in ({"password": ""}, {"password": f"{PASSWORD}\nsecond line"}):
        refused = real.client.put("/api/settings/connections/database-password", json=body)
        assert refused.status_code == 422
        assert PASSWORD not in refused.text
    refused = real.client.put("/api/settings/connections/model-key", json={"key": f"{MODEL_KEY} x"})
    assert refused.status_code == 422 and MODEL_KEY not in refused.text
    assert keychain.writes == 0


def test_an_environment_override_is_shown_as_such(real, keychain, monkeypatch):
    monkeypatch.setenv("DATALAB_ORACLE_PASSWORD", PASSWORD)
    shown = real.client.get("/api/settings/connections").json()
    assert shown["oracle"]["password"] == "environment"
    assert PASSWORD not in json.dumps(shown)


def test_the_practice_profile_only_shows_its_synthetic_database(practice, keychain):
    shown = practice.client.get("/api/settings/connections").json()
    assert shown["oracle"]["practice"] is True
    assert shown["oracle"]["can_set_password"] is False and shown["oracle"]["password"] is None
    assert shown["model"]["can_set_key"] is False
    assert "synthetic" in shown["read_only_because"]
    assert (
        practice.client.put(
            "/api/settings/connections/database-password", json={"password": PASSWORD}
        ).status_code
        == 403
    )
    assert (
        practice.client.put(
            "/api/settings/connections/model-key", json={"key": MODEL_KEY}
        ).status_code
        == 403
    )
    assert keychain.writes == 0


def test_no_database_configured_yet(tmp_path, keychain):
    h = Harness(real_settings(tmp_path, oracle=None))
    assert h.client.get("/api/settings/connections").json()["oracle"]["configured"] is False
    refused = h.client.put("/api/settings/connections/database-password", json={"password": "x"})
    assert refused.status_code == 409
    tested = h.client.post("/api/settings/connections/test").json()
    assert tested["database"]["ok"] is False


def test_test_connection_asks_the_database_and_u_m_gpt(tmp_path, keychain):
    asked = []

    def check(oracle, limits, *, practice):
        asked.append((oracle.user, practice))
        return setup.DatabaseCheck(True, "Connected, read-only.", ("IHS_2025_RO",), True)

    def models(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {MODEL_KEY}"
        return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}, {"id": "claude-x"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(models))
    h = Harness(
        real_settings(tmp_path), check_database=check, model_http=http, model_key=lambda: MODEL_KEY
    )
    tested = h.client.post("/api/settings/connections/test").json()
    assert asked == [("SVC_CANARY_USER", False)]
    assert tested["database"] == {
        "ok": True,
        "message": "Connected, read-only.",
        "enabled_roles": ["IHS_2025_RO"],
        "read_only": True,
    }
    # Only approved models count: not another company's.
    assert tested["model"]["ok"] is True and "1 approved model." in tested["model"]["message"]
    assert MODEL_KEY not in json.dumps(tested)


def test_test_connection_says_what_went_wrong(tmp_path, keychain):
    refused = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    h = Harness(
        real_settings(tmp_path),
        check_database=lambda *a, **k: setup.DatabaseCheck(False, "Can't reach the database"),
        model_http=refused,
        model_key=lambda: MODEL_KEY,
    )
    tested = h.client.post("/api/settings/connections/test").json()
    assert tested["model"] == {"ok": False, "message": "U-M GPT refused the key."}
    assert tested["database"]["ok"] is False

    def missing() -> str:
        raise credentials.MissingCredential(
            "No U-M GPT key saved. Add it in Settings → Connections."
        )

    h = Harness(real_settings(tmp_path / "b"), model_key=missing)
    assert (
        "No U-M GPT key saved"
        in h.client.post("/api/settings/connections/test").json()["model"]["message"]
    )


def test_database_problems_name_the_code_never_the_server():
    class OracleError(Exception):
        pass

    assert (
        setup._database_problem(
            OracleError("ORA-01017: invalid username/password; logon denied"), practice=False
        )
        == "The database refused the user name or password."
    )
    reach = setup._database_problem(
        OracleError(
            "DPY-6005: cannot connect to database (CONNECTION_ID=x). db.canary-host.example"
        ),
        practice=False,
    )
    assert "VPN" in reach and "DPY-6005" in reach and "canary-host" not in reach
    assert "synthetic database" in setup._database_problem(OracleError("timed out"), practice=True)
    other = setup._database_problem(OracleError("ORA-01924: role 'X' not granted"), practice=False)
    assert "ORA-01924" in other and "VPN" not in other


def test_datalab_setup_saves_through_the_same_checks(tmp_path, keychain, monkeypatch):
    monkeypatch.setenv("DATALAB_PROFILE", "real")
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "custom"))
    lab = tmp_path / "lab.toml"
    lab.write_text(
        '[oracle]\nhost = "h"\nservice = "s"\nuser = "U"\nallowed_schemas = ["IHS_2025"]\n'
    )
    answers = iter([f" {MODEL_KEY} ", PASSWORD])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: next(answers))
    assert setup.setup(None, lab, update=False) == 0
    assert (
        keychain.saved[(credentials.MODEL_KEY_SERVICE, credentials.MODEL_KEY_ACCOUNT)] == MODEL_KEY
    )
    assert keychain.saved[("datalab-oracle", "U")] == PASSWORD

    # A key that can't be saved: nothing is, and setup says so with its exit code.
    answers = iter([f"{MODEL_KEY} with spaces"])
    assert setup.setup(None, None, update=True) == 1


# ---------------------------------------------------------------- storage


def add_run(
    connection: sqlite3.Connection,
    root: Path,
    run_id: str,
    *,
    status="succeeded",
    of_run=None,
    finished: bool | None = None,
    delivery="none",
):
    finished = status not in ("queued", "running") if finished is None else finished
    connection.execute(
        "INSERT INTO workflow_runs (id, workflow_name, mode, of_run, status, started_at, "
        "finished_at, delivery_status, "
        "started_by, workflow_path, workflow_source, workflow_blob, workflow_text, image_ref, "
        "image_digest, image_platform, host_platform, r_packages_sha256, runner_version, "
        "runtime_json, params_json, seed, reads_json, run_dir) VALUES (?, 'Weekly steps', ?, ?, "
        "?, '2026-09-27T10:00:00+00:00', ?, ?, 'Someone <s@example.org>', 'weekly.yaml', 'file', "
        "'sha256:0', 'name: x', 'img', 'sha256:1', 'linux/arm64', 'linux/arm64', '0', "
        "'datalab x', '{}', '{}', 1, '[]', ?)",
        (
            run_id,
            "replay" if of_run else "run",
            of_run,
            status,
            "2026-09-27T10:05:00+00:00" if finished else None,
            delivery,
            f"runs/{run_id}",
        ),
    )
    folder = root / "runs" / run_id
    (folder / "steps" / "extract").mkdir(parents=True, exist_ok=True)
    (folder / "steps" / "extract" / "final.csv").write_text("ID\n1\n2\n")
    (folder / "workflow.yaml").write_text("name: x\n")
    (folder / "record.json").write_text(json.dumps({"id": run_id, "inputs_kept": True}))
    return folder


def add_playground_result(h: Harness, query_id: str, *, status="succeeded") -> Path:
    results = h.root / "playground" / "pg_0123456789ab" / "results"
    results.mkdir(parents=True, exist_ok=True)
    log = AccessLog(h.connection, h.root / "logs" / "audit.jsonl")
    file = results / f"{query_id}.csv"
    log.started(
        query_id=query_id, session_id="pg_0123456789ab", sql="SELECT 1 FROM dual", binds={},
        tables=[], origin="playground", result_path=file,
    )  # fmt: skip
    file.write_text("A\n1\n")
    (results / f"{query_id}.columns.json").write_text('{"columns": ["A"]}')
    if status != "running":
        log.finished(query_id, status=status, row_count=1, result_path=file)
    return file


def take_backup(h: Harness, reason: str) -> backups.Backup:
    return backups.take_backup(
        h.connection, backups.backups_dir(h.settings.database_file), app_version="0.1.0",
        reason=reason, keep=10,
    )  # fmt: skip


def test_storage_lists_what_uses_the_data_folder(practice):
    h = practice
    conversation = ConversationStore(h.connection).create(
        kind="data", mode="explore", title="Sleep and steps", model="gpt-5.5"
    )
    (h.root / "sessions" / conversation.id / "work").mkdir(parents=True)
    (h.root / "sessions" / conversation.id / "work" / "notes.md").write_text("x" * 1000)
    (h.root / "sessions" / conversation.id / "checkpoints").mkdir()
    (h.root / "sessions" / conversation.id / "checkpoints" / "blob").write_text("y" * 500)
    add_playground_result(h, "q_20260927T100000_aaaaaa")
    add_run(h.connection, h.root, "run_20260927T100000_bbbbbb")
    take_backup(h, "migrate")
    take_backup(h, "restore")
    (h.root / "practice-exports" / "2026-09-27 Sleep").mkdir(parents=True)

    usage = h.client.get("/api/settings/storage").json()
    groups = {g["id"]: g for g in usage["groups"]}
    assert list(groups) == [
        "database", "conversations", "playground", "runs", "backups", "exports", "logs", "other"
    ]  # fmt: skip
    assert usage["size_bytes"] > 0 and usage["free_bytes"] > 0
    (talk,) = groups["conversations"]["items"]
    assert talk["label"] == "Sleep and steps (data session)" and talk["size_bytes"] == 1500
    assert "workspace 1000 bytes" in talk["detail"] and "checkpoints 500 bytes" in talk["detail"]
    assert talk["removable"] is False  # the Workspace deletes conversations
    (result,) = groups["playground"]["items"]
    assert result["removable"] and result["detail"] == "1 rows"
    (run,) = groups["runs"]["items"]
    assert run["removable"] and "can't be replayed" in run["removing_loses"]
    kept = [b for b in groups["backups"]["items"] if b["kept"]]
    assert (
        len(kept) == 1 and kept[0]["needs_confirmation"] and "rollback" in kept[0]["kept_because"]
    )
    assert groups["database"]["items"][0]["removable"] is False
    audit = next(i for i in groups["logs"]["items"] if i["id"] == "audit.jsonl")
    assert audit["removable"] is False
    assert groups["exports"]["items"][0]["removable"] is False


def test_a_playground_result_can_go_but_not_while_its_query_runs(practice):
    h = practice
    done = add_playground_result(h, "q_20260927T100000_aaaaaa")
    running = add_playground_result(h, "q_20260927T100001_cccccc", status="running")

    busy = h.remove("playground-result", "q_20260927T100001_cccccc")
    assert busy.status_code == 409 and running.exists()
    removed = h.remove("playground-result", "q_20260927T100000_aaaaaa")
    assert removed.status_code == 200 and removed.json()["freed_bytes"] > 0
    assert not done.exists() and not done.with_suffix(".columns.json").exists()
    # The query stays in the history.
    assert (
        h.connection.execute(
            "SELECT status FROM queries WHERE id = 'q_20260927T100000_aaaaaa'"
        ).fetchone()[0]
        == "succeeded"
    )
    assert h.remove("playground-result", "q_20260927T100000_aaaaaa").status_code == 404


def test_a_runs_files_go_but_its_record_stays(practice):
    h = practice
    folder = add_run(h.connection, h.root, "run_20260927T100000_bbbbbb")
    removed = h.remove("run-files", "run_20260927T100000_bbbbbb")
    assert removed.status_code == 200
    assert [p.name for p in folder.iterdir()] == ["record.json"]
    record = json.loads((folder / "record.json").read_text())
    assert record["inputs_kept"] is False and "files_removed_at" in record
    row = h.connection.execute(
        "SELECT inputs_kept, status FROM workflow_runs WHERE id = 'run_20260927T100000_bbbbbb'"
    ).fetchone()
    assert tuple(row) == (0, "succeeded")
    again = h.remove("run-files", "run_20260927T100000_bbbbbb")
    assert again.status_code == 409 and "already removed" in again.json()["detail"]


def test_a_run_in_use_keeps_its_files(practice):
    h = practice
    going = add_run(h.connection, h.root, "run_20260927T100000_111111", status="running")
    original = add_run(h.connection, h.root, "run_20260927T100000_222222")
    add_run(
        h.connection,
        h.root,
        "run_20260927T100001_333333",
        status="queued",
        of_run="run_20260927T100000_222222",
    )

    for run_id, folder in (
        ("run_20260927T100000_111111", going),
        ("run_20260927T100000_222222", original),
    ):
        refused = h.remove("run-files", run_id)
        assert refused.status_code == 409, run_id
        assert (folder / "steps" / "extract" / "final.csv").exists()
    runs = {i["id"]: i for i in h.group("runs")["items"]}
    assert runs["run_20260927T100000_222222"]["not_removable_because"] == (
        "A Replay of this run is using its files."
    )
    assert (
        h.connection.execute(
            "SELECT inputs_kept FROM workflow_runs WHERE id = 'run_20260927T100000_222222'"
        ).fetchone()[0]
        == 1
    )


def test_a_run_still_delivering_keeps_its_files(practice):
    """A run is `succeeded` before its delivery, and gets `finished_at` only after."""
    h = practice
    delivering = add_run(
        h.connection, h.root, "run_20260927T100000_444444", finished=False, delivery="pending"
    )
    refused = h.remove("run-files", "run_20260927T100000_444444")
    assert refused.status_code == 409 and "still going" in refused.json()["detail"]
    h.connection.execute("UPDATE workflow_runs SET finished_at = '2026-09-27T10:06:00+00:00'")
    refused = h.remove("run-files", "run_20260927T100000_444444")
    assert refused.status_code == 409 and "delivering" in refused.json()["detail"]
    assert (delivering / "steps" / "extract" / "final.csv").exists()
    assert h.connection.execute("SELECT inputs_kept FROM workflow_runs").fetchone()[0] == 1
    listed = h.group("runs")["items"][0]
    assert listed["removable"] is False and "delivering" in listed["not_removable_because"]


def test_files_held_open_can_be_removed_on_a_retry(practice, monkeypatch):
    """On Windows an open file can't be removed: say so, and let the person try again."""
    from datalab import storage

    h = practice
    folder = add_run(h.connection, h.root, "run_20260927T100000_555555")
    real = storage._remove_entry

    def held(entry: Path) -> None:
        if entry.name == "steps":
            raise PermissionError("in use")
        real(entry)

    monkeypatch.setattr(storage, "_remove_entry", held)
    refused = h.remove("run-files", "run_20260927T100000_555555")
    assert refused.status_code == 409 and "Close it in other programs" in refused.json()["detail"]
    assert sorted(p.name for p in folder.iterdir()) == ["record.json", "steps"]
    assert json.loads((folder / "record.json").read_text())["files_removal_partial"] is True
    listed = h.group("runs")["items"][0]
    assert listed["removable"] and "couldn't be removed yet" in listed["detail"]

    monkeypatch.setattr(storage, "_remove_entry", real)
    assert h.remove("run-files", "run_20260927T100000_555555").status_code == 200
    assert [p.name for p in folder.iterdir()] == ["record.json"]
    assert json.loads((folder / "record.json").read_text())["files_removal_partial"] is False
    assert h.remove("run-files", "run_20260927T100000_555555").status_code == 409


def test_a_held_playground_result_or_backup_says_so(practice, monkeypatch):
    h = practice
    add_playground_result(h, "q_20260927T100000_aaaaaa")
    backup = take_backup(h, "manual")

    def refuse(*args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", refuse)
    monkeypatch.setattr(Path, "rename", refuse)
    for kind, item_id in (
        ("playground-result", "q_20260927T100000_aaaaaa"),
        ("backup", backup.name),
    ):
        refused = h.remove(kind, item_id)
        assert refused.status_code == 409 and "Close it" in refused.json()["detail"], kind
    monkeypatch.undo()
    assert backup.folder.exists()


def test_backups_go_and_a_rollbacks_backup_only_when_confirmed(practice):
    h = practice
    plain = take_backup(h, "migrate")
    restore = take_backup(h, "restore")
    assert h.remove("backup", plain.name).status_code == 200
    assert not plain.folder.exists()

    refused = h.remove("backup", restore.name)
    assert refused.status_code == 409 and "Confirm" in refused.json()["detail"]
    assert restore.folder.exists()
    assert h.remove("backup", restore.name, confirmed=True).status_code == 200
    assert not restore.folder.exists()
    # No half-removed folder is left behind looking like a backup.
    assert backups.list_backups(backups.backups_dir(h.settings.database_file)) == []


def test_an_updates_backup_stays_while_the_update_is_going(practice):
    h = practice
    marker = updates.begin(
        h.root, h.settings.database_file, from_version="0.1.0", to_version="0.2.0"
    )
    other = take_backup(h, "manual")
    refused = h.remove("backup", marker.backup)
    assert refused.status_code == 409 and "update in progress" in refused.json()["detail"]
    assert h.remove("backup", other.name).status_code == 200

    updates.marker_path(h.root).write_text("{not json")
    another = take_backup(h, "manual")
    assert h.remove("backup", another.name).status_code == 409  # can't tell which it needs


def test_storage_never_removes_the_database_the_audit_log_or_anything_else(practice):
    h = practice
    audit = h.root / "logs" / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}\n")
    for kind in ("database", "log", "conversation", "exports", "other", "sessions"):
        assert h.remove(kind, "datalab.sqlite").status_code == 422, kind
    for kind, item_id in [
        ("backup", "../datalab.sqlite"),
        ("backup", ".."),
        ("backup", "../logs"),
        ("run-files", "../datalab.sqlite"),
        ("run-files", "run_20260927T100000_bbbbbb/../../logs"),
        ("playground-result", "../../../datalab.sqlite"),
        ("playground-result", "*"),
    ]:
        assert h.remove(kind, item_id).status_code == 404, (kind, item_id)
    assert h.settings.database_file.exists() and audit.exists()


def test_links_are_never_followed_out_of_the_data_folder(practice, tmp_path):
    h = practice
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    (outside / "manifest.json").write_text("{}")

    folder = backups.backups_dir(h.settings.database_file)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "0.1.0-20260101-000000-abcdef").symlink_to(outside)
    assert h.remove("backup", "0.1.0-20260101-000000-abcdef").status_code == 404

    run = add_run(h.connection, h.root, "run_20260927T100000_dddddd")
    for entry in run.iterdir():
        if entry.is_dir():
            import shutil

            shutil.rmtree(entry)
        else:
            entry.unlink()
    run.rmdir()
    run.symlink_to(outside)
    assert h.remove("run-files", "run_20260927T100000_dddddd").status_code == 404

    results = h.root / "playground" / "pg_0123456789ab" / "results"
    results.mkdir(parents=True)
    (results / "q_20260927T100000_eeeeee.csv").symlink_to(outside / "keep.txt")
    assert h.remove("playground-result", "q_20260927T100000_eeeeee").status_code == 404
    assert (outside / "keep.txt").read_text() == "mine"

    # A link inside a run folder is removed as a link; what it points at stays.
    inside = add_run(h.connection, h.root, "run_20260927T100000_ffffff")
    (inside / "steps" / "sneaky").symlink_to(outside)
    assert h.remove("run-files", "run_20260927T100000_ffffff").status_code == 200
    assert (outside / "keep.txt").read_text() == "mine"


def test_a_folder_without_a_backup_manifest_is_left_alone(practice):
    h = practice
    folder = backups.backups_dir(h.settings.database_file) / "notes"
    folder.mkdir(parents=True)
    (folder / "mine.txt").write_text("x")
    refused = h.remove("backup", "notes")
    assert refused.status_code == 409 and (folder / "mine.txt").exists()


# ---------------------------------------------------------------- updates


def test_updates_show_the_version_marker_recovery_and_backups(settings, keychain):
    recovery = updates.Recovery("needs-you", "The update to 0.3.0 was interrupted…")
    h = Harness(settings, recovery=recovery)
    take_backup(h, "restore")
    updates.begin(h.root, settings.database_file, from_version=__version__, to_version="0.3.0")
    shown = h.client.get("/api/settings/updates").json()
    assert shown["version"] == __version__
    assert shown["check_available"] is False
    assert shown["check_message"] == "Update checks aren't set up yet."
    assert shown["marker"]["state"] == "backed-up" and shown["marker"]["to_version"] == "0.3.0"
    assert shown["recovery"] == {"outcome": "needs-you", "message": recovery.message}
    assert [b["reason"] for b in shown["backups"]] == ["update", "restore"]
    assert shown["backups"][1]["kept"] is True and shown["backups"][0]["usable"] is True
    assert shown["migrations_applied"] == len(db.known_migrations())


def test_updates_show_recent_updates_and_an_unreadable_marker(practice):
    h = practice
    updates.begin(h.root, h.settings.database_file, from_version="0.1.0", to_version=__version__)
    updates.finish(h.root, __version__)
    updates.marker_path(h.root).write_text("{broken")
    shown = h.client.get("/api/settings/updates").json()
    assert shown["marker"] is None and shown["marker_unreadable"] is True
    assert [(e["outcome"], e["to_version"]) for e in shown["history"]] == [
        ("finished", __version__)
    ]


# ------------------------------------------------------------ diagnostics

# Diagnostics never read the conversations or events tables at all, so the
# title and conversation canaries pass trivially; they stay as a guard in case
# that ever changes.
CANARIES = {
    "password": PASSWORD,
    "model key": MODEL_KEY,
    "env password": "canary-env-pw-44",
    "sql": "CANARY_TABLE_SQL",
    "bind": "canary-bind-P-0001",
    "result": "canary-result-value-81",
    "conversation": "canary-conversation-text",
    "title": "Canary title words",
    "safety detail": "canary-safety-detail",
    "log value": "canary-log-arg",
    "exception message": "canary-exception-message",
    "other library": "canary-third-party",
    "destination": "canary-destination-folder",
    "run text": "canary-run-workflow",
    "database host": "canary-host",
    "database user": "SVC_CANARY_USER",
}


def test_diagnostics_hold_metadata_and_nothing_else(tmp_path, keychain, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    settings = replace(real_settings(tmp_path), data_dir=home / "DataLab" / "real")
    h = Harness(settings)
    keychain.set_password("datalab-oracle", "SVC_CANARY_USER", PASSWORD)
    keychain.set_password(credentials.MODEL_KEY_SERVICE, credentials.MODEL_KEY_ACCOUNT, MODEL_KEY)
    monkeypatch.setenv("DATALAB_ORACLE_PASSWORD", CANARIES["env password"])

    store = ConversationStore(h.connection)
    talk = store.create(kind="data", mode="explore", title=CANARIES["title"], model="gpt-5.5")
    store.append(talk.id, "user_message", {"text": CANARIES["conversation"]})
    log = AccessLog(h.connection, h.root / "logs" / "audit.jsonl")
    result = h.root / "sessions" / talk.id / "oracle" / "q.csv"
    result.parent.mkdir(parents=True)
    result.write_text(f"A\n{CANARIES['result']}\n")
    log.started(
        query_id="q_20260927T100000_aaaaaa", session_id=talk.id,
        sql=f"SELECT * FROM {CANARIES['sql']} WHERE id = :p", binds={"p": CANARIES["bind"]},
        tables=[CANARIES["sql"]], result_path=result,
    )  # fmt: skip
    log.finished("q_20260927T100000_aaaaaa", status="failed", message=CANARIES["result"])
    add_run(h.connection, h.root, "run_20260927T100000_bbbbbb", status="failed")
    h.connection.execute("UPDATE workflow_runs SET workflow_text = ?", (CANARIES["run text"],))
    h.connection.execute(
        "INSERT INTO export_destinations (id, name, path, added_at) VALUES ('d', ?, ?, 'now')",
        (CANARIES["destination"], str(tmp_path / CANARIES["destination"])),
    )
    (h.root / "logs" / "safety-last.json").write_text(
        json.dumps(
            {
                "started_at": "2026-09-27T10:00:00",
                "finished_at": "2026-09-27T10:00:20",
                "passed": False,
                "passed_strict": False,
                "results": [
                    {"id": "no_key_in_containers", "promise": "p", "label": "L", "status": "fail",
                     "detail": CANARIES["safety detail"], "required": True},
                    {"id": "db_read_only", "promise": "p", "label": "L", "status": "pass",
                     "detail": CANARIES["safety detail"], "required": True},
                ],
            }
        )
    )  # fmt: skip
    logging.getLogger("datalab.sessions.manager").warning(
        "A turn failed: %s", CANARIES["log value"]
    )
    try:
        raise RuntimeError(CANARIES["exception message"])
    except RuntimeError:
        logging.getLogger("datalab.api.files").exception("Couldn't open %s", CANARIES["log value"])
    # As another library might: values already in the message (DataLab's own
    # log calls can't, since ruff's G rules refuse it).
    logging.getLogger("httpx").warning(f"formatted {CANARIES['other library']}")  # noqa: G004

    text = h.client.get("/api/settings/diagnostics").json()["text"]

    for what, canary in CANARIES.items():
        assert canary not in text, what
    assert str(home) not in text
    assert "~/DataLab/real" in text
    assert f"DataLab:               {__version__}" in text
    assert "27.1.1 (linux/arm64)" in text  # Docker
    assert "Profile:               real" in text
    assert "fail: no_key_in_containers" in text and "Results:               1 fail, 1 pass" in text
    assert "Database password:     from the environment" in text
    assert "U-M GPT key:           saved in the keychain" in text
    assert "Failed queries:        1" in text and "Failed workflow runs:  1" in text
    assert f"Latest:                {sorted(db.known_migrations())[-1]}" in text
    # What went wrong, without the values: the template and where it was raised.
    assert "'A turn failed: %s'" in text
    assert "RuntimeError at test_settings_api.py:" in text
    assert "RuntimeError" in text and "httpx" in text


def test_the_home_folder_is_shortened_to_a_tilde(monkeypatch, tmp_path):
    from datalab.diagnostics import home_as_tilde

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "me"))
    assert home_as_tilde(tmp_path / "me" / "Library" / "DataLab") == "~/Library/DataLab"
    assert home_as_tilde(tmp_path / "me") == "~"
    assert home_as_tilde(tmp_path / "meander" / "x") == str(tmp_path / "meander" / "x")


def test_settings_status_says_it_is_built(practice):
    assert practice.client.get("/api/settings/status").json() == {"available": True}
    assert os.environ.get("DATALAB_ORACLE_PASSWORD") is None


# ------------------------------------------------ Storage and the runner itself


async def test_storage_and_a_replay_never_both_go_ahead(tmp_path):
    """A Replay checks its original's kept inputs, then waits on Docker before it
    records itself. Storage removing the files in that gap must stop the Replay,
    and a Replay already recorded must stop Storage."""
    from datalab.storage import Storage, StorageRefused
    from datalab.workflows.runner import RunRefused
    from tests.test_workflow_runner import harness

    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml", seed=7)
    storage = Storage(h.settings, h.connection)
    plan = h.runner._plan

    async def removed_meanwhile(**kwargs):
        made = await plan(**kwargs)
        storage.remove("run-files", first["id"])  # the Replay's check has passed by now
        return made

    h.runner._plan = removed_meanwhile  # type: ignore[method-assign]
    with pytest.raises(RunRefused, match="removed"):
        await h.runner.replay(first["id"])
    assert h.connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 1
    assert [p.name for p in h.run_dir(first).iterdir()] == ["record.json"]

    h.runner._plan = plan  # type: ignore[method-assign]
    second = await h.run("weekly_steps.yaml", seed=8)
    replay = await h.runner.replay(second["id"])
    with pytest.raises(StorageRefused, match="Replay"):
        storage.remove("run-files", second["id"])
    await h.finish(replay)
    assert storage.remove("run-files", second["id"]).freed_bytes > 0


async def test_storage_waits_for_a_real_delivery(tmp_path):
    from datalab.storage import Storage, StorageRefused
    from tests.test_workflow_runner import harness

    h = harness(tmp_path)
    storage = Storage(h.settings, h.connection)
    deliver = h.runner._deliver
    started, go_on = asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await go_on.wait()
        return await deliver(*args, **kwargs)

    h.runner._deliver = slow  # type: ignore[method-assign]
    run_id = await h.runner.start("weekly_steps.yaml", {}, seed=7)
    await started.wait()
    assert h.runner.detail(run_id)["status"] == "succeeded"  # already, before delivery
    with pytest.raises(StorageRefused):
        storage.remove("run-files", run_id)
    go_on.set()
    await h.finish(run_id)
    assert storage.remove("run-files", run_id).freed_bytes > 0
