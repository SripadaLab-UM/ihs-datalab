"""Backing up the database before its layout changes."""

import json
import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest

from datalab import db
from datalab.db import backups
from datalab.db.backups import BackupFailed, list_backups, take_backup

ALL = sorted(db._migration_files(), key=lambda m: m.name)
# The layout of the first installable pre-release: queries and conversations.
FIRST = 2


def use_migrations(monkeypatch: pytest.MonkeyPatch, count: int | None) -> set[str]:
    """Make this DataLab have only its first `count` migrations (None: all)."""
    files = ALL if count is None else ALL[:count]
    monkeypatch.setattr(db, "_migration_files", lambda: list(files))
    return {m.name for m in files}


def open_database(path: Path, *, version: str = "0.1.0") -> sqlite3.Connection:
    """Open and migrate a database as DataLab `version` would."""
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode = WAL")
    db.migrate(connection, app_version=version)
    return connection


def add_conversation(connection: sqlite3.Connection, id: str, title: str, when: str) -> None:
    connection.execute(
        "INSERT INTO conversations (id, kind, mode, title, model, created_at, updated_at) "
        "VALUES (?, 'data', 'explore', ?, 'gpt-5.5', ?, ?)",
        (id, title, when, when),
    )
    connection.execute(
        "INSERT INTO events VALUES (?, 1, ?, 'user_message', ?)",
        (id, when, json.dumps({"text": f"Question in {title}"})),
    )
    connection.execute(
        "INSERT INTO queries (id, session_id, started_at, status, sql_text) "
        "VALUES (?, ?, ?, 'succeeded', 'SELECT 1 FROM dual')",
        (f"q-{id}", id, when),
    )


def rows(path: Path, table: str) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        connection.close()


def test_a_backup_includes_changes_still_in_the_write_ahead_log(tmp_path):
    live = db.connect(tmp_path / "datalab.sqlite")
    live.execute("PRAGMA wal_autocheckpoint = 0")  # keep everything in the -wal
    add_conversation(live, "c1", "Sleep and mood", "2026-09-01T10:00:00")
    assert (tmp_path / "datalab.sqlite-wal").stat().st_size > 0

    backup = take_backup(live, tmp_path / "backups", app_version="0.1.0", reason="manual")

    assert backup.folder == tmp_path / "backups" / "0.1.0"
    assert rows(backup.file, "conversations")[0][3] == "Sleep and mood"
    # One self-contained file, with nothing needed beside it.
    assert not backup.file.with_name("datalab.sqlite-wal").exists()
    check = sqlite3.connect(backup.file)
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    check.close()
    manifest = json.loads((backup.folder / "manifest.json").read_text())
    assert manifest["app_version"] == "0.1.0"
    assert manifest["schema_version"] == ALL[-1].name
    assert manifest["migrations"] == [m.name for m in ALL]
    assert manifest["sha256"] == backup.sha256 and backup.verify()
    assert manifest["created_at"] and manifest["reason"] == "manual"


def test_migrating_a_database_with_data_backs_it_up_first(tmp_path, monkeypatch):
    path = tmp_path / "datalab.sqlite"
    use_migrations(monkeypatch, FIRST)
    old = open_database(path)
    add_conversation(old, "c1", "Sleep and mood", "2026-09-01T10:00:00")
    old.close()

    use_migrations(monkeypatch, None)
    live = open_database(path, version="0.2.0")

    [backup] = list_backups(tmp_path / "backups")
    assert backup.name == "0.2.0" and backup.reason == "migrate"
    assert list(backup.migrations) == [m.name for m in ALL[:FIRST]]
    # The same conversation, before 0005 added its rigor_review column.
    assert rows(backup.file, "conversations") == [rows(path, "conversations")[0][:-1]]
    assert "attachments" not in _tables(backup.file)  # the layout from before
    assert "attachments" in _tables(path)
    live.close()


def test_new_or_up_to_date_databases_need_no_backup(tmp_path):
    path = tmp_path / "datalab.sqlite"
    db.connect(path).close()  # a new database: nothing to keep
    db.connect(path).close()  # nothing to migrate
    assert list_backups(tmp_path / "backups") == []


def test_no_migration_runs_if_the_backup_fails(tmp_path, monkeypatch):
    path = tmp_path / "datalab.sqlite"
    use_migrations(monkeypatch, FIRST)
    open_database(path).close()
    use_migrations(monkeypatch, None)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _: Usage(1, 1, 0))

    with pytest.raises(BackupFailed, match="disk space"):
        db.connect(path)

    assert "attachments" not in _tables(path)
    assert list(tmp_path.joinpath("backups").iterdir()) == []


def test_only_the_newest_backups_are_kept_and_nothing_else_is_touched(tmp_path):
    live = db.connect(tmp_path / "datalab.sqlite")
    folder = tmp_path / "backups"
    notes = folder / "my notes"
    notes.mkdir(parents=True)
    (notes / "keep.txt").write_text("mine")
    (folder / ".incoming-dead").mkdir()  # a backup cut off by a crash

    made = [take_backup(live, folder, app_version="0.1.0", reason="manual") for _ in range(5)]

    assert [b.name for b in made[:3]] == ["0.1.0", "0.1.0-2", "0.1.0-3"]
    assert list_backups(folder) == made[2:]
    assert (notes / "keep.txt").read_text() == "mine"
    assert not (folder / ".incoming-dead").exists()


def test_a_changed_backup_no_longer_verifies(tmp_path):
    live = db.connect(tmp_path / "datalab.sqlite")
    backup = take_backup(live, tmp_path / "backups", app_version="0.1.0", reason="manual")
    with backup.file.open("r+b") as file:
        file.seek(200)
        file.write(b"\xff")
    assert not backup.verify()


def test_a_folder_without_a_manifest_isnt_a_backup(tmp_path):
    (tmp_path / "backups" / "0.1.0").mkdir(parents=True)
    (tmp_path / "backups" / "0.1.0" / "datalab.sqlite").write_bytes(b"")
    assert list_backups(tmp_path / "backups") == []


def _tables(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
