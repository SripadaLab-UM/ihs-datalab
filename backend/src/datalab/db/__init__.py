"""DataLab's local SQLite database.

Migrations are plain numbered `.sql` files in `migrations/`, applied in order
at startup. Each runs once, and they only ever go forward: a released
migration is never changed or removed.

Before any migration runs on a database that already has data, the database
is backed up to `backups/<version>/` (see backups.py and docs/DISTRIBUTION.md).
If the backup fails, no migration runs. A database with migrations this
DataLab doesn't have was changed by a newer DataLab; it isn't opened, and
`datalab rollback` can put back the backup taken before that change.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from datalab.db import backups


class DatabaseNewerThanApp(RuntimeError):
    """The database was changed by a newer DataLab than this one."""

    def __init__(self, unknown: list[str], app_version: str) -> None:
        self.unknown = unknown
        super().__init__(
            f"This database was updated by a newer DataLab. It has changes that this "
            f"DataLab ({app_version}) doesn't know about ({', '.join(unknown)}). Reopen the "
            "newer DataLab, or run `datalab rollback` to go back to the backup taken "
            "before it changed the database."
        )


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def known_migrations() -> set[str]:
    """The migrations this DataLab has."""
    return {m.name for m in _migration_files()}


def migrate(connection: sqlite3.Connection, *, app_version: str | None = None) -> list[str]:
    """Apply pending migrations; return the names of those applied."""
    if app_version is None:
        from datalab import __version__ as app_version
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {row[0] for row in connection.execute("SELECT name FROM schema_migrations")}
    files = sorted(_migration_files(), key=lambda m: m.name)
    unknown = sorted(done - {m.name for m in files})
    if unknown:
        raise DatabaseNewerThanApp(unknown, app_version)
    pending = [m for m in files if m.name not in done]
    if pending and done:
        _back_up_before_migrating(connection, app_version)
    applied = []
    for migration in pending:
        # executescript commits any open transaction first, so the transaction
        # has to be part of the script for a migration to apply all-or-nothing.
        name = migration.name.replace("'", "''")
        script = (
            f"BEGIN;\n{migration.read_text()}\n"
            f"INSERT INTO schema_migrations VALUES ('{name}', datetime('now'));\nCOMMIT;"
        )
        try:
            connection.executescript(script)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        applied.append(migration.name)
    return applied


def _back_up_before_migrating(connection: sqlite3.Connection, app_version: str) -> None:
    path = _file_of(connection)
    if path is None:  # an in-memory database: nothing to keep
        return
    from datalab import updates

    data_dir = path.parent
    if updates.backup_covering(data_dir, path, app_version) is not None:
        return  # the update already backed up the database as it is now
    backups.take_backup(
        connection,
        backups.backups_dir(path),
        app_version=app_version,
        from_version=updates.from_version_for(data_dir, app_version),
        reason="migrate",
    )


def _file_of(connection: sqlite3.Connection) -> Path | None:
    for _, name, file in connection.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file) if file else None
    return None


def _migration_files():
    folder = resources.files(__package__).joinpath("migrations")
    return [f for f in folder.iterdir() if f.name.endswith(".sql")]
