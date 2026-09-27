"""One DataLab per data folder."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys

import pytest

from datalab import datalock
from datalab.datalock import DataFolderInUse, hold, holder


def test_a_second_datalab_on_the_same_folder_is_refused(tmp_path):
    with hold(tmp_path / "data"):
        assert holder(tmp_path / "data") == os.getpid()
        with pytest.raises(DataFolderInUse):
            hold(tmp_path / "data")
        # A different folder is a different DataLab.
        with hold(tmp_path / "other"):
            pass
    # Released when the first one stops.
    with hold(tmp_path / "data"):
        pass


def test_a_refused_launch_leaves_the_owners_record_alone(tmp_path):
    with hold(tmp_path):
        with pytest.raises(DataFolderInUse):
            hold(tmp_path)
        assert holder(tmp_path) == os.getpid()


def test_another_process_is_refused_with_a_message(tmp_path):
    # The eval runner and the tests use fresh folders, so they never meet this.
    datalock.refuse_second_instance(tmp_path, "practice")
    code = (
        "import sys; from pathlib import Path; from datalab.datalock import refuse_second_instance;"
        f"refuse_second_instance(Path({str(tmp_path)!r}), 'practice')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Another practice DataLab" in result.stderr
    assert f"process {os.getpid()}" in result.stderr
    # The same process asking again keeps the lock it has.
    assert (
        datalock.refuse_second_instance(tmp_path, "practice") is datalock._held[tmp_path.resolve()]
    )


def test_the_lock_goes_when_the_process_does(tmp_path):
    code = (
        "from pathlib import Path; from datalab.datalock import hold;"
        f"hold(Path({str(tmp_path)!r})); import os; os._exit(0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
    with hold(tmp_path):
        pass


def write_owner(folder, **record):
    (folder / ".lock").write_text(json.dumps(record))


def test_the_lock_records_its_owner(tmp_path):
    with hold(tmp_path):
        record = datalock.owner(tmp_path)
        assert record is not None
        assert record["pid"] == os.getpid()
        assert record["host"] == socket.gethostname()
        assert record["started"] == datalock._started(os.getpid())


def test_without_os_locks_a_live_owner_still_refuses(tmp_path):
    parent = os.getppid()
    write_owner(tmp_path, pid=parent, host=socket.gethostname(), started=datalock._started(parent))
    with pytest.raises(DataFolderInUse):
        datalock._fallback(tmp_path / ".lock")


def test_without_os_locks_an_owner_that_isnt_running_is_taken_over(tmp_path, caplog):
    host = socket.gethostname()
    parent = os.getppid()
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    for record in (
        # The process has ended.
        {"pid": ended.pid, "host": host, "started": "then"},
        # Its id was reused by a process that started later.
        {"pid": parent, "host": host, "started": "Mon Jan  1 00:00:00 2001"},
        # It was on another computer sharing the folder, which can't be checked.
        {"pid": parent, "host": "elsewhere", "started": datalock._started(parent)},
    ):
        write_owner(tmp_path, **record)
        with caplog.at_level(logging.WARNING):
            datalock._fallback(tmp_path / ".lock")  # no refusal
    assert sum("taking over" in r.message for r in caplog.records) == 3
    # An unreadable record is taken over too.
    (tmp_path / ".lock").write_text("12345\n")
    datalock._fallback(tmp_path / ".lock")


def test_locks_left_by_a_test_are_let_go(tmp_path):
    datalock.refuse_second_instance(tmp_path, "practice")
    datalock.release_all()
    assert datalock._held == {}
    with hold(tmp_path):
        pass
