"""Backups of DataLab's database, taken before anything changes its layout.

Each backup is a folder in `<data_dir>/backups/`:

    backups/0.2.0-20260927-070102-3fa1c2/
        datalab.sqlite     the database as it was, one self-contained file
        manifest.json      which version took it, its schema, when, and a checksum

The folder is named for the DataLab version about to change the database and
when (plus a random part), so `backups/0.2.0-…/` is "the database just
before 0.2.0 touched it". Names are never reused, even after older backups
are removed.

The copy is made with SQLite's online backup API, never by copying the file:
while DataLab runs, recent changes sit in the write-ahead log (`-wal`) next to
the database, and a plain file copy would miss them or catch them half-written.
A backup is built in a hidden `.incoming-…` folder and renamed into place only
once it is complete, so a folder with a manifest is always a whole backup.

Only DataLab's database is backed up. Conversation workspaces, runs, and repos
are the user's files and are never rolled back (see docs/DISTRIBUTION.md).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

FOLDER = "backups"
FILE = "datalab.sqlite"
MANIFEST = "manifest.json"
INCOMING = ".incoming-"
# How many backups to keep. The oldest beyond this are removed after each new
# one. (A `[backups]` section in settings.toml can set this later.) Backups
# taken before a rollback replaced the database are never removed
# automatically: they may hold the only copy of what the rollback dropped.
KEEP = 3
KEPT_REASONS = frozenset({"restore"})
# Room to leave on the disk after a backup, beyond the backup itself.
_SPARE_BYTES = 200 * 1024**2
# Antivirus and search indexers on Windows briefly hold new files open.
_RENAME_ATTEMPTS = 5

log = logging.getLogger(__name__)


class BackupFailed(RuntimeError):
    """The database couldn't be backed up, so nothing that needed a backup ran."""


@dataclass(frozen=True)
class Backup:
    folder: Path
    # The DataLab version that took the backup, about to change the database.
    app_version: str
    # The version that used the database before, when known (an update knows).
    from_version: str | None
    # The migrations applied in the backed-up database, in order.
    migrations: tuple[str, ...]
    created_at: str
    sha256: str
    size_bytes: int
    # "migrate" (before a new version's migrations), "update" (before an
    # update switches versions), "restore" (before a rollback replaced the
    # database), or "manual".
    reason: str

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def file(self) -> Path:
        return self.folder / FILE

    @property
    def schema_version(self) -> str | None:
        """The last migration the backed-up database had, e.g. `0005_rigor.sql`."""
        return self.migrations[-1] if self.migrations else None

    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)

    def verify(self) -> bool:
        """Whether the backup file is still exactly what was written."""
        return self.file.is_file() and _sha256(self.file) == self.sha256


def backups_dir(database_file: Path) -> Path:
    return database_file.parent / FOLDER


def applied_migrations(connection: sqlite3.Connection, schema: str = "main") -> list[str]:
    """The migrations a database has had, in order (none for a new database)."""
    found = connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if not found:
        return []
    return sorted(r[0] for r in connection.execute(f"SELECT name FROM {schema}.schema_migrations"))


def take_backup(
    source: sqlite3.Connection,
    folder: Path,
    *,
    app_version: str,
    reason: str,
    from_version: str | None = None,
    keep: int = KEEP,
    protect: tuple[str, ...] = (),
) -> Backup:
    """Back up the database open in `source` into a new folder under `folder`.

    Then remove the oldest backups beyond `keep`, except any named in `protect`.
    Raises BackupFailed if the backup can't be made; the database itself is
    never changed.
    """
    folder.mkdir(parents=True, exist_ok=True)
    _remove_incomplete(folder)
    _check_space(source, folder)
    incoming = folder / f"{INCOMING}{secrets.token_hex(4)}"
    incoming.mkdir()
    try:
        target_path = incoming / FILE
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
            # One self-contained file: the copy needs no -wal beside it, and
            # can be opened read-only.
            target.execute("PRAGMA journal_mode = DELETE")
            status = target.execute("PRAGMA quick_check").fetchone()[0]
            migrations = applied_migrations(target)
        finally:
            target.close()
        if status != "ok":
            raise BackupFailed(f"the backup copy failed SQLite's check: {status}")
        _fsync(target_path)
        created = datetime.now().astimezone()
        backup = Backup(
            folder=folder / _free_name(folder, app_version, created),
            app_version=app_version,
            from_version=from_version,
            migrations=tuple(migrations),
            created_at=created.isoformat(timespec="microseconds"),
            sha256=_sha256(target_path),
            size_bytes=target_path.stat().st_size,
            reason=reason,
        )
        _write_json(incoming / MANIFEST, _manifest(backup))
        _rename(incoming, backup.folder)
    except BaseException as error:
        shutil.rmtree(incoming, ignore_errors=True)
        if isinstance(error, (sqlite3.Error, OSError)):
            raise BackupFailed(str(error)) from error
        raise
    prune(folder, keep, protect=(*protect, backup.name))
    return backup


def list_backups(folder: Path) -> list[Backup]:
    """The complete backups in `folder`, oldest first."""
    found = []
    if not folder.is_dir():
        return found
    for entry in folder.iterdir():
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        backup = read_backup(entry)
        if backup is not None:
            found.append(backup)
    return sorted(found, key=lambda b: (b.created(), b.name))


def read_backup(entry: Path) -> Backup | None:
    """The backup in a folder, or None if it has no readable manifest."""
    try:
        raw = json.loads((entry / MANIFEST).read_text())
        return Backup(
            folder=entry,
            app_version=str(raw["app_version"]),
            from_version=raw.get("from_version"),
            migrations=tuple(raw["migrations"]),
            created_at=str(raw["created_at"]),
            sha256=str(raw["sha256"]),
            size_bytes=int(raw["size_bytes"]),
            reason=str(raw.get("reason", "")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prune(folder: Path, keep: int, *, protect: tuple[str, ...] = ()) -> list[Path]:
    """Remove the oldest backups beyond `keep`; return the folders removed.

    Only folders with a DataLab backup manifest are ever removed, never one
    named in `protect`, and never one taken before a rollback (those don't
    count towards `keep`). A folder that can't be removed right now (a file
    held open on Windows, say) is left for next time: the new backup is fine.
    """
    backups = [b for b in list_backups(folder) if b.reason not in KEPT_REASONS]
    removed = []
    for backup in backups[: max(len(backups) - max(keep, 1), 0)]:
        if backup.name in protect:
            continue
        try:
            shutil.rmtree(backup.folder)
        except OSError as error:
            log.warning("Couldn't remove the old backup %s yet: %s", backup.name, error)
            continue
        removed.append(backup.folder)
    return removed


def _manifest(backup: Backup) -> dict:
    raw = asdict(backup)
    raw.pop("folder")
    raw["migrations"] = list(backup.migrations)
    raw["schema_version"] = backup.schema_version
    raw["file"] = FILE
    return raw


def _free_name(folder: Path, version: str, created: datetime) -> str:
    """A name no backup has had: the version, the time, and a random part.

    The random part matters: a counter would give a new backup the name of
    one just removed, and a marker still naming that one would then point
    at a different backup.
    """
    safe = re.sub(r"[^A-Za-z0-9._+-]", "-", version).strip(".") or "unknown"
    while True:
        name = f"{safe}-{created:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        if not (folder / name).exists():
            return name


def _rename(source: Path, target: Path) -> None:
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _remove_incomplete(folder: Path) -> None:
    """Remove half-made backups left by a crash. They are only ever copies."""
    for entry in folder.glob(f"{INCOMING}*"):
        shutil.rmtree(entry, ignore_errors=True)


def _check_space(source: sqlite3.Connection, folder: Path) -> None:
    pages = source.execute("PRAGMA page_count").fetchone()[0]
    size = source.execute("PRAGMA page_size").fetchone()[0]
    needed = pages * size + _SPARE_BYTES
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise BackupFailed(
            f"not enough free disk space to back up the database "
            f"({needed // 1024**2} MB needed, {free // 1024**2} MB free)"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    _fsync(temporary)
    os.replace(temporary, path)


def _fsync(path: Path) -> None:
    with contextlib.suppress(OSError), path.open("rb+") as file:
        os.fsync(file.fileno())
