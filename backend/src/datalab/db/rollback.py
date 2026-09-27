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
  rollback can itself be undone. Those backups are never removed
  automatically.
- The Data accessed log (`queries`) is never rolled back: its rows are
  carried over into the restored database, so the record of what was queried
  is never lost. Only values in columns the older layout lacks stay behind,
  in the "restore" backup; they count as dropped.
- Only the database is restored. Conversation workspaces, runs, and repos are
  the person's files and stay as they are: a conversation made after the
  update keeps its folder under `sessions/`, though the older DataLab no
  longer lists it.
"""

from __future__ import annotations

import secrets
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from datalab.db import backups as backups_module
from datalab.db.backups import Backup, applied_migrations, list_backups, take_backup

# Tables whose rows are the schema itself, not anything the person recorded.
_BOOKKEEPING = {"schema_migrations"}
# Tables a rollback never drops rows from: their rows are copied from the
# database being replaced into the restored one. `queries` is the Data
# accessed log, the record of every query DataLab ran; it has been in every
# layout since 0001.
CARRIED = ("queries",)
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
    # Rows the restore carries over rather than drops (the Data accessed log).
    carried: tuple[Loss, ...] = field(default=())

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
    dropped, carried = compare(database_file, chosen.file)
    return Plan(chosen, undone, tuple(dropped), tuple(carried))


def restore(database_file: Path, chosen: Backup, *, app_version: str) -> Backup:
    """Replace the database with a backup; return the backup of what it replaced.

    DataLab must not be running. The replaced database is backed up first
    (and that backup is never removed automatically). The Data accessed log
    is carried over from it into the restored database before anything is
    replaced, so a rollback never loses a record of what was queried.
    """
    if not chosen.verify():
        raise RollbackRefused(f"The backup {chosen.name!r} doesn't match its checksum.")
    folder = backups_module.backups_dir(database_file)
    live = sqlite3.connect(database_file, isolation_level=None)
    try:
        before = take_backup(
            live, folder, app_version=app_version, reason="restore", protect=(chosen.name,)
        )
        try:
            merged = _with_carried_rows(chosen, before, folder)
        except BaseException:
            # Nothing was replaced, so this copy of the unchanged database isn't needed.
            shutil.rmtree(before.folder, ignore_errors=True)
            raise
        try:
            with _read_only(merged) as source:
                # The backup API replaces the whole database in one step, WAL
                # and all, rather than a file copy under an open write-ahead log.
                source.backup(live)
            status = live.execute("PRAGMA quick_check").fetchone()[0]
            if status != "ok":
                with _read_only(before.file) as undo:
                    undo.backup(live)
                raise RollbackRefused(
                    f"The restored database failed SQLite's check ({status}), so the "
                    "database was put back as it was."
                )
        finally:
            shutil.rmtree(merged.parent, ignore_errors=True)
    finally:
        live.close()
    return before


def _with_carried_rows(chosen: Backup, before: Backup, folder: Path) -> Path:
    """A copy of `chosen` with the carried tables' rows from `before` merged in.

    Rows are matched by primary key; where a row is in both, the newer one
    (from `before`) wins, in the columns both layouts have.
    """
    work = folder / f"{backups_module.INCOMING}merge-{secrets.token_hex(4)}"
    work.mkdir()
    merged = work / backups_module.FILE
    try:
        shutil.copyfile(chosen.file, merged)
        connection = sqlite3.connect(merged.resolve().as_uri(), uri=True, isolation_level=None)
        try:
            connection.execute("ATTACH DATABASE ? AS newer", (_read_only_uri(before.file),))
            connection.execute("BEGIN")
            then = _tables(connection, "main")
            for table in CARRIED:
                if table not in then or table not in _tables(connection, "newer"):
                    continue
                _merge(connection, table)
            connection.execute("COMMIT")
            status = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        if status != "ok":
            raise RollbackRefused(f"The merged database failed SQLite's check: {status}")
    except sqlite3.Error as error:
        shutil.rmtree(work, ignore_errors=True)
        raise RollbackRefused(
            f"The Data accessed log couldn't be carried over into the backup ({error}), "
            "so nothing was changed."
        ) from error
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return merged


def _merge(connection: sqlite3.Connection, table: str) -> None:
    older = _columns(connection, "main", table)
    newer = {c.name for c in _columns(connection, "newer", table)}
    common = [c.name for c in older if c.name in newer]
    keys = [c.name for c in older if c.pk and c.name in newer]
    listed = ", ".join(_quote(c) for c in common)
    if keys:
        updates = ", ".join(f"{_quote(c)} = excluded.{_quote(c)}" for c in common if c not in keys)
        conflict = f"ON CONFLICT ({', '.join(_quote(k) for k in keys)}) " + (
            f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        )
    else:
        conflict = ""
    # "WHERE true" lets SQLite tell the upsert's ON from a join's.
    connection.execute(
        f"INSERT INTO main.{_quote(table)} ({listed}) "
        f"SELECT {listed} FROM newer.{_quote(table)} WHERE true {conflict}"
    )


def compare(database_file: Path, backup_file: Path) -> tuple[list[Loss], list[Loss]]:
    """What the database has now that the backup doesn't, per table.

    Returns what a restore would drop, and what it carries over instead (the
    carried tables). A value counts as dropped when it's in a column the
    backup's layout doesn't have and isn't that column's default.
    """
    dropped, carried = [], []
    with _read_only(backup_file) as connection:
        connection.execute("ATTACH DATABASE ? AS live", (_read_only_uri(database_file),))
        then = _tables(connection, "main")
        for table in _tables(connection, "live"):
            if table in _BOOKKEEPING:
                continue
            live_table = f"live.{_quote(table)}"
            if table not in then:
                added = _count(connection, f"SELECT count(*) FROM {live_table}")
                changed = changed_common = new_values = 0
            else:
                columns = _columns(connection, "live", table)
                old_names = {c.name for c in _columns(connection, "main", table)}
                common = [c.name for c in columns if c.name in old_names]
                keys = [c.name for c in columns if c.pk and c.name in old_names] or common
                same_key = " AND ".join(f"b.{_quote(k)} IS l.{_quote(k)}" for k in keys)
                same_row = " AND ".join(f"b.{_quote(c)} IS l.{_quote(c)}" for c in common)
                # Values in columns only the newer layout has, other than their default.
                extra = " OR ".join(
                    f"l.{_quote(c.name)} IS NOT ({c.default or 'NULL'})"
                    for c in columns
                    if c.name not in old_names
                )
                old_table = f"main.{_quote(table)}"
                added = _count(
                    connection,
                    f"SELECT count(*) FROM {live_table} AS l WHERE NOT EXISTS "
                    f"(SELECT 1 FROM {old_table} AS b WHERE {same_key})",
                )
                differs = f"NOT EXISTS (SELECT 1 FROM {old_table} AS b WHERE {same_row})"
                existing = (
                    f"SELECT count(*) FROM {live_table} AS l WHERE EXISTS "
                    f"(SELECT 1 FROM {old_table} AS b WHERE {same_key})"
                )
                changed_common = _count(connection, f"{existing} AND {differs}")
                changed = (
                    _count(connection, f"{existing} AND ({differs} OR {extra})")
                    if extra
                    else changed_common
                )
                new_values = (
                    _count(connection, f"SELECT count(*) FROM {live_table} AS l WHERE {extra}")
                    if extra
                    else 0
                )
            if table in CARRIED and table in then:
                if added or changed_common:
                    carried.append(Loss(table, added, changed_common))
                if new_values:
                    note = "(values in columns the older DataLab doesn't have)"
                    dropped.append(Loss(table, 0, new_values, (note,)))
            elif added or changed:
                examples = _new_conversations(connection, then) if table == "conversations" else ()
                dropped.append(Loss(table, added, changed, examples))
    return dropped, carried


def describe(losses: tuple[Loss, ...] | list[Loss]) -> list[str]:
    """Plain lines for the person: what a rollback would drop (or carry over)."""
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


@dataclass(frozen=True)
class _Column:
    name: str
    pk: int
    default: str | None


def _columns(connection: sqlite3.Connection, schema: str, table: str) -> list[_Column]:
    rows = connection.execute(f"PRAGMA {schema}.table_info({_quote(table)})").fetchall()
    return [_Column(r[1], r[5], r[4]) for r in rows]


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
