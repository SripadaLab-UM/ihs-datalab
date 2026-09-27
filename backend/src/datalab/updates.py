"""The data side of updating DataLab: the update marker, and recovering from
an update that was interrupted.

An update installs the new version alongside the current one, then switches
the launcher to it (docs/DISTRIBUTION.md). Whatever runs the update records
its progress in `<data_dir>/update-in-progress.json`:

1. `begin()`, once the running DataLab has stopped its conversations: writes
   the marker ("started"), backs up the database into `backups/<new version>/`,
   and records the backup ("backed-up").
2. `advance(..., "installed")` once the new version is installed beside the
   old one, and `advance(..., "switched")` once the launcher opens it.
3. The new version starts. Its migrations reuse the update's backup when the
   database hasn't changed since (see `backup_covering`), or take a fresh one.
   Once it has started, `finish()` removes the marker.

If the marker is still there at a start, the update was interrupted (a crash,
power loss, or the update failing part way). `recover()` works out what
happened and either finishes it, undoes it, or says what the person should do.
It never drops anything recorded in the database without the person's say-so.

A line for each finished or abandoned update goes in `logs/updates.jsonl`,
versions and times only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from datalab import datalock
from datalab.db import backups, rollback
from datalab.db.backups import Backup

MARKER = "update-in-progress.json"
STATES = ("started", "backed-up", "installed", "switched")
_HISTORY = Path("logs") / "updates.jsonl"
_MTIME_MARGIN = 2.0  # seconds

log = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


class UnreadableMarker(UpdateError):
    pass


@dataclass(frozen=True)
class Marker:
    from_version: str
    to_version: str
    state: str
    started_at: str
    updated_at: str
    # The backup folder `begin()` made, once it's complete, and its checksum
    # (so a different backup that later got the same name is never mistaken
    # for it).
    backup: str | None = None
    backup_sha256: str | None = None


@dataclass(frozen=True)
class Recovery:
    # "finishing": this is the new version's start, so the update carries on.
    # "abandoned": the update never changed the database; the marker is cleared.
    # "undone": the database was put back to the update's backup.
    # "needs-you": the person has to choose (reinstall, or `datalab rollback`).
    outcome: str
    message: str


def marker_path(data_dir: Path) -> Path:
    return data_dir / MARKER


def read_marker(data_dir: Path) -> Marker | None:
    path = marker_path(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        marker = Marker(**{k: raw[k] for k in Marker.__dataclass_fields__ if k in raw})
    except (OSError, ValueError, TypeError) as error:
        raise UnreadableMarker(str(error)) from error
    if marker.state not in STATES:
        raise UnreadableMarker(f"unknown state {marker.state!r}")
    return marker


def begin(data_dir: Path, database_file: Path, *, from_version: str, to_version: str) -> Marker:
    """Start an update: write the marker, then back up the database.

    Call it only once the running DataLab has stopped its conversations, so
    nothing is recorded after the backup. It runs holding the data folder's
    lock: the caller's, if this process has it, or its own (and then it
    refuses, with DataFolderInUse, while a DataLab is running).
    """
    with _holding_lock(data_dir):
        return _begin(data_dir, database_file, from_version, to_version)


def _begin(data_dir: Path, database_file: Path, from_version: str, to_version: str) -> Marker:
    if marker_path(data_dir).exists():
        raise UpdateError(
            "An earlier update hasn't finished. Start DataLab once so it can sort that "
            "out, then update again."
        )
    now = _now()
    marker = Marker(from_version, to_version, "started", now, now)
    _write(data_dir, marker)
    if database_file.exists():
        source = sqlite3.connect(database_file, isolation_level=None)
        try:
            backup = backups.take_backup(
                source,
                backups.backups_dir(database_file),
                app_version=to_version,
                from_version=from_version,
                reason="update",
            )
        finally:
            source.close()
        marker = replace(marker, backup=backup.name, backup_sha256=backup.sha256)
    marker = replace(marker, state="backed-up", updated_at=_now())
    _write(data_dir, marker)
    return marker


def advance(data_dir: Path, state: str) -> Marker:
    """Record that the update got further ("installed", then "switched")."""
    marker = read_marker(data_dir)
    if marker is None:
        raise UpdateError("No update is in progress.")
    if state not in STATES or STATES.index(state) <= STATES.index(marker.state):
        raise UpdateError(f"An update at {marker.state!r} can't go to {state!r}.")
    marker = replace(marker, state=state, updated_at=_now())
    _write(data_dir, marker)
    return marker


def finish(data_dir: Path, app_version: str) -> Marker | None:
    """Clear the marker once the version the update installed has started."""
    try:
        marker = read_marker(data_dir)
    except UnreadableMarker:
        return None
    if marker is None or not same_version(marker.to_version, app_version):
        return None
    _clear(data_dir, marker, "finished")
    return marker


def backup_covering(data_dir: Path, database_file: Path, app_version: str) -> Backup | None:
    """The update's backup, if it still holds the database exactly as it is.

    Then the new version's migrations don't need a second backup. The backup
    must be complete and intact, for this version, and newer than the last
    change to the database file and its write-ahead log (by a margin, since
    some file systems only keep modification times to the second or two).
    """
    try:
        marker = read_marker(data_dir)
    except UnreadableMarker:
        return None
    if marker is None or not same_version(marker.to_version, app_version):
        return None
    backup = _update_backup(marker, database_file)
    if backup is None:
        return None
    for path in (database_file, database_file.with_name(database_file.name + "-wal")):
        # An empty -wal is only a database being opened, not a change.
        if not path.exists() or path.stat().st_size == 0:
            continue
        if path.stat().st_mtime + _MTIME_MARGIN >= backup.created().timestamp():
            return None
    return backup


def from_version_for(data_dir: Path, app_version: str) -> str | None:
    """The version being updated from, if an update to `app_version` is under way."""
    with contextlib.suppress(UnreadableMarker):
        marker = read_marker(data_dir)
        if marker is not None and same_version(marker.to_version, app_version):
            return marker.from_version
    return None


def recover(
    data_dir: Path, database_file: Path, *, app_version: str, known: set[str]
) -> Recovery | None:
    """At startup: sort out an update that didn't finish. None if there wasn't one.

    Runs holding the data folder's lock, like `begin`.
    """
    with _holding_lock(data_dir):
        return _recover(data_dir, database_file, app_version, known)


def _recover(
    data_dir: Path, database_file: Path, app_version: str, known: set[str]
) -> Recovery | None:
    try:
        marker = read_marker(data_dir)
    except UnreadableMarker:
        return _unreadable(data_dir, database_file, app_version, known)
    if marker is None:
        return None
    if same_version(marker.to_version, app_version):
        # The new version is starting: its migrations run (with a backup) as
        # usual, and the marker is cleared once it's up.
        return Recovery(
            "finishing", f"Finishing the update from {marker.from_version} to {app_version}."
        )

    newer = _newer_migrations(database_file, known)
    if not newer:
        _clear(data_dir, marker, "abandoned")
        return Recovery(
            "abandoned",
            f"The update from {marker.from_version} to {marker.to_version} didn't finish. "
            f"DataLab {app_version} is still the one in use, and your data wasn't changed. "
            "You can try the update again.",
        )

    # The new version had already changed the database when the update was
    # abandoned. Put back the update's backup, but only if that drops nothing.
    # If anything goes wrong on the way, leave it to the person.
    backup = _update_backup(marker, database_file)
    if backup is not None:
        try:
            plan = rollback.plan(database_file, known, choose=backup.name)
            if not plan.loses_data:
                kept = rollback.restore(database_file, plan.backup, app_version=app_version)
                _clear(data_dir, marker, "undone")
                carried = " (the Data accessed log was carried over)" if plan.carried else ""
                return Recovery(
                    "undone",
                    f"The update to {marker.to_version} was interrupted after it had changed "
                    f"the database. DataLab put back the backup taken just before "
                    f"({plan.backup.name}); nothing else had been recorded since{carried}. "
                    f"The changed database is kept in backups/{kept.name}.",
                )
        except (rollback.RollbackRefused, backups.BackupFailed, sqlite3.Error, OSError) as error:
            log.warning("Couldn't undo the interrupted update automatically: %s", error)
    return Recovery(
        "needs-you",
        f"The update to {marker.to_version} was interrupted after it had changed the "
        f"database, and DataLab {app_version} can't use the database as it is now. "
        f"Either reinstall DataLab {marker.to_version}, or run `datalab rollback` to see "
        "what going back to the backup would drop.",
    )


def _unreadable(data_dir: Path, database_file: Path, app_version: str, known: set[str]) -> Recovery:
    if _newer_migrations(database_file, known):
        return Recovery(
            "needs-you",
            "An update was interrupted, and the database has changes this DataLab "
            f"({app_version}) doesn't know about. Reinstall the newer DataLab, or run "
            "`datalab rollback`.",
        )
    # Keep the note for the diagnostics, out of the way of the next update.
    aside = data_dir / f"update-unreadable-{datetime.now():%Y%m%d-%H%M%S}.json"
    os.replace(marker_path(data_dir), aside)
    _log(data_dir, {"outcome": "unreadable", "kept_as": aside.name})
    return Recovery(
        "abandoned",
        "An update was interrupted before it changed anything, and left a note DataLab "
        f"couldn't read (kept as {aside.name}). Your data wasn't changed.",
    )


@contextmanager
def _holding_lock(data_dir: Path) -> Iterator[None]:
    """The data folder's lock for the duration, unless this process holds it already."""
    if datalock.held(data_dir):
        yield
        return
    with datalock.hold(data_dir):
        yield


def same_version(a: str, b: str) -> bool:
    """Whether two version strings name the same release.

    Accepts the package's form ("0.1.0a2") and the tag's ("v0.1.0-alpha.2"),
    so a pre-release is never mistaken for the release it leads up to.
    """
    return _normal(a) == _normal(b)


def _normal(version: str) -> str:
    text = version.strip().lower().removeprefix("v")
    text = re.sub(r"[-_.]?(alpha|a)[-_.]?(\d+)", r"a\2", text)
    text = re.sub(r"[-_.]?(beta|b)[-_.]?(\d+)", r"b\2", text)
    text = re.sub(r"[-_.]?(rc|c|pre|preview)[-_.]?(\d+)", r"rc\2", text)
    return re.sub(r"[-_.]?dev[-_.]?(\d+)", r".dev\1", text)


def _update_backup(marker: Marker, database_file: Path) -> Backup | None:
    """The update's backup, if it's still there, whole, and the one it made."""
    if marker.backup is None:
        return None
    backup = backups.read_backup(backups.backups_dir(database_file) / marker.backup)
    if backup is None or not backup.verify():
        return None
    if marker.backup_sha256 is not None and backup.sha256 != marker.backup_sha256:
        return None
    return backup


def _newer_migrations(database_file: Path, known: set[str]) -> list[str]:
    if not database_file.exists():
        return []
    connection = sqlite3.connect(database_file)
    try:
        return [m for m in backups.applied_migrations(connection) if m not in known]
    finally:
        connection.close()


def _clear(data_dir: Path, marker: Marker, outcome: str) -> None:
    _log(data_dir, {**asdict(marker), "outcome": outcome})
    marker_path(data_dir).unlink(missing_ok=True)


def _log(data_dir: Path, entry: dict) -> None:
    path = data_dir / _HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as file:
        file.write(json.dumps({"at": _now(), **entry}) + "\n")


def _write(data_dir: Path, marker: Marker) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = marker_path(data_dir)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    temporary.write_text(json.dumps(asdict(marker), indent=2) + "\n")
    with temporary.open("rb+") as file:
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
