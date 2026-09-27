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
