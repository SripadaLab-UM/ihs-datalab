"""The update marker, and recovering from an update that was interrupted."""

import json
import os
import sqlite3
import time

import pytest

from datalab import __version__, updates
from datalab.db.backups import applied_migrations, list_backups
from datalab.updates import UpdateError
from tests.test_backups import FIRST, add_conversation, open_database, rows, use_migrations


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A data folder in use by DataLab 0.1.0, which has only the first migrations."""
    older = use_migrations(monkeypatch, FIRST)
    connection = open_database(tmp_path / "datalab.sqlite", version="0.1.0")
    add_conversation(connection, "c1", "Sleep and mood", "2026-09-01T10:00:00")
    connection.close()
    tmp_path.joinpath("older").write_text("\n".join(sorted(older)))
    return tmp_path


def older(data_dir) -> set[str]:
    return set(data_dir.joinpath("older").read_text().split("\n"))


def schema(path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return applied_migrations(connection)
    finally:
        connection.close()


def test_an_update_records_its_progress_and_backs_up_first(data_dir):
    database = data_dir / "datalab.sqlite"
    marker = updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")

    assert marker.state == "backed-up" and marker.backup.startswith("0.2.0-")
    [backup] = list_backups(data_dir / "backups")
    assert backup.reason == "update" and backup.from_version == "0.1.0"
    assert updates.read_marker(data_dir) == marker
    assert updates.advance(data_dir, "installed").state == "installed"
    assert updates.advance(data_dir, "switched").state == "switched"
    with pytest.raises(UpdateError):
        updates.advance(data_dir, "installed")  # never backwards
    with pytest.raises(UpdateError, match="hasn't finished"):
        updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")


def test_the_new_version_finishes_the_update_and_reuses_its_backup(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    _age(database)  # the old version closed the database before the backup
    updates.advance(data_dir, "switched")

    recovery = updates.recover(data_dir, database, app_version="0.2.0", known=set())
    assert recovery is not None and recovery.outcome == "finishing"
    use_migrations(monkeypatch, None)
    open_database(database, version="0.2.0").close()

    # The update's backup already held the database as it was: no second one.
    assert [b.reason for b in list_backups(data_dir / "backups")] == ["update"]
    assert updates.finish(data_dir, "0.1.0") is None  # not the version being installed
    assert updates.finish(data_dir, "0.2.0") is not None
    assert updates.read_marker(data_dir) is None
    [line] = (data_dir / "logs" / "updates.jsonl").read_text().splitlines()
    assert json.loads(line)["outcome"] == "finished"


def test_a_database_changed_after_the_update_backup_is_backed_up_again(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    connection = sqlite3.connect(database, isolation_level=None)
    add_conversation(connection, "c2", "Step counts", "2026-09-20T09:30:00")
    connection.close()
    _age(database, by=-10)  # changed after the backup

    use_migrations(monkeypatch, None)
    open_database(database, version="0.2.0").close()

    found = list_backups(data_dir / "backups")
    assert [b.reason for b in found] == ["update", "migrate"]
    assert found[-1].reason == "migrate" and found[-1].from_version == "0.1.0"
    assert len(rows(found[-1].file, "conversations")) == 2


def test_an_update_that_never_changed_the_database_is_cleared(data_dir):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")

    # The launcher still opens 0.1.0: the switch never happened.
    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "abandoned"
    assert "wasn't changed" in recovery.message
    assert updates.read_marker(data_dir) is None
    assert updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir)) is None


def test_an_interrupted_update_that_changed_the_database_is_undone(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    use_migrations(monkeypatch, None)
    open_database(database, version="0.2.0").close()  # 0.2.0 migrated, then crashed

    # Back on 0.1.0, which can't use the new layout.
    use_migrations(monkeypatch, FIRST)
    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "undone"
    assert schema(database) == sorted(older(data_dir))
    assert updates.read_marker(data_dir) is None
    open_database(database, version="0.1.0").close()  # it opens normally again


def test_it_wont_undo_an_update_if_that_drops_new_work(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    use_migrations(monkeypatch, None)
    connection = open_database(database, version="0.2.0")
    add_conversation(connection, "c2", "Step counts", "2026-09-20T09:30:00")
    connection.close()

    use_migrations(monkeypatch, FIRST)
    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "needs-you"
    assert "datalab rollback" in recovery.message
    assert len(rows(database, "conversations")) == 2  # left as it was
    assert updates.read_marker(data_dir) is not None


def test_an_unreadable_marker_is_set_aside_when_the_data_is_fine(data_dir):
    database = data_dir / "datalab.sqlite"
    updates.marker_path(data_dir).write_text("{not json")

    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "abandoned"
    assert not updates.marker_path(data_dir).exists()
    [aside] = data_dir.glob("update-unreadable-*.json")
    assert aside.read_text() == "{not json"


def _age(path, by: float = 60) -> None:
    """Set a file's (and its -wal's) modification time `by` seconds in the past."""
    when = time.time() - by
    for file in (path, path.with_name(path.name + "-wal")):
        if file.exists():
            os.utime(file, (when, when))


class TestStartup:
    @pytest.fixture
    def serve(self, data_dir, monkeypatch):
        """`datalab serve`, stopping short of running the server."""
        from datalab import cli

        monkeypatch.setenv("DATALAB_PROFILE", "practice")
        monkeypatch.setenv("DATALAB_DATA_DIR", str(data_dir))
        monkeypatch.setattr("datalab.trial.refuse_if_running", lambda settings: None)
        monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)

        def create_app(settings, **_):
            from datalab import db

            db.connect(settings.database_file).close()

        monkeypatch.setattr("datalab.app.create_app", create_app)
        return lambda: cli.main(["serve", "--no-browser"])

    def test_it_says_what_happened_to_an_interrupted_update(self, data_dir, serve, capsys):
        database = data_dir / "datalab.sqlite"
        updates.begin(data_dir, database, from_version=__version__, to_version="99.0.0")

        assert serve() == 0  # still the version the update never replaced

        assert "didn't finish" in capsys.readouterr().out
        assert updates.read_marker(data_dir) is None

    def test_the_new_version_clears_the_marker_once_it_has_started(
        self, data_dir, serve, monkeypatch, capsys
    ):
        database = data_dir / "datalab.sqlite"
        updates.begin(data_dir, database, from_version="0.0.9", to_version=__version__)
        use_migrations(monkeypatch, None)

        assert serve() == 0

        assert f"Finishing the update from 0.0.9 to {__version__}" in capsys.readouterr().out
        assert updates.read_marker(data_dir) is None
        assert len(schema(database)) == len(use_migrations(monkeypatch, None))

    def test_an_older_version_wont_start_on_a_newer_database(
        self, data_dir, serve, monkeypatch, capsys
    ):
        use_migrations(monkeypatch, None)
        open_database(data_dir / "datalab.sqlite", version="0.2.0").close()
        use_migrations(monkeypatch, FIRST)

        assert serve() == 1
        assert "datalab rollback" in capsys.readouterr().out


def test_versions_keep_their_pre_release_part():
    assert updates.same_version("0.1.0a2", "v0.1.0-alpha.2")
    assert updates.same_version("0.2.0rc1", "0.2.0-rc.1")
    assert not updates.same_version("0.1.0", "0.1.0a1")
    assert not updates.same_version("0.1.0a1", "0.1.0a2")


def test_it_leaves_it_to_the_person_if_the_undo_fails(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    use_migrations(monkeypatch, None)
    open_database(database, version="0.2.0").close()
    use_migrations(monkeypatch, FIRST)

    def fails(*args, **kwargs):
        raise updates.rollback.RollbackRefused("The restored database failed SQLite's check")

    monkeypatch.setattr(updates.rollback, "restore", fails)
    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "needs-you"
    assert updates.read_marker(data_dir) is not None


def test_a_backup_that_isnt_the_updates_own_is_never_used(data_dir, monkeypatch):
    database = data_dir / "datalab.sqlite"
    marker = updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
    # Another backup under the same name, as if the first had been replaced.
    path = updates.marker_path(data_dir)
    path.write_text(path.read_text().replace(marker.backup_sha256 or "", "0" * 64))
    use_migrations(monkeypatch, None)
    open_database(database, version="0.2.0").close()
    use_migrations(monkeypatch, FIRST)

    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "needs-you"


def test_an_update_that_added_a_used_column_isnt_undone_silently(data_dir, monkeypatch, tmp_path):
    from tests.test_backups import with_extra_migration

    database = data_dir / "datalab.sqlite"
    updates.begin(data_dir, database, from_version="0.1.0", to_version="0.3.0")
    with_extra_migration(monkeypatch, tmp_path, "ALTER TABLE queries ADD COLUMN reviewer TEXT;")
    connection = open_database(database, version="0.3.0")
    connection.execute("UPDATE queries SET reviewer = 'second analyst'")
    connection.close()
    use_migrations(monkeypatch, FIRST)

    recovery = updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))

    assert recovery is not None and recovery.outcome == "needs-you"
    assert "reviewer" in [
        c[1] for c in sqlite3.connect(database).execute("PRAGMA table_info(queries)")
    ]


def test_an_update_wont_begin_or_recover_while_a_datalab_is_running(data_dir):
    from datalab.datalock import DataFolderInUse
    from tests.test_rollback import hold_in_another_process

    database = data_dir / "datalab.sqlite"
    with hold_in_another_process(data_dir):
        with pytest.raises(DataFolderInUse):
            updates.begin(data_dir, database, from_version="0.1.0", to_version="0.2.0")
        with pytest.raises(DataFolderInUse):
            updates.recover(data_dir, database, app_version="0.1.0", known=older(data_dir))
    assert updates.read_marker(data_dir) is None
    assert list_backups(data_dir / "backups") == []


def test_the_running_datalab_can_begin_an_update_under_its_own_lock(data_dir):
    from datalab import datalock

    datalock.refuse_second_instance(data_dir, "practice")  # as `datalab serve` does
    marker = updates.begin(
        data_dir, data_dir / "datalab.sqlite", from_version="0.1.0", to_version="0.2.0"
    )
    assert marker.state == "backed-up" and datalock.held(data_dir)
