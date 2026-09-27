"""The updater: side-by-side install, the marker's states, the launcher switch,
the restart, and undoing each step that fails.

Nothing is downloaded or installed for real: GitHub is `FakeGitHub`, and
`uv`, the new version and `docker pull` are a fake command runner that makes
folders in tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from datalab import db, updater, updates
from datalab.config import Settings
from datalab.db import backups
from datalab.releases import ChecksumMismatch, ReleaseSource, UpdateChecker
from datalab.updater import Layout, Relaunch, UpdateFailed, Updater, check_images
from tests.release_fakes import (
    AGENT,
    GATEWAY,
    PROXY,
    REPO,
    FakeGitHub,
    images_bytes,
    sums_for,
    wheel_bytes,
)

OLD, NEW = "0.1.0a2", "0.1.0a3"


class FakeRunner:
    """`uv venv`, `uv pip install`, `<new>/bin/datalab --version` and `pull-images`."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.commands: list[list[str]] = []
        self.cwds: list[Path | None] = []
        self.fail: str | None = None  # "venv", "pip", "version", "pull-images"
        self.says = NEW
        self.events: list[str] | None = None

    def __call__(
        self, command: Sequence[str], timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = list(command)
        self.commands.append(command)
        self.cwds.append(cwd)
        step = self._step(command)
        if self.events is not None:
            self.events.append(step)
        if step == self.fail:
            return subprocess.CompletedProcess(command, 1, "", "it went wrong")
        if step == "venv":
            folder = Path(command[-1])
            (folder / "bin").mkdir(parents=True)
            (folder / "bin" / "python").write_text("")
        elif step == "pip":
            python = Path(command[command.index("--python") + 1])
            (python.parent / "datalab").write_text("#!/bin/sh\n")
        elif step == "version":
            return subprocess.CompletedProcess(command, 0, f"datalab {self.says}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    @staticmethod
    def _step(command: list[str]) -> str:
        if command[1:2] == ["venv"]:
            return "venv"
        if command[1:3] == ["pip", "install"]:
            return "pip"
        if command[-1] == "--version":
            return "version"
        if command[-1] == "pull-images":
            return "pull-images"
        return "other"


class World:
    """A data folder, an install root with the running version, and a release on GitHub."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path / "data"
        self.settings = Settings(profile="real", data_dir=self.data_dir, oracle=None)
        connection = db.connect(self.settings.database_file)
        connection.close()
        self.root = tmp_path / "app"
        running = self.root / "versions" / OLD
        (running / "bin").mkdir(parents=True)
        (running / ".complete").write_text("{}")
        (self.root / "current").write_text(f"{OLD}\n")
        self.layout = Layout(self.root, windows=False, prefix=running)
        self.github = FakeGitHub()
        self.github.release("v0.1.0-alpha.3", prerelease=True, notes="Faster exports.")
        self.checker = UpdateChecker(
            self.settings, source=ReleaseSource(REPO, http=self.github.client()), current=OLD
        )
        self.run = FakeRunner(self.layout)
        self.events: list[str] = []
        self.run.events = self.events
        self.spawned: list[list[str]] = []
        self.shutdowns = 0
        self.busy: str | None = None

    def updater(self, **overrides) -> Updater:
        async def stop_sessions() -> None:
            self.events.append("stop-sessions")

        def shutdown() -> bool:
            self.shutdowns += 1
            return True

        options = {
            "layout": self.layout,
            "current": OLD,
            "run": self.run,
            "uv": "/fake/uv",
            "platform": "darwin",
            "busy": lambda: self.busy,
            "stop_sessions": stop_sessions,
            "spawn_helper": self.spawned.append,
            "shutdown": shutdown,
            "python": "/old/python",
        }
        options.update(overrides)
        return Updater(self.settings, self.checker, **options)

    def check(self):
        found = self.checker.check()
        assert found.state == "available"
        return found

    def marker(self):
        return updates.read_marker(self.data_dir)

    def history(self) -> list[str]:
        return [e["outcome"] for e in updates.history(self.data_dir)]

    def backups(self) -> list[str]:
        return [
            b.reason for b in backups.list_backups(backups.backups_dir(self.settings.database_file))
        ]


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


async def install(world: World, **overrides) -> Updater:
    world.check()
    update = world.updater(**overrides)
    await update.start(NEW)
    await update.wait()
    return update


def assert_nothing_changed(world: World) -> None:
    assert world.marker() is None
    assert world.layout.pointer() == (OLD, None)
    assert not (world.root / "versions" / NEW).exists()
    assert (world.root / "versions" / OLD / ".complete").exists()


# ------------------------------------------------------------------ the whole way


async def test_an_update_installs_beside_backs_up_switches_and_restarts(world):
    update = await install(world)

    assert update.progress.state == "restarting", update.progress.message
    # Conversations stop before the backup; the new version is installed in
    # its own folder, pulls its images, and only then is the launcher switched.
    assert world.events == ["stop-sessions", "venv", "pip", "version", "pull-images"]
    marker = world.marker()
    assert marker is not None
    assert (marker.from_version, marker.to_version, marker.state) == (OLD, NEW, "switched")
    assert marker.backup is not None and world.backups() == ["update"]
    assert world.layout.pointer() == (NEW, OLD)
    assert world.layout.complete(NEW)
    # The running version's folder is untouched and kept, to go back to.
    assert (world.root / "versions" / OLD / ".complete").read_text() == "{}"
    assert not (world.root / "downloads" / NEW).exists()
    venv, pip, *_ = world.run.commands
    assert venv[:4] == ["/fake/uv", "venv", "--python", "3.13"]
    assert venv[-1] == str(world.root / "versions" / NEW)
    assert pip[-1].endswith(f"datalab-{NEW}-py3-none-any.whl")
    # uv gets constraints.txt by its plain name, from its folder: uv cuts a
    # --constraints path at its first space ("Application Support").
    assert pip[pip.index("--constraints") + 1] == "constraints.txt"
    assert world.run.cwds[1] == world.root / "downloads" / NEW
    # The restart helper runs on the old version's Python, then DataLab quits.
    [helper] = world.spawned
    assert helper[:4] == ["/old/python", "-m", "datalab.updater", "relaunch"]
    assert helper[helper.index("--to") + 1] == NEW and helper[helper.index("--from") + 1] == OLD
    assert world.shutdowns == 1


async def test_the_new_version_finishes_the_update_when_it_starts(world):
    await install(world)
    # What `datalab serve` does in the new version: recover, then finish.
    recovery = updates.recover(
        world.data_dir, world.settings.database_file, app_version=NEW, known=db.known_migrations()
    )
    assert recovery is not None and recovery.outcome == "finishing"
    updates.finish(world.data_dir, NEW)
    assert world.marker() is None and world.history() == ["finished"]


# ------------------------------------------------------------------ refusals


async def test_installing_needs_the_version_on_offer(world):
    update = world.updater()
    with pytest.raises(UpdateFailed, match="isn't the one on offer"):
        await update.start(NEW)  # nothing checked yet
    world.check()
    with pytest.raises(UpdateFailed, match="isn't the one on offer"):
        await update.start("0.9.0")


async def test_it_waits_while_something_is_working(world):
    world.check()
    world.busy = "A conversation's agent is working."
    with pytest.raises(UpdateFailed, match="agent is working"):
        await world.updater().start(NEW)
    assert_nothing_changed(world)


async def test_something_started_during_the_download_is_never_cut_off(world):
    world.check()
    update = world.updater()
    await update.start(NEW)
    world.busy = "A conversation's agent is working."  # while it downloads
    await update.wait()
    assert update.progress.state == "failed" and "agent is working" in update.progress.message
    assert "stop-sessions" not in world.events
    assert_nothing_changed(world)
    assert not (world.root / "downloads" / NEW).exists()


async def test_it_wont_start_over_an_unfinished_update(world):
    world.check()
    updates.begin(
        world.data_dir, world.settings.database_file, from_version=OLD, to_version="0.1.0a9"
    )
    with pytest.raises(UpdateFailed, match="hasn't finished"):
        await world.updater().start(NEW)


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"platform": "linux"}, "Mac and Windows"),
        ({"uv": ""}, "uv"),
    ],
)
async def test_why_it_cant_update_itself(world, overrides, why):
    world.check()
    update = world.updater(**overrides)
    assert why in (update.why_not() or "")
    with pytest.raises(UpdateFailed):
        await update.start(NEW)


def test_a_copy_not_from_the_installer_cant_update_itself(world, tmp_path):
    layout = Layout(world.root, windows=False, prefix=tmp_path / "somewhere" / ".venv")
    assert "wasn't installed by the DataLab installer" in (
        world.updater(layout=layout).why_not() or ""
    )


# ------------------------------------------------------------------ failures, each undone


async def test_a_download_that_fails_its_checksum_changes_nothing(world):
    world.check()
    # The package on GitHub is swapped after the check read SHA256SUMS.
    wheel_id = next(
        int(a["url"].rsplit("/", 1)[1])
        for a in world.github.releases[0]["assets"]
        if a["name"].endswith(".whl")
    )
    world.github.files[wheel_id] = b"something else"
    update = world.updater()
    await update.start(NEW)
    await update.wait()
    assert update.progress.state == "failed"
    assert "didn't pass their checks" in update.progress.message
    assert "stop-sessions" not in world.events  # conversations kept going
    assert_nothing_changed(world)
    assert world.backups() == [] and world.history() == []
    assert not (world.root / "downloads" / NEW).exists()


async def test_being_offline_mid_download_changes_nothing(world):
    world.check()
    import httpx

    def offline(request):
        raise httpx.ConnectError("gone", request=request)

    world.github.answer = offline
    update = world.updater()
    await update.start(NEW)
    await update.wait()
    assert update.progress.state == "failed" and "Couldn't download" in update.progress.message
    assert_nothing_changed(world)


async def test_a_backup_that_fails_stops_the_update(world, monkeypatch):
    def full_disk(*args, **kwargs):
        raise backups.BackupFailed("No space left on device")

    monkeypatch.setattr(backups, "take_backup", full_disk)
    update = await install(world)
    assert update.progress.state == "failed" and "couldn't back up" in update.progress.message
    assert_nothing_changed(world)
    assert world.history() == ["abandoned"]
    assert "venv" not in world.events


@pytest.mark.parametrize("step", ["venv", "pip", "version", "pull-images"])
async def test_a_failed_install_step_is_undone(world, step):
    world.run.fail = step
    update = await install(world)
    assert update.progress.state == "failed", update.progress.message
    assert f"{OLD} is still the one in use" in update.progress.message
    assert_nothing_changed(world)
    # The marker is cleared as abandoned; the backup stays (it does no harm).
    assert world.history() == ["abandoned"] and world.backups() == ["update"]


async def test_a_new_version_that_says_its_another_is_undone(world):
    world.run.says = "0.1.0a1"
    update = await install(world)
    assert update.progress.state == "failed" and "says it's" in update.progress.message
    assert_nothing_changed(world)


async def test_a_switch_that_fails_puts_the_launcher_back(world, monkeypatch):
    real_advance = updates.advance

    def advance(data_dir, state):
        if state == "switched":
            raise OSError("disk went away")
        return real_advance(data_dir, state)

    monkeypatch.setattr(updates, "advance", advance)
    update = await install(world)
    assert update.progress.state == "failed"
    assert world.layout.pointer() == (OLD, None)
    assert_nothing_changed(world)
    assert world.history() == ["abandoned"]


async def test_an_interruption_mid_install_is_still_undone(world):
    class Interrupted(BaseException):
        pass

    def interrupted(command, timeout, cwd=None):
        raise Interrupted()

    world.check()
    staged = world.updater().download(world.checker.last.release, world.checker.last.expected)  # type: ignore[arg-type]
    with pytest.raises(Interrupted):
        world.updater(run=interrupted).apply(staged)
    assert_nothing_changed(world)


def test_a_version_installed_before_is_reused_and_kept(world):
    world.check()
    (world.root / "versions" / NEW / "bin").mkdir(parents=True)
    (world.root / "versions" / NEW / ".complete").write_text("{}")
    world.run.fail = "pull-images"
    update = world.updater()
    staged = update.download(world.checker.last.release, world.checker.last.expected)  # type: ignore[arg-type]
    with pytest.raises(UpdateFailed):
        update.apply(staged)
    assert "venv" not in world.events  # not installed again
    assert world.layout.complete(NEW)  # and not removed: this update didn't make it


def test_a_half_installed_folder_from_before_is_replaced(world):
    world.check()
    (world.root / "versions" / NEW / "bin").mkdir(parents=True)  # no .complete
    (world.root / "versions" / NEW / "stale").write_text("x")
    update = world.updater()
    staged = update.download(world.checker.last.release, world.checker.last.expected)  # type: ignore[arg-type]
    update.apply(staged)
    assert not (world.root / "versions" / NEW / "stale").exists()
    assert world.layout.complete(NEW) and world.layout.pointer() == (NEW, OLD)


# ------------------------------------------------------------------ cut off part way


@pytest.mark.parametrize("state", ["started", "backed-up", "installed"])
def test_an_update_cut_off_before_the_switch_is_abandoned_at_the_next_start(world, state):
    """Power lost part way: the launcher still opens the old version, whose
    startup recovery clears the marker."""
    updates.begin(world.data_dir, world.settings.database_file, from_version=OLD, to_version=NEW)
    if state == "installed":
        updates.advance(world.data_dir, "installed")
    recovery = updates.recover(
        world.data_dir, world.settings.database_file, app_version=OLD, known=db.known_migrations()
    )
    assert recovery is not None and recovery.outcome == "abandoned"
    assert world.marker() is None


def test_an_update_cut_off_after_the_switch_is_finished_by_the_new_version(world):
    updates.begin(world.data_dir, world.settings.database_file, from_version=OLD, to_version=NEW)
    updates.advance(world.data_dir, "installed")
    updates.advance(world.data_dir, "switched")
    recovery = updates.recover(
        world.data_dir, world.settings.database_file, app_version=NEW, known=db.known_migrations()
    )
    assert recovery is not None and recovery.outcome == "finishing"


def test_abandon_refuses_once_the_new_version_changed_the_database(world):
    updates.begin(world.data_dir, world.settings.database_file, from_version=OLD, to_version=NEW)
    known = db.known_migrations()
    connection = sqlite3.connect(world.settings.database_file)
    connection.execute("INSERT INTO schema_migrations VALUES ('9999_new.sql', '')")
    connection.commit()
    connection.close()
    with pytest.raises(updates.UpdateError):
        updates.abandon(world.data_dir, world.settings.database_file, app_version=OLD, known=known)
    assert world.marker() is not None  # left for the startup recovery


# ------------------------------------------------------------------ restarting


async def test_if_the_helper_cant_start_it_says_to_reopen(world):
    def refuse(command):
        raise OSError("no")

    update = await install(world, spawn_helper=refuse)
    assert update.progress.state == "restarting"
    assert "Quit DataLab and open it again" in update.progress.message
    assert world.shutdowns == 0


async def test_without_a_way_to_quit_it_says_to_reopen(world):
    update = await install(world, shutdown=None)
    assert "Quit DataLab and open it again" in update.progress.message


class Fake:
    """Processes, the data folder's lock and time, for the restart helper."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.now = 0.0
        self.alive: set[int] = {100}
        self.owner = 100
        self.launched: list[list[str]] = []
        # What happens when a DataLab is opened: a function of the launch count.
        self.on_launch = self.new_version_starts

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.now > 2:
            self.alive.discard(100)  # the old DataLab has quit

    def launch(self, command: list[str]) -> None:
        self.launched.append(command)
        self.on_launch()

    def new_version_starts(self) -> None:
        self.alive.add(200)
        self.owner = 200
        updates.finish(self.world.data_dir, NEW)

    def relaunch(self) -> Relaunch:
        return Relaunch(
            self.world.layout,
            self.world.data_dir,
            "real",
            NEW,
            OLD,
            100,
            alive=lambda pid: pid in self.alive,
            owner=lambda data_dir: self.owner,
            launch=self.launch,
            platform="darwin",
            clock=self.clock,
            sleep=self.sleep,
        )


@pytest.fixture
def switched(world) -> World:
    """An update that got as far as the switch."""
    (world.root / "versions" / NEW / "bin").mkdir(parents=True)
    (world.root / "versions" / NEW / ".complete").write_text("{}")
    updates.begin(world.data_dir, world.settings.database_file, from_version=OLD, to_version=NEW)
    updates.advance(world.data_dir, "installed")
    world.layout.switch(NEW, previous=OLD)
    updates.advance(world.data_dir, "switched")
    return world


def test_the_helper_waits_for_the_old_one_then_opens_the_new(switched):
    fake = Fake(switched)
    assert fake.relaunch().run() == "finished"
    [command] = fake.launched
    assert command[0] == "osascript" and str(switched.layout.shim) in command[-1]
    assert fake.now > 2  # not before the old DataLab quit
    assert switched.layout.pointer() == (NEW, OLD)


def test_a_new_version_that_doesnt_start_means_going_back(switched):
    fake = Fake(switched)
    fake.on_launch = lambda: None  # never takes the data folder
    assert fake.relaunch().run() == "went-back"
    assert len(fake.launched) == 2  # the new one, then the old one again
    assert switched.layout.pointer() == (OLD, NEW)
    # The old version's startup recovery sorts out the marker.
    recovery = updates.recover(
        switched.data_dir,
        switched.settings.database_file,
        app_version=OLD,
        known=db.known_migrations(),
    )
    assert recovery is not None and recovery.outcome == "abandoned"


def test_a_new_version_that_stops_before_finishing_means_going_back(switched):
    fake = Fake(switched)

    def starts_then_dies() -> None:
        if len(fake.launched) == 1:
            fake.owner = 200  # took the lock, then crashed (not alive)

    fake.on_launch = starts_then_dies
    assert fake.relaunch().run() == "went-back"
    assert switched.layout.pointer()[0] == OLD


def test_a_slow_start_is_left_alone(switched):
    fake = Fake(switched)

    def starts_slowly() -> None:
        fake.alive.add(200)
        fake.owner = 200  # holds the folder, never finishes

    fake.on_launch = starts_slowly
    assert fake.relaunch().run() == "stuck"
    assert len(fake.launched) == 1 and switched.layout.pointer() == (NEW, OLD)


def test_if_the_old_one_never_quits_nothing_is_opened(switched):
    fake = Fake(switched)
    fake.sleep = lambda seconds: setattr(fake, "now", fake.now + seconds)  # type: ignore[method-assign]
    assert fake.relaunch().run() == "old-still-running"
    assert fake.launched == []


def test_the_launch_command_quotes_paths_for_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / 'it\'s "here"'))
    layout = Layout(tmp_path / "Application Support" / "app", windows=False)
    command = updater.launch_command(layout, "practice", "darwin")
    assert command[:2] == ["osascript", "-e"]
    script = command[-1]
    assert script.startswith('tell application "Terminal" to do script "')
    assert "--profile practice serve" in script
    assert "Application Support/app/bin/datalab'" in script  # single-quoted for the shell
    assert '\\"here\\"' in script  # and escaped for AppleScript


def test_the_launch_command_on_windows(tmp_path):
    """UNTESTED on a real Windows machine: only the command it builds."""
    layout = Layout(Path("C:/Users/o'neil/AppData/Local/DataLab/app"), windows=True)
    command = updater.launch_command(layout, "real", "win32")
    assert command[:3] == ["powershell.exe", "-NoExit", "-Command"]
    assert "o''neil" in command[3] and command[3].endswith("datalab.cmd' --profile real serve")


# ------------------------------------------------------------------ layout, images, busy


def test_the_windows_layout(tmp_path):
    """UNTESTED on a real Windows machine: the paths it uses."""
    layout = Layout(tmp_path, windows=True)
    assert layout.executable("0.2.0") == tmp_path / "versions" / "0.2.0" / "Scripts" / "datalab.exe"
    assert layout.python("0.2.0") == tmp_path / "versions" / "0.2.0" / "Scripts" / "python.exe"
    assert layout.shim == tmp_path / "bin" / "datalab.cmd"


def test_the_layout_lists_complete_versions_and_switches_only_to_one(tmp_path):
    layout = Layout(tmp_path, windows=False)
    for version in ("0.10.0", "0.9.0", "0.2.0a1"):
        (tmp_path / "versions" / version).mkdir(parents=True)
        (tmp_path / "versions" / version / ".complete").write_text("{}")
    (tmp_path / "versions" / "0.11.0").mkdir()  # half installed
    assert layout.installed() == ["0.2.0a1", "0.9.0", "0.10.0"]
    with pytest.raises(UpdateFailed):
        layout.switch("0.11.0", previous="0.10.0")
    layout.switch("0.10.0", previous=None)
    assert layout.pointer() == ("0.10.0", None)
    with pytest.raises(UpdateFailed):
        layout.folder("../etc")


def test_images_must_be_pinned_and_match_the_package(tmp_path):
    wheel = tmp_path / "datalab.whl"
    wheel.write_bytes(wheel_bytes(NEW))
    assert check_images(images_bytes(), wheel) == {
        "agent": AGENT,
        "gateway": GATEWAY,
        "proxy": PROXY,
    }
    with pytest.raises(ChecksumMismatch, match="by digest"):
        check_images(images_bytes(agent="ghcr.io/sripadalab-um/datalab-agent:latest"), wheel)
    other = "ghcr.io/sripadalab-um/datalab-agent@sha256:" + "d" * 64
    with pytest.raises(ChecksumMismatch, match="different agent"):
        check_images(images_bytes(agent=other), wheel)
    with pytest.raises(ChecksumMismatch):
        check_images(b'{"agent": "x"}', wheel)
    bare = tmp_path / "bare.whl"
    with zipfile.ZipFile(bare, "w") as package:
        package.writestr("datalab/__init__.py", "")
    with pytest.raises(ChecksumMismatch, match="which images"):
        check_images(images_bytes(), bare)


async def test_a_release_whose_images_dont_match_its_package_isnt_installed(world):
    world.github.release(
        "v0.1.0-alpha.4",
        prerelease=True,
        files={
            "datalab-0.1.0a4-py3-none-any.whl": wheel_bytes("0.1.0a4"),
            "constraints.txt": b"",
            "images.json": images_bytes(gateway="docker.io/library/nginx@sha256:" + "e" * 64),
        },
    )
    found = world.checker.check()
    assert found.release is not None and found.release.version == "0.1.0a4"
    update = world.updater()
    await update.start("0.1.0a4")
    await update.wait()
    assert update.progress.state == "failed" and "different gateway" in update.progress.message
    assert world.marker() is None and "stop-sessions" not in world.events


def test_busy_reasons(tmp_path):
    connection = db.connect(tmp_path / "datalab.sqlite")
    assert updater.busy_reason(connection, lambda: False) is None
    assert "agent is working" in (updater.busy_reason(connection, lambda: True) or "")
    connection.execute(
        "INSERT INTO queries (id, session_id, sql_text, status, started_at) "
        "VALUES ('q1', 's1', 'select 1', 'running', '2026-09-27')"
    )
    assert "query is running" in (updater.busy_reason(connection, lambda: False) or "")


def test_sums_for_the_fake_release_match_its_files():
    # (The fake itself: SHA256SUMS lists what the release carries.)
    text = sums_for({"a": b"1"}).decode()
    assert text.endswith("  a\n") and json.dumps(text)
