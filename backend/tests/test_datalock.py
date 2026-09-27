"""One DataLab per data folder."""

from __future__ import annotations

import os
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


def test_another_process_is_refused_with_a_message(tmp_path, monkeypatch):
    # The eval runner and the tests use fresh folders, so they never meet this.
    monkeypatch.setattr(datalock, "_held", {})
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
    datalock._held[tmp_path.resolve()].release()


def test_the_lock_goes_when_the_process_does(tmp_path):
    code = (
        "from pathlib import Path; from datalab.datalock import hold;"
        f"hold(Path({str(tmp_path)!r})); import os; os._exit(0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
    with hold(tmp_path):
        pass


def test_without_os_locks_a_live_owner_still_refuses(tmp_path, monkeypatch):
    (tmp_path / ".lock").write_text(f"{os.getppid()}\n")
    with pytest.raises(DataFolderInUse):
        datalock._fallback(tmp_path / ".lock")
    # A process that has ended doesn't hold anything.
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    (tmp_path / ".lock").write_text(f"{ended.pid}\n")
    datalock._fallback(tmp_path / ".lock")
