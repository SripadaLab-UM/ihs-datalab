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
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

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

    The lock file records the owner (process id, computer, and when that
    process started), for the message a second DataLab shows and for the
    fallback below. It's opened without truncating, so a refused launch
    never erases it.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    handle = path.open("a+b")
    try:
        _lock(handle, path)
    except BaseException:
        handle.close()
        raise
    owner = {"pid": os.getpid(), "host": socket.gethostname(), "started": _started(os.getpid())}
    handle.seek(0)
    handle.truncate()
    handle.write((json.dumps(owner) + "\n").encode())
    handle.flush()
    return DataFolderLock(path, handle)


def owner(data_dir: Path) -> dict[str, Any] | None:
    """What the DataLab holding (or last holding) the folder recorded, if readable."""
    with contextlib.suppress(OSError, ValueError):
        record = json.loads((data_dir / LOCK_NAME).read_text(encoding="utf-8"))
        if isinstance(record, dict) and isinstance(record.get("pid"), int):
            return record
    return None


def holder(data_dir: Path) -> int | None:
    """The process id recorded by the DataLab holding the folder, if readable."""
    record = owner(data_dir)
    return record["pid"] if record else None


# Windows can release a crashed process's locks a little late.
_WINDOWS_RETRY_SECONDS = 2.0
# Elsewhere a lock is only ever held briefly by the probe (`in_use`): a
# DataLab starting at that instant waits this long rather than refusing.
_POSIX_RETRY_SECONDS = 0.75


def _lock(handle: IO[bytes], path: Path, *, retry: bool = True) -> None:
    if sys.platform == "win32":
        import msvcrt

        # Locks one byte far past anything written, so the record stays
        # readable (Windows locks stop other processes reading what they cover).
        deadline = time.monotonic() + (_WINDOWS_RETRY_SECONDS if retry else 0)
        while True:
            handle.seek(1 << 20)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise DataFolderInUse(str(path)) from error
                time.sleep(0.1)
    try:
        import fcntl
    except ImportError:
        _fallback(path)
        return
    deadline = time.monotonic() + (_POSIX_RETRY_SECONDS if retry else 0)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise DataFolderInUse(str(path)) from error
            time.sleep(0.05)
        except OSError:
            # Some network file systems don't support locks at all.
            _fallback(path)
            return


def _fallback(path: Path) -> None:
    """Without OS locks: refuse only if the recorded owner is certainly still
    running, that is on this computer, alive, and started when it said (a
    process id can be reused). Otherwise the lock is taken over, with a
    warning. Weaker than a lock (two launches at the same instant could both
    pass), but it stops the usual case of starting DataLab twice.
    """
    log.warning("file locks aren't available for %s; checking its owner's record", path)
    record = owner(path.parent)
    if record is None or record["pid"] == os.getpid():
        return
    if record.get("host") != socket.gethostname():
        log.warning("taking over %s from a DataLab on %s", path, record.get("host"))
        return
    started = _started(record["pid"])
    if started is None or started != record.get("started"):
        log.warning("taking over %s: process %s has ended", path, record["pid"])
        return
    raise DataFolderInUse(str(path))


def _started(pid: int) -> str | None:
    """When a process started, as `ps` reports it, or None if it isn't running
    (or this isn't a system with `ps`)."""
    if sys.platform == "win32":
        return None
    try:
        output = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return output or None


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


def held(data_dir: Path) -> bool:
    """Whether this process holds the data folder's lock (see `refuse_second_instance`)."""
    return data_dir.resolve() in _held


def release_all() -> None:
    """Let go of every lock `refuse_second_instance` took (for tests)."""
    while _held:
        _, lock = _held.popitem()
        lock.release()


def in_use(data_dir: Path) -> bool:
    """Whether another process holds the data folder's lock.

    A probe: it takes the lock for an instant only if it's free, and never
    writes the owner's record, so it can't disturb a DataLab that is running
    or about to start (which would see the folder free a moment later)."""
    path = data_dir / LOCK_NAME
    if not path.exists() or held(data_dir):
        return False
    try:
        handle = path.open("a+b")
    except OSError:
        return False
    try:
        # No waiting: held means held (and the probe lets go at once).
        _lock(handle, path, retry=False)
    except DataFolderInUse:
        return True
    finally:
        handle.close()
    return False
