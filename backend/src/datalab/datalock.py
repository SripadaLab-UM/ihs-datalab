"""One DataLab per data folder.

A DataLab cleans up its data folder's containers when it starts and ends the
turns the last run left going. A second one on the same folder would do that
to the first, which is still working. The port check alone can't prevent it:
two launches at once both find the port free. So each DataLab takes an
exclusive lock on `<data folder>/.lock` before doing anything else, and holds
it until it exits. The operating system releases it however the process
ends, even in a crash, so there's never a stale lock to clear by hand.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

LOCK_NAME = ".lock"


class DataFolderInUse(RuntimeError):
    """Another DataLab holds this data folder."""


class DataFolderLock:
    """Held while the DataLab using a data folder runs. See `hold`."""

    def __init__(self, path: Path, handle: IO[bytes]) -> None:
        self.path = path
        self._handle: IO[bytes] | None = handle

    def release(self) -> None:
        if self._handle is not None:
            # Closing the file releases the lock on every platform.
            self._handle.close()
            self._handle = None

    def __enter__(self) -> DataFolderLock:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def hold(data_dir: Path) -> DataFolderLock:
    """Take the data folder's lock, or raise DataFolderInUse.

    The lock file holds the owner's process id, for the message a second
    DataLab shows. It's opened without truncating, so a refused launch never
    erases it.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    handle = path.open("a+b")
    try:
        _lock(handle, path)
    except BaseException:
        handle.close()
        raise
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n".encode())
    handle.flush()
    return DataFolderLock(path, handle)


def holder(data_dir: Path) -> int | None:
    """The process id recorded by the DataLab holding the folder, if readable."""
    with contextlib.suppress(OSError, ValueError):
        return int((data_dir / LOCK_NAME).read_text(encoding="utf-8").split()[0])
    return None


def _lock(handle: IO[bytes], path: Path) -> None:
    if sys.platform == "win32":
        import msvcrt

        # Locks one byte far past anything written, so the process id stays
        # readable (Windows locks stop other processes reading what they cover).
        handle.seek(1 << 20)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise DataFolderInUse(str(path)) from error
        return
    try:
        import fcntl
    except ImportError:
        _fallback(path)
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise DataFolderInUse(str(path)) from error
    except OSError:
        # Some network file systems don't support locks at all.
        _fallback(path)


def _fallback(path: Path) -> None:
    """Without OS locks: refuse if the recorded process is still alive.

    Weaker than a lock (two launches at the same instant could both pass),
    but still stops the usual case of starting DataLab twice.
    """
    pid = holder(path.parent)
    if pid is None or pid == os.getpid():
        log.warning("file locks aren't available for %s; checking its process id only", path)
        return
    try:
        os.kill(pid, 0)  # POSIX only (this is never reached on Windows): no signal is sent
    except ProcessLookupError:
        return
    except PermissionError:
        pass  # alive, and someone else's
    raise DataFolderInUse(str(path))


# Locks this process holds until it exits, by data folder.
_held: dict[Path, DataFolderLock] = {}


def refuse_second_instance(data_dir: Path, profile: str) -> DataFolderLock:
    """Take the data folder's lock for the rest of this process, or exit with
    a message saying another DataLab has it."""
    key = data_dir.resolve()
    if key in _held:
        return _held[key]
    try:
        _held[key] = hold(data_dir)
    except DataFolderInUse:
        pid = holder(data_dir)
        who = f" (process {pid})" if pid else ""
        sys.exit(
            f"Another {profile} DataLab{who} is already using the data folder {data_dir}. "
            "Use that one, or stop it first."
        )
    return _held[key]
