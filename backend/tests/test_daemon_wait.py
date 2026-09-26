import asyncio
import subprocess
import sys
import threading
import time

import pytest

from datalab.safety import _in_daemon_thread


def test_a_hanging_worker_is_abandoned_not_waited_for():
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(_in_daemon_thread(lambda: release.wait(60), 0.2))
    assert time.monotonic() - started < 2
    release.set()
    assert asyncio.run(_in_daemon_thread(lambda: 42, 1)) == 42
    with pytest.raises(ValueError):
        asyncio.run(_in_daemon_thread(lambda: int("x"), 1))


def test_a_database_that_never_answers_doesnt_stop_datalab_exiting():
    # A listener that accepts and never speaks: an Oracle login waits forever.
    script = """
import asyncio, socket
from dataclasses import replace
from datalab.config import OracleSettings, QueryLimits
from datalab.data.oracle import OracleDatabase
from datalab.safety import _in_daemon_thread
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
port = server.getsockname()[1]
settings = OracleSettings(
    host="127.0.0.1", port=port, service="X", user="U", keychain_service="t",
    read_only_roles=(), allowed_schemas=(),
)
database = OracleDatabase(settings, "p", QueryLimits())
try:
    asyncio.run(_in_daemon_thread(lambda: database.session_privileges(timeout=1), 2))
except TimeoutError:
    print("gave up")
"""
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert "gave up" in done.stdout, done.stderr
    assert time.monotonic() - started < 15


def test_a_stuck_database_worker_is_noticed():
    from datalab.safety import _database_worker_busy, _in_daemon_thread

    release = threading.Event()
    with pytest.raises(TimeoutError):
        asyncio.run(_in_daemon_thread(release.wait, 0.1))
    assert _database_worker_busy()  # a second check won't start another
    release.set()
    time.sleep(0.2)
    assert not _database_worker_busy()
