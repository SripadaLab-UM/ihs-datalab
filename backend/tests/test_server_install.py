"""`datalab server install` / `rollback` on the lab's server (server_install.py)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from datalab import db, updates
from datalab.config import ServerSettings
from datalab.server_install import InstallFailed, ServerInstaller, ServerLayout


class Machine:
    """The server's commands, pretended: systemctl, uv, the new release's own."""

    def __init__(self, layout: ServerLayout) -> None:
        self.layout = layout
        self.commands: list[list[str]] = []
        self.running = True
        self.starts_as: dict[str, str | None] = {}  # release id -> version it answers as
        self.fail: set[str] = set()

    def run(self, command, timeout):
        command = list(command)
        self.commands.append(command)
        words = " ".join(command)
        code = 1 if any(f in words for f in self.fail) else 0
        out = ""
        if command[-1] == "--version":
            release = Path(command[0]).parents[2].name
            out = f"datalab {release.split('-')[0]}"
        if "systemctl" in command and not code:
            self.running = command[-2] == "start"
        return subprocess.CompletedProcess(command, code, out, "it failed" if code else "")

    def health(self) -> str | None:
        if not self.running:
            return None
        return self.starts_as.get(self.layout.pointer("current") or "", None)


def wheel(folder: Path, version: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"datalab-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("datalab/__init__.py", f"__version__ = '{version}'\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "requirements.txt").write_text(
        "httpx==0.28.1 --hash=sha256:" + "a" * 64 + f"\n./{path.name} --hash=sha256:{digest}\n"
    )
    return path


@pytest.fixture
def server(settings, tmp_path):
    root = tmp_path / "datalab"
    layout = ServerLayout(root)
    # The release that runs now: 0.3.0, installed the same way.
    old = layout.folder("0.3.0-old")
    (old / "venv" / "lib" / "python3.13" / "site-packages" / "datalab" / "db" / "migrations").mkdir(
        parents=True
    )
    for migration in db.known_migrations():
        migrations = old / "venv/lib/python3.13/site-packages/datalab/db/migrations"
        (migrations / migration).write_text("")
    (old / ".complete").write_text(json.dumps({"wheel_sha256": "0", "version": "0.3.0"}))
    layout.point("current", "0.3.0-old")
    machine = Machine(layout)
    machine.starts_as["0.3.0-old"] = "0.3.0"
    server = ServerSettings(public_url="https://lab.example.edu", service="dl.service")
    on_server = replace(settings, deployment="server", server=server)
    on_server.data_dir.mkdir(parents=True)
    installer = ServerInstaller(
        on_server,
        layout,
        uv="uv",
        run=machine.run,
        say=lambda _: None,
        sleep=lambda _: None,
        health=machine.health,
    )
    return installer, layout, machine, tmp_path


def test_a_built_release_is_installed_beside_and_switched_to(server, monkeypatch):
    installer, layout, machine, tmp_path = server
    monkeypatch.setattr("datalab.server_install.START_SECONDS", 1)
    wheel(tmp_path / "build", "0.4.0")
    staged = installer.stage_folder(tmp_path / "build")
    machine.starts_as[staged.release_id] = "0.4.0"
    installer.install(staged)
    assert layout.pointer("current") == staged.release_id
    assert layout.pointer("previous") == "0.3.0-old"
    ran = [" ".join(c) for c in machine.commands]
    assert any("pip install" in c and "--require-hashes" in c for c in ran)
    assert any(c.endswith("--profile practice pull-images") for c in ran)
    # Installed and its images pulled before the service stopped.
    stop = next(i for i, c in enumerate(ran) if "systemctl stop dl.service" in c)
    assert stop > next(i for i, c in enumerate(ran) if "pull-images" in c)
    # The update marker, for the new release's start-up to finish.
    marker = updates.read_marker(installer.settings.data_dir)
    assert marker is not None and (marker.state, marker.to_version) == ("switched", "0.4.0")


def test_a_release_whose_files_dont_check_out_changes_nothing(server):
    installer, layout, machine, tmp_path = server
    wheel(tmp_path / "build", "0.4.0")
    requirements = tmp_path / "build" / "requirements.txt"
    requirements.write_text(requirements.read_text().replace("sha256:", "sha256:0", 1)[:-2] + "\n")
    with pytest.raises(InstallFailed, match="checks"):
        installer.stage_folder(tmp_path / "build")
    assert layout.pointer("current") == "0.3.0-old" and machine.commands == []


def test_a_release_that_doesnt_start_is_switched_back(server, monkeypatch):
    installer, layout, machine, tmp_path = server
    monkeypatch.setattr("datalab.server_install.START_SECONDS", 0.05)
    wheel(tmp_path / "build", "0.4.0")
    staged = installer.stage_folder(tmp_path / "build")  # never answers
    with pytest.raises(InstallFailed, match=r"0\.3\.0-old is running again"):
        installer.install(staged)
    assert layout.pointer("current") == "0.3.0-old"
    assert machine.running


def test_it_waits_for_running_work_and_gives_up_without_changing_anything(server):
    installer, layout, machine, tmp_path = server
    connection = db.connect(installer.settings.database_file)
    connection.execute(
        "INSERT INTO queries (id, session_id, started_at, status, sql_text) "
        "VALUES ('q1', 'c_1', 'now', 'running', 'SELECT 1 FROM DUAL')"
    )
    connection.close()
    wheel(tmp_path / "build", "0.4.0")
    staged = installer.stage_folder(tmp_path / "build")
    with pytest.raises(InstallFailed, match="query is running"):
        installer.install(staged, wait=0)
    assert layout.pointer("current") == "0.3.0-old"
    assert not any("systemctl" in c for c in machine.commands)


def test_rollback_goes_back_and_restores_the_database_only_when_it_must(server, monkeypatch):
    installer, layout, machine, _ = server
    monkeypatch.setattr("datalab.server_install.START_SECONDS", 1)
    new = layout.folder("0.4.0-new")
    new.mkdir()
    (new / ".complete").write_text(json.dumps({"wheel_sha256": "1", "version": "0.4.0"}))
    layout.point("previous", "0.3.0-old")
    layout.point("current", "0.4.0-new")
    db.connect(installer.settings.database_file).close()  # every migration 0.3.0 knows
    installer.rollback()
    assert (layout.pointer("current"), layout.pointer("previous")) == ("0.3.0-old", "0.4.0-new")
    assert not any(c[-1:] == ["rollback"] for c in machine.commands)
    # Back again, now with a migration the older release doesn't know.
    layout.point("current", "0.4.0-new")
    layout.point("previous", "0.3.0-old")
    connection = sqlite3.connect(installer.settings.database_file)
    connection.execute("INSERT INTO schema_migrations VALUES ('9999_newer.sql', 'now')")
    connection.commit()
    connection.close()
    installer.rollback(drop_since=True)
    assert [c for c in machine.commands if "rollback" in c][-1][-2:] == ["rollback", "--yes"]
    assert layout.pointer("current") == "0.3.0-old"


def test_its_only_for_the_server(settings, tmp_path):
    with pytest.raises(InstallFailed, match="server"):
        ServerInstaller(settings, ServerLayout(tmp_path))
