"""DataLab's local SQLite database.

Migrations are plain numbered `.sql` files in `migrations/`, applied in order
at startup. Each runs once. The database is backed up before an app update
runs new migrations (see docs/DISTRIBUTION.md).
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; return the names of those applied."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {row[0] for row in connection.execute("SELECT name FROM schema_migrations")}
    applied = []
    for migration in sorted(_migration_files(), key=lambda m: m.name):
        if migration.name in done:
            continue
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


def _migration_files():
    folder = resources.files(__package__).joinpath("migrations")
    return [f for f in folder.iterdir() if f.name.endswith(".sql")]
