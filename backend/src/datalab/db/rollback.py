"""Rolling DataLab's database back to a backup, for an older DataLab.

A newer DataLab may have changed the database's layout (its migrations). If
the person goes back to the older version, that version can't safely use the
newer layout, so `datalab rollback` puts back the backup taken just before
the newer version changed it.

The rules:
- It only runs when the installed DataLab is older than the database, that
  is, the database has migrations this DataLab doesn't have.
- It only restores a backup this DataLab can read (its migrations are all
  ones this DataLab has), and only if the backup's checksum still matches.
- Anything recorded in the database since that backup (new conversations,
  queries, and so on) is listed first. If there is any, it refuses unless
  the person confirms (`--yes`).
- The database as it is now is backed up first (reason "restore"), so a
  rollback can itself be undone.
- Only the database is restored. Conversation workspaces, runs, and repos are
  the person's files and stay as they are: a conversation made after the
  update keeps its folder under `sessions/`, though the older DataLab no
  longer lists it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from datalab.db import backups as backups_module
from datalab.db.backups import Backup, applied_migrations, list_backups, take_backup

# Tables whose rows are the schema itself, not anything the person recorded.
_BOOKKEEPING = {"schema_migrations"}
# How many new conversations to name when listing what a rollback drops.
_EXAMPLES = 5


class RollbackRefused(RuntimeError):
    """A rollback can't or shouldn't run; the message says why."""


@dataclass(frozen=True)
class Loss:
    """What restoring a backup drops from one table."""

    table: str
    added: int  # rows recorded since the backup
    changed: int  # rows that existed then but have changed since
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    backup: Backup
    # Migrations in the database now that this DataLab doesn't have.
    undone: tuple[str, ...]
    losses: tuple[Loss, ...] = field(default=())

    @property
    def loses_data(self) -> bool:
        return any(loss.added or loss.changed for loss in self.losses)


def plan(
    database_file: Path,
    known: set[str],
    *,
    choose: str | None = None,
) -> Plan:
    """Work out what `datalab rollback` would do, changing nothing.

    `known` is the set of migrations the installed DataLab has. `choose`
    names a backup folder; by default the newest usable backup is used.
    """
    if not database_file.exists():
        raise RollbackRefused("There's no DataLab database yet, so there's nothing to roll back.")
    with _read_only(database_file) as live:
        current = applied_migrations(live)
    undone = tuple(m for m in current if m not in known)
    if not undone:
        raise RollbackRefused(
            "This DataLab already understands the database as it is, so there's "
            "nothing to roll back. (Rollback is for going back to an older DataLab "
            "after a newer one updated the database.)"
        )
    available = list_backups(backups_module.backups_dir(database_file))
    usable = [b for b in available if set(b.migrations) <= known]
    if choose is not None:
        chosen = next((b for b in available if b.name == choose), None)
        if chosen is None:
            names = ", ".join(b.name for b in available) or "none"
            raise RollbackRefused(f"There's no backup called {choose!r}. Backups: {names}.")
        if chosen not in usable:
            raise RollbackRefused(
                f"The backup {choose!r} is from a newer DataLab too "
                f"({chosen.schema_version}), so this DataLab can't use it either."
            )
    elif usable:
        chosen = usable[-1]
    else:
        raise RollbackRefused(
            "None of the backups in "
            f"{backups_module.backups_dir(database_file)} is one this DataLab can use. "
            "Reinstall the newer DataLab to keep using your data."
        )
    if not chosen.verify():
        raise RollbackRefused(
            f"The backup {chosen.name!r} has changed or is damaged since it was made "
            "(its checksum doesn't match), so it won't be restored."
        )
    return Plan(chosen, undone, tuple(compare(database_file, chosen.file)))


def restore(database_file: Path, chosen: Backup, *, app_version: str) -> Backup:
    """Replace the database with a backup; return the backup of what it replaced.

    DataLab must not be running. The replaced database is backed up first,
    and kept even if that means keeping more than the usual number.
    """
    if not chosen.verify():
        raise RollbackRefused(f"The backup {chosen.name!r} doesn't match its checksum.")
    folder = backups_module.backups_dir(database_file)
    live = sqlite3.connect(database_file, isolation_level=None)
    try:
        before = take_backup(
            live, folder, app_version=app_version, reason="restore", protect=(chosen.name,)
        )
        with _read_only(chosen.file) as source:
            # The backup API replaces the whole database in one step, WAL and
            # all, rather than a file copy under an open write-ahead log.
            source.backup(live)
        status = live.execute("PRAGMA quick_check").fetchone()[0]
        if status != "ok":
            raise RollbackRefused(f"The restored database failed SQLite's check: {status}")
    finally:
        live.close()
    return before


def compare(database_file: Path, backup_file: Path) -> list[Loss]:
    """What the database has now that the backup doesn't, per table."""
    losses = []
    with _read_only(backup_file) as connection:
        connection.execute("ATTACH DATABASE ? AS live", (_read_only_uri(database_file),))
        then = _tables(connection, "main")
        for table in _tables(connection, "live"):
            if table in _BOOKKEEPING:
                continue
            columns = _columns(connection, "live", table)
            if table not in then:
                added = _count(connection, f"SELECT count(*) FROM live.{_quote(table)}")
                changed = 0
            else:
                old_columns = {c for c, _ in _columns(connection, "main", table)}
                common = [c for c, _ in columns if c in old_columns]
                keys = [c for c, pk in columns if pk and c in old_columns] or common
                match = " AND ".join(f"b.{_quote(k)} IS l.{_quote(k)}" for k in keys)
                added = _count(
                    connection,
                    f"SELECT count(*) FROM live.{_quote(table)} AS l WHERE NOT EXISTS "
                    f"(SELECT 1 FROM main.{_quote(table)} AS b WHERE {match})",
                )
                listed = ", ".join(_quote(c) for c in common)
                differing = _count(
                    connection,
                    f"SELECT count(*) FROM (SELECT {listed} FROM live.{_quote(table)} "
                    f"EXCEPT SELECT {listed} FROM main.{_quote(table)})",
                )
                changed = max(differing - added, 0)
            if added or changed:
                examples = _new_conversations(connection, then) if table == "conversations" else ()
                losses.append(Loss(table, added, changed, examples))
    return losses


def describe(losses: tuple[Loss, ...] | list[Loss]) -> list[str]:
    """Plain lines for the person: what a rollback would drop."""
    lines = []
    for loss in losses:
        what = loss.table.replace("_", " ")
        parts = []
        if loss.added:
            parts.append(f"{loss.added} new")
        if loss.changed:
            parts.append(f"{loss.changed} changed")
        lines.append(f"{what}: {' and '.join(parts)}")
        lines.extend(f"    {example}" for example in loss.examples)
    return lines


def _new_conversations(connection: sqlite3.Connection, then: set[str]) -> tuple[str, ...]:
    if "conversations" not in then:
        query = "SELECT created_at, title FROM live.conversations"
    else:
        query = (
            "SELECT created_at, title FROM live.conversations AS l WHERE NOT EXISTS "
            "(SELECT 1 FROM main.conversations AS b WHERE b.id = l.id)"
        )
    rows = connection.execute(f"{query} ORDER BY created_at LIMIT {_EXAMPLES + 1}").fetchall()
    shown = tuple(f"{str(when)[:16].replace('T', ' ')}  {title}" for when, title in rows)
    return shown[:_EXAMPLES] + (("…",) if len(rows) > _EXAMPLES else ())


def _tables(connection: sqlite3.Connection, schema: str) -> set[str]:
    return {
        r[0]
        for r in connection.execute(
            f"SELECT name FROM {schema}.sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(connection: sqlite3.Connection, schema: str, table: str) -> list[tuple[str, int]]:
    rows = connection.execute(f"PRAGMA {schema}.table_info({_quote(table)})").fetchall()
    return [(r[1], r[5]) for r in rows]


def _count(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0])


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


@contextmanager
def _read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection to a database file, closed afterwards."""
    connection = sqlite3.connect(_read_only_uri(path), uri=True)
    try:
        yield connection
    finally:
        connection.close()
