"""Settings → Storage: what uses disk in DataLab's data folder, and the few
things a person may remove from there.

Nothing is ever deleted automatically (docs/SAFETY.md, "Your work isn't
lost"). This module only lists, and removes exactly one item a person chose
and confirmed. What can be removed:

- **a Playground result** (`playground/<pg_id>/results/<query id>.csv`, and
  its column types beside it), unless its query is still running. The query
  stays in the Playground's history, without its result;
- **a workflow run's files** (`runs/<run id>/`), once the run has finished
  and no Replay of it is going. The run's record stays: in the database, and
  as `record.json` in the folder. The run can't be replayed afterwards;
- **a database backup** (`backups/<name>/`), except the one an update in
  progress relies on. A backup taken before a rollback may hold the only
  copy of what the rollback dropped, so it's removed only with an explicit
  confirmation.

Never removed here: the database, the audit log (`logs/audit.jsonl`),
conversations (the Workspace deletes those), exports, repos, or anything
that isn't exactly one of the items above. Items are named by kind and id,
never by path, and each id must match its kind's pattern; the path is then
built here and checked to be a real folder or file (not a link) directly in
its expected place.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from datalab import updates
from datalab.config import Settings
from datalab.db import backups as backups_module
from datalab.exports import MANIFEST as EXPORT_MANIFEST
from datalab.exports import DestinationStore
from datalab.workflows.records import WRITE_LOCK

RemovableKind = Literal["playground-result", "run-files", "backup"]

_QUERY_ID = re.compile(r"q_\d{8}T\d{6}_[0-9a-f]{6}")
_PLAYGROUND_ID = re.compile(r"pg_[0-9a-f]{12}")
_RUN_ID = re.compile(r"run_\d{8}T\d{6}_[0-9a-f]{6}")
_BACKUP_NAME = re.compile(r"[A-Za-z0-9+_-][A-Za-z0-9._+-]{0,199}")
# Runs whose files are in use: going, or about to go.
_ACTIVE_RUN = ("queued", "running")
# What stays in a run's folder when its files are removed.
_RUN_RECORD = "record.json"
_HELD_OPEN = (
    "couldn't be removed: another program may have a file open (antivirus, or a file "
    "open elsewhere). Close it in other programs and try again."
)

# Top-level entries each group accounts for; the rest are listed as "Other".
_KNOWN = {
    "datalab.sqlite",
    "datalab.sqlite-wal",
    "datalab.sqlite-shm",
    "sessions",
    "playground",
    "runs",
    "backups",
    "logs",
    "practice-exports",
}
_OTHER_LABELS = {
    "repos": "The lab's repos (knowledge base and pipelines)",
    "catalog": "The database catalog: tables and columns, metadata only",
    "workflow-cache": "Built R packages for workflows",
    "workflows-local": "Workflow files on this computer",
    "settings.toml": "Settings",
    "update-preferences.json": "Whether to check for updates every hour",
    ".lock": "The data folder's lock",
}


_YOURS = "Exported files are yours; DataLab never deletes them."


class StorageRefused(RuntimeError):
    """An item can't be removed (the message says why). Nothing was changed."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Item:
    kind: str
    id: str
    label: str
    detail: str
    size_bytes: int
    modified_at: str | None = None
    # Whether the person can remove it here, and if not, why not.
    removable: bool = False
    not_removable_because: str | None = None
    # Backups: kept for good (a rollback's), and why.
    kept: bool = False
    kept_because: str | None = None
    # What removing it loses, for the confirmation.
    removing_loses: str | None = None
    # The removal needs an explicit "I understand" (a rollback's backup).
    needs_confirmation: bool = False


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    about: str
    size_bytes: int
    items: list[Item] = field(default_factory=list)


@dataclass(frozen=True)
class Usage:
    data_dir: Path
    size_bytes: int
    free_bytes: int | None
    groups: list[Group]


@dataclass(frozen=True)
class Removed:
    kind: str
    id: str
    freed_bytes: int


class Storage:
    def __init__(
        self,
        settings: Settings,
        database: sqlite3.Connection,
        *,
        turn_running: Callable[[str], bool] = lambda _: False,
    ) -> None:
        self._settings = settings
        self._db = database
        self._turn_running = turn_running
        self._root = settings.data_dir

    # ------------------------------------------------------------ listing

    def usage(self) -> Usage:
        groups = [
            self._database(),
            self._conversations(),
            self._playground(),
            self._runs(),
            self._backups(),
            self._exports(),
            self._logs(),
            self._other(),
        ]
        free = None
        with contextlib.suppress(OSError):
            free = shutil.disk_usage(self._root).free
        return Usage(self._root, size_of(self._root), free, [g for g in groups if g is not None])

    def _database(self) -> Group:
        names = ("datalab.sqlite", "datalab.sqlite-wal", "datalab.sqlite-shm")
        files = [self._root / n for n in names]
        size = sum(size_of(f) for f in files)
        return Group(
            "database",
            "DataLab's database",
            "Conversations, queries, workflow runs and settings, as DataLab records them. "
            "It's never removed here.",
            size,
            [
                Item(
                    "database",
                    "datalab.sqlite",
                    "datalab.sqlite",
                    "With its write-ahead log",
                    size,
                    not_removable_because="DataLab's own record. It's never deleted.",
                )
            ],
        )

    def _conversations(self) -> Group:
        folder = self._root / "sessions"
        rows = {
            r["id"]: r
            for r in self._db.execute("SELECT id, kind, title, updated_at FROM conversations")
        }
        items = []
        for entry in _folders(folder):
            row = rows.get(entry.name)
            parts = {
                "workspace": size_of(entry / "work"),
                "checkpoints": size_of(entry / "checkpoints"),
                "query results": size_of(entry / "oracle"),
            }
            total = size_of(entry)
            parts["other"] = max(total - sum(parts.values()), 0)
            detail = " · ".join(f"{name} {human(size)}" for name, size in parts.items() if size)
            if row is None:
                label, why = (
                    "A conversation DataLab no longer lists",
                    (
                        "DataLab no longer lists this conversation (after a rollback, say), "
                        "so it isn't removed from here."
                    ),
                )
            else:
                kind = "Data" if row["kind"] == "data" else "Research"
                label = f"{row['title']} ({kind.lower()} session)"
                why = (
                    "The agent is working in it."
                    if self._turn_running(entry.name)
                    else "Delete a conversation from the Workspace, where you can see what "
                    "it holds."
                )
            items.append(
                Item(
                    "conversation",
                    entry.name,
                    label,
                    detail or "Empty",
                    total,
                    modified_at=row["updated_at"] if row else _mtime(entry),
                    not_removable_because=why,
                )
            )
        items.sort(key=lambda i: i.size_bytes, reverse=True)
        return Group(
            "conversations",
            "Conversations",
            "Each conversation's workspace, its checkpoints (one per turn), and its query results.",
            sum(i.size_bytes for i in items),
            items,
        )

    def _playground(self) -> Group:
        running = {
            r[0]
            for r in self._db.execute(
                "SELECT id FROM queries WHERE status = 'running' AND origin = 'playground'"
            )
        }
        facts = {
            r["id"]: r
            for r in self._db.execute(
                "SELECT id, row_count, finished_at FROM queries WHERE origin = 'playground'"
            )
        }
        items = []
        for results in sorted((self._root / "playground").glob("pg_*/results")):
            if not _PLAYGROUND_ID.fullmatch(results.parent.name) or not _plain_dir(results):
                continue
            for file in sorted(results.glob("q_*.csv")):
                query_id = file.stem
                if not _QUERY_ID.fullmatch(query_id) or not _plain_file(file):
                    continue
                row = facts.get(query_id)
                size = size_of(file) + size_of(_columns_file(file))
                rows = f"{row['row_count']:,} rows" if row and row["row_count"] is not None else ""
                busy = query_id in running
                items.append(
                    Item(
                        "playground-result",
                        query_id,
                        f"Result of {query_id}",
                        rows or "Result file",
                        size,
                        modified_at=(row["finished_at"] if row else None) or _mtime(file),
                        removable=not busy,
                        not_removable_because="Its query is still running." if busy else None,
                        removing_loses=(
                            "The result's file. The query stays in the Playground's history "
                            "(its SQL, when it ran, how many rows), and you can run it again."
                        ),
                    )
                )
        items.sort(key=lambda i: i.modified_at or "", reverse=True)
        return Group(
            "playground",
            "SQL Playground results",
            "One file per query you ran in the Playground, up to the extraction cap each. "
            "They're never cleaned up automatically.",
            size_of(self._root / "playground"),
            items,
        )

    def _runs(self) -> Group:
        rows = self._db.execute(
            "SELECT id, workflow_name, status, started_at, finished_at, delivery_status, "
            "run_dir, inputs_kept, of_run FROM workflow_runs ORDER BY started_at DESC"
        ).fetchall()
        replaying = {r["of_run"] for r in rows if r["of_run"] and r["status"] in _ACTIVE_RUN}
        items = []
        for row in rows:
            folder = self._root / row["run_dir"]
            if not _plain_dir(folder):
                continue
            size = size_of(folder)
            why = _why_run_busy(row, row["id"] in replaying)
            state = ""
            if not row["inputs_kept"]:
                if _run_leftovers(folder):
                    state = " · some files couldn't be removed yet"
                else:
                    state = " · files removed, record kept"
                    why = why or "Its files were already removed; only its record is left."
            items.append(
                Item(
                    "run-files",
                    row["id"],
                    f"{row['workflow_name']}",
                    f"{row['id']} · {row['status']}{state}",
                    size,
                    modified_at=row["started_at"],
                    removable=why is None,
                    not_removable_because=why,
                    removing_loses=(
                        "The run's extracts, outputs and logs in DataLab's data folder. Its "
                        "record stays (what ran, with which settings, and the checksum of every "
                        "output), and anything it delivered stays where it was delivered. The "
                        "run can't be replayed afterwards."
                    ),
                )
            )
        return Group(
            "runs",
            "Workflow runs",
            "Each run's folder: its extracted inputs, every step's outputs, and its record.",
            size_of(self._root / "runs"),
            items,
        )

    def _backups(self) -> Group:
        folder = backups_module.backups_dir(self._settings.database_file)
        found = backups_module.list_backups(folder)
        protected, why_protected = self._update_backups()
        rotating = [b for b in found if b.reason not in backups_module.KEPT_REASONS]
        newest = {b.name for b in rotating[-backups_module.KEEP :]}
        items = []
        for backup in reversed(found):
            kept = backup.reason in backups_module.KEPT_REASONS
            if kept:
                kept_because = (
                    "Taken before a rollback replaced the database. It may hold the only copy "
                    "of what the rollback dropped, so it's never removed automatically."
                )
            elif backup.name in newest:
                kept_because = (
                    f"One of the newest {backups_module.KEEP}. Older ones are removed "
                    "automatically after each new backup."
                )
            else:
                kept_because = None
            why = _protected_because(backup.name, protected, why_protected)
            items.append(
                Item(
                    "backup",
                    backup.name,
                    backup.name,
                    f"{_reason(backup.reason)} · DataLab {backup.app_version} · "
                    f"schema {backup.schema_version or 'empty'}",
                    size_of(backup.folder),
                    modified_at=backup.created_at,
                    removable=why is None,
                    not_removable_because=why,
                    kept=kept,
                    kept_because=kept_because,
                    removing_loses=(
                        "This backup of DataLab's database. It's the database as it was just "
                        "before a rollback replaced it: conversations, queries and runs "
                        "recorded after the update it undid may exist only here. Once it's "
                        "removed, that rollback can't be undone."
                        if kept
                        else "This backup of DataLab's database. `datalab rollback` can no "
                        "longer go back to it."
                    ),
                    needs_confirmation=kept,
                )
            )
        return Group(
            "backups",
            "Database backups",
            "Copies of DataLab's database, taken before an update changes it. Only the "
            "database: your files are never rolled back.",
            size_of(folder),
            items,
        )

    def _update_backups(self) -> tuple[set[str], str | None]:
        """Backups an update in progress relies on, and why they can't go."""
        try:
            marker = updates.read_marker(self._root)
        except updates.UnreadableMarker:
            return set(), (
                "An update left a note DataLab can't read. Start DataLab again before "
                "removing backups."
            )
        if marker is None or marker.backup is None:
            return set(), None
        return {marker.backup}, "An update in progress relies on this backup."

    def _exports(self) -> Group | None:
        if self._settings.profile == "practice":
            folder = self._root / "practice-exports"
            return Group(
                "exports",
                "Practice exports",
                "Practice DataLab exports only to this folder. Exported files are yours: "
                "DataLab never deletes them.",
                size_of(folder),
                [
                    Item(
                        "exports",
                        e.name,
                        e.name,
                        "Export",
                        size_of(e),
                        modified_at=_mtime(e),
                        not_removable_because=_YOURS,
                    )
                    for e in sorted(_folders(folder), key=lambda e: e.name, reverse=True)
                ],
            )
        items = []
        for destination in DestinationStore(self._db).list():
            path = Path(destination.path)
            made = [e for e in _folders(path) if (e / EXPORT_MANIFEST).is_file()]
            size = sum(size_of(e) for e in made)
            items.append(
                Item(
                    "exports",
                    destination.id,
                    destination.name,
                    f"{len(made)} export{'s' if len(made) != 1 else ''} by DataLab"
                    if destination.available
                    else "Folder not found",
                    size,
                    not_removable_because=_YOURS,
                )
            )
        return Group(
            "exports",
            "Exports",
            "Outside the data folder, in the export folders you chose. Only the folders "
            "DataLab made are counted. DataLab never deletes them.",
            sum(i.size_bytes for i in items),
            items,
        )

    def _logs(self) -> Group:
        folder = self._root / "logs"
        items = []
        for file in sorted(folder.glob("*")) if folder.is_dir() else []:
            if not _plain_file(file):
                continue
            audit = file.name == "audit.jsonl"
            items.append(
                Item(
                    "log",
                    file.name,
                    file.name,
                    "The audit log: every query and export, metadata only"
                    if audit
                    else "Metadata only",
                    size_of(file),
                    modified_at=_mtime(file),
                    not_removable_because=(
                        "The record of everything DataLab queried and exported. It's never deleted."
                        if audit
                        else "Kept for diagnostics."
                    ),
                )
            )
        return Group(
            "logs",
            "Logs",
            "What DataLab did, never data: queries' metadata, updates, the last Safety check.",
            size_of(folder),
            items,
        )

    def _other(self) -> Group:
        items = []
        if self._root.is_dir():
            for entry in sorted(self._root.iterdir(), key=lambda e: e.name):
                if entry.name in _KNOWN:
                    continue
                items.append(
                    Item(
                        "other",
                        entry.name,
                        entry.name,
                        _OTHER_LABELS.get(entry.name, ""),
                        size_of(entry),
                        not_removable_because="DataLab manages this itself.",
                    )
                )
        return Group(
            "other",
            "Everything else",
            "The lab's repos, workflow files, built packages and settings.",
            sum(i.size_bytes for i in items),
            items,
        )

    # ------------------------------------------------------------ removing

    def remove(self, kind: str, item_id: str, *, confirmed: bool = False) -> Removed:
        """Remove one item the person chose. Raises StorageRefused, changing nothing."""
        if kind == "playground-result":
            return self._remove_playground_result(item_id)
        if kind == "run-files":
            return self._remove_run_files(item_id)
        if kind == "backup":
            return self._remove_backup(item_id, confirmed=confirmed)
        raise StorageRefused("That can't be removed from here.", status=422)

    def _remove_playground_result(self, query_id: str) -> Removed:
        if not _QUERY_ID.fullmatch(query_id):
            raise StorageRefused("No such Playground result.", status=404)
        found = [
            f
            for f in (self._root / "playground").glob(f"pg_*/results/{query_id}.csv")
            if _PLAYGROUND_ID.fullmatch(f.parent.parent.name)
            and _plain_dir(f.parent)
            and _plain_file(f)
            and _inside(f, f.parent)
            and _inside(f.parent.parent, self._root / "playground")
        ]
        if len(found) != 1:
            raise StorageRefused("No such Playground result.", status=404)
        row = self._db.execute("SELECT status FROM queries WHERE id = ?", (query_id,)).fetchone()
        if row is not None and row["status"] == "running":
            raise StorageRefused("Its query is still running. Stop it or let it finish first.")
        file = found[0]
        freed = size_of(file) + size_of(_columns_file(file))
        try:
            file.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise StorageRefused(f"The result {_HELD_OPEN}") from None
        with contextlib.suppress(OSError):
            columns = _columns_file(file)
            if _plain_file(columns):
                columns.unlink()
        return Removed("playground-result", query_id, freed)

    def _remove_run_files(self, run_id: str) -> Removed:
        if not _RUN_ID.fullmatch(run_id):
            raise StorageRefused("No such run.", status=404)
        row = self._db.execute(
            "SELECT status, run_dir, inputs_kept FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        folder = self._root / "runs" / run_id
        if row is None or row["run_dir"] != f"runs/{run_id}" or not _plain_dir(folder):
            raise StorageRefused("No such run.", status=404)
        if not _inside(folder, self._root / "runs"):
            raise StorageRefused("No such run.", status=404)
        if not row["inputs_kept"] and not _run_leftovers(folder):
            raise StorageRefused("This run's files were already removed.")
        # Claimed in one statement, holding the run tables' lock: only a run
        # that has finished (delivery too) and that no Replay is using. A
        # Replay's own record goes in only while `inputs_kept` is 1
        # (RunStore.create_run), so the two can't both go ahead.
        placeholders = ", ".join("?" for _ in _ACTIVE_RUN)
        with WRITE_LOCK:
            claimed = self._db.execute(
                "UPDATE workflow_runs SET inputs_kept = 0 WHERE id = ? "
                f"AND status NOT IN ({placeholders}) AND finished_at IS NOT NULL "
                "AND delivery_status != 'pending' AND NOT EXISTS (SELECT 1 FROM workflow_runs "
                f"AS other WHERE other.of_run = ? AND other.status IN ({placeholders}))",
                (run_id, *_ACTIVE_RUN, run_id, *_ACTIVE_RUN),
            ).rowcount
        if claimed != 1:
            current = self._db.execute(
                "SELECT status, finished_at, delivery_status FROM workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            why = _why_run_busy(current, self._replaying(run_id)) if current else None
            raise StorageRefused(why or "The run is in use.")
        freed = 0
        stuck = False
        for entry in _run_leftovers(folder):
            size = size_of(entry)
            try:
                _remove_entry(entry)
            except OSError:
                stuck = True
                size -= size_of(entry)
            freed += size
        _note_files_removed(folder / _RUN_RECORD, partial=stuck)
        if stuck:
            raise StorageRefused(f"Some of the run's files {_HELD_OPEN}")
        return Removed("run-files", run_id, freed)

    def _replaying(self, run_id: str) -> bool:
        placeholders = ", ".join("?" for _ in _ACTIVE_RUN)
        return (
            self._db.execute(
                f"SELECT 1 FROM workflow_runs WHERE of_run = ? AND status IN ({placeholders})",
                (run_id, *_ACTIVE_RUN),
            ).fetchone()
            is not None
        )

    def _remove_backup(self, name: str, *, confirmed: bool) -> Removed:
        folder = backups_module.backups_dir(self._settings.database_file)
        if not _BACKUP_NAME.fullmatch(name) or name in (".", ".."):
            raise StorageRefused("No such backup.", status=404)
        entry = folder / name
        if not _plain_dir(entry) or not _inside(entry, folder):
            raise StorageRefused("No such backup.", status=404)
        backup = backups_module.read_backup(entry)
        if backup is None:
            # Only folders with a DataLab manifest are ever removed (as in prune).
            raise StorageRefused("That folder isn't a DataLab backup, so it's left alone.")
        why = _protected_because(name, *self._update_backups())
        if why is not None:
            raise StorageRefused(why)
        if backup.reason in backups_module.KEPT_REASONS and not confirmed:
            raise StorageRefused(
                "This backup was taken before a rollback replaced the database, and may hold "
                "the only copy of what the rollback dropped. Confirm to remove it."
            )
        freed = size_of(entry)
        # Renamed aside first, so a half-removed folder is never taken for a backup.
        for earlier in folder.glob(f"{backups_module.INCOMING}removing-*"):
            shutil.rmtree(earlier, ignore_errors=True)  # a removal a held file stopped
        aside = folder / f"{backups_module.INCOMING}removing-{secrets.token_hex(4)}"
        try:
            entry.rename(aside)
        except OSError:
            raise StorageRefused(f"The backup {_HELD_OPEN}") from None
        # Whatever a held file keeps here now is no backup, and goes next time.
        shutil.rmtree(aside, ignore_errors=True)
        return Removed("backup", name, freed)


# ---------------------------------------------------------------- helpers


def size_of(path: Path) -> int:
    """Bytes used by a file, or a folder and everything in it. Links aren't followed."""
    try:
        info = path.lstat()
    except OSError:
        return 0
    if not _is_dir(info):
        return info.st_size
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"


def _is_dir(info: os.stat_result) -> bool:
    import stat

    return stat.S_ISDIR(info.st_mode)


def _plain_dir(path: Path) -> bool:
    """A real folder, not a link to one."""
    return path.is_dir() and not path.is_symlink()


def _plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _inside(path: Path, folder: Path) -> bool:
    """Whether `path`, links resolved, is directly in `folder` (links resolved too)."""
    try:
        return path.resolve(strict=True).parent == folder.resolve(strict=True)
    except OSError:
        return False


def _folders(folder: Path) -> list[Path]:
    if not _plain_dir(folder):
        return []
    try:
        return [e for e in folder.iterdir() if _plain_dir(e) and not e.name.startswith(".")]
    except OSError:
        return []


def _columns_file(result: Path) -> Path:
    return result.with_suffix(".columns.json")


def _mtime(path: Path) -> str | None:
    try:
        stamp = path.lstat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def _why_run_busy(row: sqlite3.Row, replaying: bool) -> str | None:
    """Why a run's files are in use: it's going, still delivering, or being replayed.

    A run is `succeeded` before its delivery, and only gets `finished_at`
    once delivery is done, so both are checked."""
    if row["status"] in _ACTIVE_RUN or row["finished_at"] is None:
        return "The run is still going."
    if row["delivery_status"] == "pending":
        return "The run is still delivering its results."
    if replaying:
        return "A Replay of this run is using its files."
    return None


def _protected_because(name: str, protected: set[str], why: str | None) -> str | None:
    """Why an update keeps a backup: it names this one, or its note can't be read (all)."""
    if why is None or (protected and name not in protected):
        return None
    return why


def _reason(reason: str) -> str:
    return {
        "migrate": "Before an upgrade",
        "update": "Before an update",
        "restore": "Before a rollback",
        "manual": "Taken by hand",
    }.get(reason, reason or "Backup")


def _remove_entry(entry: Path) -> None:
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink(missing_ok=True)
    else:
        # rmtree never follows links inside: it removes the link itself.
        shutil.rmtree(entry)


def _run_leftovers(folder: Path) -> list[Path]:
    """What's in a run's folder besides its record."""
    try:
        return [e for e in folder.iterdir() if not (e.name == _RUN_RECORD and _plain_file(e))]
    except OSError:
        return []


def _note_files_removed(record: Path, *, partial: bool) -> None:
    """Say in the run folder's own record that its files were removed (or some were)."""
    if not _plain_file(record):
        return
    with contextlib.suppress(OSError, ValueError):
        detail = json.loads(record.read_text(encoding="utf-8"))
        if isinstance(detail, dict):
            detail["inputs_kept"] = False
            detail["files_removed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            detail["files_removal_partial"] = partial
            temporary = record.with_name(record.name + ".partial")
            temporary.write_text(
                json.dumps(detail, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
            )
            temporary.replace(record)
