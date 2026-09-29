import sqlite3

import pytest

from datalab import db
from datalab.data.access_log import AccessLog


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "datalab.sqlite"
    connection = db.connect(path)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "queries" in tables
    assert db.migrate(connection) == []  # nothing left to apply
    connection.close()
    assert db.migrate(db.connect(path)) == []


def test_queries_from_before_origins_count_as_the_conversations(tmp_path):
    connection = sqlite3.connect(tmp_path / "old.sqlite", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    # A data folder from before migration 0006, with a query in it.
    for migration in sorted(db._migration_files(), key=lambda m: m.name):
        if migration.name >= "0006":
            break
        connection.executescript(migration.read_text())
        connection.execute("INSERT INTO schema_migrations VALUES (?, 'then')", (migration.name,))
    connection.execute(
        "INSERT INTO queries (id, session_id, started_at, status, sql_text) "
        "VALUES ('q1', 'c_1', 'then', 'succeeded', 'SELECT 1 FROM DUAL')"
    )
    assert "0006_query_origin.sql" in db.migrate(connection)
    log = AccessLog(connection, tmp_path / "audit.jsonl")
    [record] = log.for_session("c_1")
    assert (record.id, record.origin) == ("q1", "conversation")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO queries (id, session_id, origin, started_at, status, sql_text) "
            "VALUES ('q2', 'x', 'elsewhere', 'now', 'running', '')"
        )


def test_0011_keeps_existing_folders_and_deliveries(tmp_path):
    """A data folder from before 0011, with export folders and a delivery in it:
    the folders stay offered, and old deliveries record no name or sync app."""
    connection = sqlite3.connect(tmp_path / "old.sqlite", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for migration in sorted(db._migration_files(), key=lambda m: m.name):
        if migration.name >= "0011":
            break
        connection.executescript(migration.read_text())
        connection.execute("INSERT INTO schema_migrations VALUES (?, 'then')", (migration.name,))
    connection.execute(
        "INSERT INTO export_destinations (id, name, path, added_at, key) VALUES "
        "('dest_1', 'Synthetic Dropbox', '/synthetic/dropbox', 'then', 'lab-dropbox')"
    )
    connection.execute(
        "INSERT INTO workflow_runs (id, workflow_name, mode, status, started_at, started_by, "
        "workflow_path, workflow_source, workflow_blob, workflow_text, image_ref, image_digest, "
        "image_platform, host_platform, r_packages_sha256, runner_version, runtime_json, "
        "params_json, seed, reads_json, run_dir) VALUES ('run_1', 'w', 'run', 'succeeded', "
        "'then', 'Synthetic <s@example.org>', 'w.yaml', 'file', 'sha256:0', 'name: w', 'img', "
        "'sha256:1', 'linux/arm64', 'linux/arm64', 'sha256:2', '0', '{}', '{}', 1, '[]', 'runs/1')"
    )
    connection.execute(
        "INSERT INTO workflow_run_deliveries (id, run_id, destination_key, destination_id, "
        "destination_path, folder, files_json, manifest_sha256, delivered_at) VALUES "
        "('dl_1', 'run_1', 'lab-dropbox', 'dest_1', '/synthetic/dropbox', '/synthetic/dropbox/x', "
        "'[]', 'sha256:3', 'then')"
    )
    assert db.migrate(connection, app_version="0.0.0") == [
        "0011_export_folders.sql",
        "0012_kb_edits.sql",
        "0013_pipeline_edits.sql",
        "0014_express.sql",
    ]
    [folder] = connection.execute("SELECT * FROM export_destinations").fetchall()
    assert (folder["name"], folder["key"], folder["offered"]) == (
        "Synthetic Dropbox",
        "lab-dropbox",
        1,
    )
    [delivery] = connection.execute("SELECT * FROM workflow_run_deliveries").fetchall()
    assert delivery["destination_id"] == "dest_1"
    assert (delivery["destination_name"], delivery["sync_provider"]) == (None, None)
