"""The chosen folder's file list, kept in DataLab's database: metadata only
(path, size, modified date, content hash), never contents.

The first refresh lists the whole folder; later ones ask Dropbox only what
changed since (its cursor), deletions included. Practice DataLab's local
folder has no cursor and is listed again each time.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from datalab.dropbox.sources import CursorTooOld, FileEntry, Source, path_key


@dataclass(frozen=True)
class Chosen:
    folder_id: str
    folder_path: str
    root_ns: str | None
    cursor: str | None
    indexed_at: str | None
    chosen_at: str


class ProcessedIndex:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()

    # The chosen folder ------------------------------------------------------

    def chosen(self) -> Chosen | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM processed_source").fetchone()
        if row is None:
            return None
        return Chosen(
            folder_id=row["folder_id"],
            folder_path=row["folder_path"],
            root_ns=row["root_ns"],
            cursor=row["cursor"],
            indexed_at=row["indexed_at"],
            chosen_at=row["chosen_at"],
        )

    def choose(self, folder_id: str, folder_path: str, root_ns: str | None) -> None:
        """A new folder: its file list starts empty until the next refresh."""
        with self._lock, self._transaction():
            self._db.execute("DELETE FROM processed_source")
            self._db.execute("DELETE FROM processed_files")
            self._db.execute(
                "INSERT INTO processed_source (one, folder_id, folder_path, root_ns, chosen_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (folder_id, folder_path, root_ns, _now()),
            )

    def forget(self) -> None:
        with self._lock, self._transaction():
            self._db.execute("DELETE FROM processed_source")
            self._db.execute("DELETE FROM processed_files")

    # Refreshing -------------------------------------------------------------

    def refresh(self, source: Source) -> int:
        """Bring the list up to date from `source`; how many files it now has.
        The source is asked outside the lock (it may take a while)."""
        chosen = self.chosen()
        if chosen is None:
            raise LookupError("No folder is chosen.")
        changes = None
        if chosen.cursor:
            try:
                changes = source.changes(chosen.cursor)
            except CursorTooOld:
                changes = None
        whole = changes is None
        if changes is None:
            changes = source.listing()
        folder_path = getattr(source, "folder_path", chosen.folder_path)
        with self._lock, self._transaction():
            current = self._db.execute("SELECT folder_id FROM processed_source").fetchone()
            if current is None or current["folder_id"] != chosen.folder_id:
                return 0  # another folder was chosen meanwhile
            if whole:
                self._db.execute("DELETE FROM processed_files")
            for key in changes.deleted:
                escaped = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                self._db.execute(
                    "DELETE FROM processed_files WHERE path_key = ? OR path_key LIKE ? ESCAPE '\\'",
                    (key, escaped + "/%"),
                )
            self._db.executemany(
                "INSERT OR REPLACE INTO processed_files "
                "(path_key, path, file_id, size, modified, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (e.key, e.path, e.id, e.size, e.modified, e.content_hash)
                    for e in changes.changed
                ],
            )
            self._db.execute(
                "UPDATE processed_source SET cursor = ?, indexed_at = ?, folder_path = ?",
                (changes.cursor, _now(), folder_path),
            )
            return self._db.execute("SELECT COUNT(*) FROM processed_files").fetchone()[0]

    # Reading ----------------------------------------------------------------

    def get(self, path: str) -> FileEntry | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM processed_files WHERE path_key = ?", (path_key(path),)
            ).fetchone()
        return _entry(row) if row else None

    def files(self) -> Iterator[FileEntry]:
        """Every file, in path order."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM processed_files ORDER BY path_key").fetchall()
        for row in rows:
            yield _entry(row)

    def totals(self) -> tuple[int, int]:
        """How many files, and their total size in bytes."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM processed_files"
            ).fetchone()
        return int(row["n"]), int(row["bytes"])

    def _transaction(self):
        return _Transaction(self._db)


class _Transaction:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        # Like exports.py: the connection is shared, so callers hold their lock.
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind, *_: object) -> None:
        self._db.execute("COMMIT" if kind is None else "ROLLBACK")


def _entry(row: sqlite3.Row) -> FileEntry:
    return FileEntry(
        path=row["path"],
        id=row["file_id"],
        size=row["size"],
        modified=row["modified"],
        content_hash=row["content_hash"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
