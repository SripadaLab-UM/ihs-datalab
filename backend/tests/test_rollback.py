"""Rolling the database back for an older DataLab, and refusing when it's unsafe."""

import sqlite3

import pytest

from datalab import cli, db
from datalab.db import rollback
from datalab.db.backups import list_backups, take_backup
from datalab.db.rollback import RollbackRefused
from tests.test_backups import (
    FIRST,
    add_conversation,
    open_database,
    rows,
    use_migrations,
    with_extra_migration,
)


@pytest.fixture
def updated(tmp_path, monkeypatch):
    """A database made by an older DataLab, then updated by a newer one.

    Returns the database file and the migrations the older DataLab has.
    """
    path = tmp_path / "datalab.sqlite"
    older = use_migrations(monkeypatch, FIRST)
    connection = open_database(path, version="0.1.0")
    add_conversation(connection, "c1", "Sleep and mood", "2026-09-01T10:00:00")
    connection.close()
    use_migrations(monkeypatch, None)
    open_database(path, version="0.2.0").close()  # backed up, then migrated
    return path, older


def test_an_older_datalab_refuses_to_open_a_newer_database(updated, monkeypatch):
    path, _ = updated
    use_migrations(monkeypatch, FIRST)
    with pytest.raises(db.DatabaseNewerThanApp, match="datalab rollback"):
        db.connect(path)


def test_rollback_puts_back_the_backup_taken_before_the_update(updated):
    path, older = updated
    before = rows(path, "conversations")

    plan = rollback.plan(path, older)
    assert plan.backup.name.startswith("0.2.0-") and plan.backup.reason == "migrate"
    assert plan.undone == ("0003_attachments.sql", "0004_exports.sql", "0005_rigor.sql")
    assert not plan.loses_data

    kept = rollback.restore(path, plan.backup, app_version="0.1.0")

    assert [r[:7] for r in before] == rows(path, "conversations")
    connection = sqlite3.connect(path)
    assert db.backups.applied_migrations(connection) == sorted(older)
    connection.close()
    # What it replaced is kept, so the rollback can itself be undone.
    assert kept.reason == "restore" and len(kept.migrations) == 5
    assert kept.verify()


def test_rollback_lists_what_it_would_drop(updated):
    path, older = updated
    connection = sqlite3.connect(path, isolation_level=None)
    add_conversation(connection, "c2", "Step counts by cohort", "2026-09-20T09:30:00")
    connection.execute("UPDATE conversations SET title = 'Sleep, mood' WHERE id = 'c1'")
    connection.close()

    plan = rollback.plan(path, older)

    assert plan.loses_data
    losses = {loss.table: loss for loss in plan.losses}
    assert losses["conversations"].added == 1 and losses["conversations"].changed == 1
    assert losses["conversations"].examples == ("2026-09-20 09:30  Step counts by cohort",)
    assert losses["events"].added == 1
    assert "schema_migrations" not in losses
    # The Data accessed log isn't dropped: it's carried over.
    assert "queries" not in losses
    assert [(c.table, c.added) for c in plan.carried] == [("queries", 1)]
    lines = rollback.describe(plan.losses)
    assert "conversations: 1 new and 1 changed" in lines


def test_rows_in_tables_the_backup_lacks_count_as_dropped(updated):
    path, older = updated
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute(
        "INSERT INTO export_destinations VALUES ('d1', 'Dropbox', '/tmp/x', '2026-09-20')"
    )
    connection.close()
    losses = {loss.table: loss for loss in rollback.plan(path, older).losses}
    assert losses["export_destinations"].added == 1


def test_rollback_refuses_when_there_is_nothing_to_roll_back(updated):
    path, _ = updated
    with pytest.raises(RollbackRefused, match="nothing to roll back"):
        rollback.plan(path, db.known_migrations())
    with pytest.raises(RollbackRefused, match="no DataLab database"):
        rollback.plan(path.with_name("missing.sqlite"), db.known_migrations())


def test_rollback_only_restores_a_backup_the_older_datalab_can_use(updated):
    path, older = updated
    live = sqlite3.connect(path, isolation_level=None)
    newer = take_backup(live, path.parent / "backups", app_version="0.2.0", reason="manual")
    live.close()

    # The newest backup is from the newer layout too, so the older one is picked.
    assert rollback.plan(path, older).backup.reason == "migrate"
    with pytest.raises(RollbackRefused, match="newer DataLab too"):
        rollback.plan(path, older, choose=newer.name)
    with pytest.raises(RollbackRefused, match="no backup called"):
        rollback.plan(path, older, choose="0.0.9")


def test_rollback_refuses_a_damaged_backup(updated):
    path, older = updated
    [backup] = list_backups(path.parent / "backups")
    with backup.file.open("r+b") as file:
        file.seek(300)
        file.write(b"\x00\x01")
    with pytest.raises(RollbackRefused, match="checksum"):
        rollback.plan(path, older)


class TestCommand:
    @pytest.fixture
    def data_dir(self, updated, monkeypatch):
        path, _ = updated
        monkeypatch.setenv("DATALAB_PROFILE", "practice")
        monkeypatch.setenv("DATALAB_DATA_DIR", str(path.parent))
        monkeypatch.setattr("datalab.trial.refuse_if_running", lambda settings: None)
        # The installed DataLab is the older one.
        use_migrations(monkeypatch, FIRST)
        return path.parent

    def test_it_refuses_to_drop_anything_without_yes(self, data_dir, capsys):
        path = data_dir / "datalab.sqlite"
        connection = sqlite3.connect(path, isolation_level=None)
        add_conversation(connection, "c2", "Step counts by cohort", "2026-09-20T09:30:00")
        connection.close()
        before = rows(path, "conversations")

        assert cli.main(["rollback"]) == 1

        out = capsys.readouterr().out
        assert "Step counts by cohort" in out and "datalab rollback --yes" in out
        assert "stay on disk" in out
        assert rows(path, "conversations") == before  # nothing changed
        assert [b.reason for b in list_backups(data_dir / "backups")] == ["migrate"]

        assert cli.main(["rollback", "--yes"]) == 0
        assert [r[0] for r in rows(path, "conversations")] == ["c1"]
        assert "Rolled back" in capsys.readouterr().out
        db.connect(path).close()  # the older DataLab opens it again

    def test_it_lists_the_backups(self, data_dir, capsys):
        assert cli.main(["rollback", "--list"]) == 0
        out = capsys.readouterr().out
        assert "0.2.0" in out and "migrate" in out and "0002_conversations.sql" in out


def test_the_data_accessed_log_survives_a_rollback(updated):
    path, older = updated
    connection = sqlite3.connect(path, isolation_level=None)
    add_conversation(connection, "c2", "Step counts by cohort", "2026-09-20T09:30:00")
    connection.execute("UPDATE queries SET row_count = 42 WHERE id = 'q-c1'")
    connection.close()
    queries_before = rows(path, "queries")

    plan = rollback.plan(path, older)
    kept = rollback.restore(path, plan.backup, app_version="0.1.0")

    # Every query, new or changed since the update, is still recorded...
    assert rows(path, "queries") == queries_before
    # ...while the rest of the database is as the backup had it.
    assert [r[0] for r in rows(path, "conversations")] == ["c1"]
    assert kept.reason == "restore"


def test_if_the_log_cant_be_carried_over_nothing_changes(updated, monkeypatch):
    path, older = updated
    before = rows(path, "conversations")
    plan = rollback.plan(path, older)

    def broken(connection, table):
        raise sqlite3.IntegrityError("CHECK constraint failed: status")

    monkeypatch.setattr(rollback, "_merge", broken)
    with pytest.raises(RollbackRefused, match="nothing was changed"):
        rollback.restore(path, plan.backup, app_version="0.1.0")

    assert rows(path, "conversations") == before
    reasons = [b.reason for b in list_backups(path.parent / "backups")]
    assert reasons == ["migrate"]  # no leftover copy of the unchanged database
    assert not list((path.parent / "backups").glob(".incoming-*"))


def test_values_in_columns_the_older_layout_lacks_count_as_dropped(updated, monkeypatch, tmp_path):
    path, older = updated
    # A newer release adds a column, and a row uses it.
    with_extra_migration(monkeypatch, tmp_path, "ALTER TABLE queries ADD COLUMN reviewer TEXT;")
    open_database(path, version="0.3.0").close()
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("UPDATE queries SET reviewer = 'second analyst' WHERE id = 'q-c1'")
    connection.close()

    plan = rollback.plan(path, older)

    assert plan.loses_data
    [loss] = plan.losses
    assert (loss.table, loss.changed) == ("queries", 1)


def test_a_new_column_left_at_its_default_isnt_a_change(updated):
    path, older = updated  # 0005 added conversations.rigor_review, default 0
    assert not rollback.plan(path, older).loses_data
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("UPDATE conversations SET rigor_review = 1")
    connection.close()
    [loss] = rollback.plan(path, older).losses
    assert (loss.table, loss.added, loss.changed) == ("conversations", 0, 1)
