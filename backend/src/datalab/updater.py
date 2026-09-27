"""Installing a new DataLab release beside the running one, and restarting into it.

The steps (docs/DISTRIBUTION.md, "How an update is installed"), each
recorded in the update marker (updates.py):

1. **Download and check.** The release's package, `constraints.txt` and
   `images.json`, each refused unless it matches `SHA256SUMS` (and GitHub's
   own checksum, where it keeps one). `images.json` must pin every image by
   digest, and name the images the new package itself runs. Nothing has
   changed yet; a failure here leaves no trace but the log.
2. **Stop conversations**, then `updates.begin`: the marker, and a backup of
   the database ("backed-up").
3. **Install beside the old version**, in its own folder
   (`<install root>/versions/<version>/`, a Python environment `uv` makes),
   then let the new version pull its pinned images ("installed"). The
   running version's folder is never touched.
4. **Switch the launcher**: `<install root>/current` names the version the
   launcher opens, and `previous` the one it replaced ("switched"). Both are
   one-line files, replaced whole.
5. **Restart.** A small helper, run by the old version's own Python, waits
   for this DataLab to quit, opens the new one the way the launcher does,
   and waits for it to finish the update (it clears the marker once it's up).
   If the new version doesn't start, the helper puts the launcher back and
   opens the old version, whose startup recovery sorts out the rest.

If step 3 or 4 fails, the launcher is put back, what the step installed is
removed, and the marker cleared ("abandoned"). The previous version's folder
stays, so `datalab versions --use <version>` can go back to it (and then
`datalab rollback`, if the newer one changed the database).

Mac is the platform this has been tried on. The Windows paths (Scripts\\,
`datalab.cmd`, a new console for the restart) follow the same steps and are
covered by unit tests only: UNTESTED on a real Windows machine.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from datalab import __version__, datalock, updates
from datalab.config import Settings, default_data_dir
from datalab.releases import (
    CheckProblem,
    ChecksumMismatch,
    Release,
    UpdateChecker,
    is_newer,
    parse_version,
)

log = logging.getLogger(__name__)

INSTALL_DIR_ENV = "DATALAB_INSTALL_DIR"
_DIGEST = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
_COMPLETE = ".complete"
# How long the helper waits: for this DataLab to quit, for the new one to take
# the data folder, and for it to finish starting (migrations can take a while).
QUIT_SECONDS = 90
TAKE_SECONDS = 60
START_SECONDS = 10 * 60


class Runner(Protocol):
    def __call__(
        self, command: Sequence[str], timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]: ...


class UpdateFailed(RuntimeError):
    """An update step failed. Its message is for the person; nothing was left half-done."""


def run_command(
    command: Sequence[str], timeout: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd
    )


# ------------------------------------------------------------------ layout


def default_root() -> Path:
    """Where the installer puts DataLab's versions: beside the data folders
    (`~/Library/Application Support/DataLab/app`, `%LOCALAPPDATA%\\DataLab\\app`)."""
    override = os.environ.get(INSTALL_DIR_ENV)
    if override:
        return Path(override)
    return default_data_dir("real").parent / "app"


class Layout:
    """The installed versions, side by side, and which one the launcher opens.

    ```
    <root>/versions/<version>/   one Python environment per version
    <root>/current               the version the launcher opens
    <root>/previous              the one it opened before the last switch
    <root>/bin/datalab           the launcher's command: runs `current`
                                 (datalab.cmd on Windows)
    <root>/downloads/<version>/  a release's files while it's being installed
    ```
    """

    def __init__(
        self, root: Path, *, windows: bool | None = None, prefix: Path | None = None
    ) -> None:
        self.root = root
        self.windows = sys.platform == "win32" if windows is None else windows
        self._prefix = prefix if prefix is not None else Path(sys.prefix)

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def shim(self) -> Path:
        return self.root / "bin" / ("datalab.cmd" if self.windows else "datalab")

    def folder(self, version: str) -> Path:
        name = _folder_name(version)
        return self.versions / name

    def executable(self, version: str) -> Path:
        folder = self.folder(version)
        return folder / "Scripts" / "datalab.exe" if self.windows else folder / "bin" / "datalab"

    def python(self, version: str) -> Path:
        folder = self.folder(version)
        return folder / "Scripts" / "python.exe" if self.windows else folder / "bin" / "python"

    def complete(self, version: str) -> bool:
        return (self.folder(version) / _COMPLETE).is_file()

    def installed(self) -> list[str]:
        """The versions installed completely, oldest first."""
        if not self.versions.is_dir():
            return []
        found = [p.name for p in self.versions.iterdir() if (p / _COMPLETE).is_file()]
        return sorted(found, key=lambda v: parse_version(v) or parse_version("0"))  # type: ignore[arg-type,return-value]

    def running_version(self) -> str | None:
        """This process's version folder's name, if it runs from this layout."""
        with contextlib.suppress(OSError):
            prefix = self._prefix.resolve()
            if prefix.parent == self.versions.resolve() and (prefix / _COMPLETE).is_file():
                return prefix.name
        return None

    def pointer(self) -> tuple[str | None, str | None]:
        return _read_line(self.root / "current"), _read_line(self.root / "previous")

    def switch(self, to: str, previous: str | None) -> None:
        """Point the launcher at `to`. `previous` first, so a switch cut off
        half way still names both."""
        if not self.complete(to):
            raise UpdateFailed(f"DataLab {to} isn't installed completely.")
        if previous is not None:
            _write_line(self.root / "previous", previous)
        _write_line(self.root / "current", to)

    def restore(self, pointer: tuple[str | None, str | None]) -> None:
        current, previous = pointer
        if previous is None:
            (self.root / "previous").unlink(missing_ok=True)
        else:
            _write_line(self.root / "previous", previous)
        if current is not None:
            _write_line(self.root / "current", current)


def _folder_name(version: str) -> str:
    parsed = parse_version(version)
    if parsed is None or str(parsed) != version:
        raise UpdateFailed(f"{version!r} isn't a DataLab version.")
    return version


def _read_line(path: Path) -> str | None:
    with contextlib.suppress(OSError, UnicodeDecodeError):
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    return None


def _write_line(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_text(text + "\n", encoding="utf-8")
    with temporary.open("rb+") as file:
        os.fsync(file.fileno())
    os.replace(temporary, path)


# ------------------------------------------------------------------ checks


@dataclass(frozen=True)
class Staged:
    """A release's files, downloaded and checked."""

    version: str
    folder: Path
    wheel: Path
    constraints: Path
    images: dict[str, str]


def check_images(images_json: bytes, wheel: Path) -> dict[str, str]:
    """`images.json`, if it pins every image by digest and names exactly the
    images the new package runs (its `release.json` and `containers.py`)."""
    try:
        images = json.loads(images_json)
    except ValueError:
        raise ChecksumMismatch("images.json isn't readable.") from None
    if not isinstance(images, dict) or set(images) != {"agent", "gateway", "proxy"}:
        raise ChecksumMismatch("images.json doesn't list the agent, gateway and proxy images.")
    for role, image in images.items():
        if not isinstance(image, str) or not _DIGEST.fullmatch(image):
            raise ChecksumMismatch(f"images.json doesn't pin the {role} image by digest.")
    try:
        with zipfile.ZipFile(wheel) as package:
            release = json.loads(package.read("datalab/release.json"))
            containers = package.read("datalab/sessions/containers.py").decode("utf-8")
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        raise ChecksumMismatch("The package doesn't say which images it runs.") from None
    runs = {
        "agent": release.get("agent_image") if isinstance(release, dict) else None,
        "gateway": _constant(containers, "GATEWAY_IMAGE"),
        "proxy": _constant(containers, "PROXY_IMAGE"),
    }
    for role, image in runs.items():
        if image != images[role]:
            raise ChecksumMismatch(f"images.json and the package name different {role} images.")
    return images


def _constant(source: str, name: str) -> str | None:
    match = re.search(rf'^{name} = "(.*)"$', source, re.MULTILINE)
    return match.group(1) if match else None


def busy_reason(database: sqlite3.Connection, conversations_busy: Callable[[], bool]) -> str | None:
    """Why an update shouldn't start now: something is working."""
    if conversations_busy():
        return "A conversation's agent is working. Wait for it to finish, then install the update."
    with contextlib.suppress(sqlite3.Error):
        if database.execute("SELECT 1 FROM queries WHERE status = 'running' LIMIT 1").fetchone():
            return "A query is running. Wait for it to finish, then install the update."
        running = database.execute(
            "SELECT 1 FROM workflow_runs WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if running:
            return "A workflow run is going. Wait for it to finish, then install the update."
    return None


# ------------------------------------------------------------------ updater

Step = Literal[
    "idle",
    "downloading",
    "stopping",
    "backing-up",
    "installing",
    "pulling-images",
    "switching",
    "restarting",
    "failed",
]


@dataclass(frozen=True)
class Progress:
    state: Step = "idle"
    version: str | None = None
    message: str = ""
    started_at: str | None = None
    updated_at: str | None = None


def _nothing_busy() -> str | None:
    return None


class Updater:
    """Installs the release the last check found, when the person confirms."""

    def __init__(
        self,
        settings: Settings,
        checker: UpdateChecker,
        *,
        layout: Layout | None = None,
        current: str = __version__,
        run: Runner = run_command,
        uv: str | None = None,
        platform: str = sys.platform,
        known: Callable[[], set[str]] | None = None,
        busy: Callable[[], str | None] = _nothing_busy,
        stop_sessions: Callable[[], Awaitable[None]] | None = None,
        spawn_helper: Callable[[list[str]], None] | None = None,
        shutdown: Callable[[], bool] | None = None,
        python: str = sys.executable,
    ) -> None:
        self.settings = settings
        self.checker = checker
        self.layout = layout or Layout(default_root(), windows=platform == "win32")
        self.current = current
        self._run = run
        self._uv = uv if uv is not None else _find_uv(platform)
        self._platform = platform
        self._known = known or _known_migrations
        self._busy = busy
        self._stop_sessions = stop_sessions
        self._spawn = spawn_helper or _spawn_detached
        self._shutdown = shutdown
        self._python = python
        self._progress = Progress()
        self._task: asyncio.Task[None] | None = None

    @property
    def progress(self) -> Progress:
        return self._progress

    def why_not(self) -> str | None:
        """Why this DataLab can't install an update itself, or None."""
        if self._platform not in ("darwin", "win32"):
            return "Updating from inside DataLab works on Mac and Windows."
        if self.layout.running_version() is None:
            return (
                "This copy of DataLab wasn't installed by the DataLab installer (it's a "
                "development copy, or from an older installer), so it can't update itself. "
                "Install the new version with the installer instead."
            )
        if not self._uv:
            return "uv, which installs DataLab, wasn't found. Run the DataLab installer again."
        return None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, version: str) -> Progress:
        """Start installing `version`, which the last check offered. The person
        has confirmed (the route insists). Returns at once; `progress` follows it."""
        if self.running():
            raise UpdateFailed("An update is already being installed.")
        why = self.why_not()
        if why is not None:
            raise UpdateFailed(why)
        last = self.checker.last
        release = last.release
        if last.state != "available" or release is None or release.version != version:
            raise UpdateFailed("That version isn't the one on offer. Check for updates again.")
        if not is_newer(release.version, self.current):
            raise UpdateFailed(f"DataLab {release.version} isn't newer than this one.")
        with contextlib.suppress(updates.UnreadableMarker):
            if updates.read_marker(self.settings.data_dir) is not None:
                raise UpdateFailed(
                    "An earlier update hasn't finished. Quit DataLab and open it again so it "
                    "can sort that out, then update."
                )
        busy = self._busy()
        if busy is not None:
            raise UpdateFailed(busy)
        self._set("downloading", release.version, f"Downloading DataLab {release.version}…")
        self._task = asyncio.create_task(self._install(release, dict(last.expected)))
        return self._progress

    async def wait(self) -> None:
        """For tests: until the update has got as far as it will."""
        if self._task is not None:
            await self._task

    async def _install(self, release: Release, expected: dict[str, str]) -> None:
        version = release.version
        try:
            staged = await asyncio.to_thread(self.download, release, expected)
            try:
                self._set("stopping", version, "Stopping conversations…")
                if self._stop_sessions is not None:
                    await self._stop_sessions()
                await asyncio.to_thread(self.apply, staged)
            finally:
                shutil.rmtree(staged.folder, ignore_errors=True)
        except UpdateFailed as failed:
            self._set("failed", version, str(failed))
            return
        except Exception as error:
            log.exception("the update to %s failed", version)
            self._set("failed", version, f"The update failed ({type(error).__name__}).")
            return
        self._set(
            "restarting",
            version,
            f"DataLab {version} is installed. DataLab is restarting; it opens again in a "
            "new window in a moment.",
        )
        await asyncio.to_thread(self.restart, version)

    # The steps, each usable on its own (tests, and `datalab update`) ---------

    def download(self, release: Release, expected: dict[str, str]) -> Staged:
        """Step 1: the release's files, each checked. Nothing else changes."""
        folder = self.layout.downloads / release.version
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        try:
            paths: dict[str, Path] = {}
            for asset in release.files:
                sha256 = expected.get(asset.name)
                if sha256 is None:
                    raise ChecksumMismatch(f"There's no checksum for {asset.name}.")
                paths[asset.name] = folder / asset.name
                self.checker.source.download(asset, paths[asset.name], sha256=sha256)
            wheel = paths[release.wheel.name]
            images = check_images(paths[release.images.name].read_bytes(), wheel)
        except CheckProblem as problem:
            shutil.rmtree(folder, ignore_errors=True)
            raise UpdateFailed(
                f"Couldn't download DataLab {release.version} ({problem}). Nothing was changed."
            ) from None
        except (ChecksumMismatch, OSError) as error:
            shutil.rmtree(folder, ignore_errors=True)
            raise UpdateFailed(
                f"DataLab {release.version}'s files didn't pass their checks ({error}). "
                "Nothing was changed."
            ) from None
        return Staged(release.version, folder, wheel, paths[release.constraints.name], images)

    def apply(self, staged: Staged) -> None:
        """Steps 2 to 4: back up, install beside this version, pull its images,
        switch the launcher. Undone, apart from the backup, if a step fails."""
        version = staged.version
        data_dir, database = self.settings.data_dir, self.settings.database_file
        self._set("backing-up", version, "Backing up the database…")
        try:
            updates.begin(data_dir, database, from_version=self.current, to_version=version)
        except updates.UpdateError as error:
            raise UpdateFailed(str(error)) from None
        except Exception as error:  # BackupFailed, a full disk
            self._give_up()
            raise UpdateFailed(
                f"DataLab couldn't back up its database ({error}), so it didn't update. "
                "If the disk is full, free up some space and try again."
            ) from None
        pointer = self.layout.pointer()
        created = False
        switched = False
        try:
            self._set("installing", version, f"Installing DataLab {version} beside this one…")
            # A version installed before (and switched away from) is reused, and kept.
            created = not self.layout.complete(version)
            self._install_beside(staged)
            self._set("pulling-images", version, "Downloading DataLab's container images…")
            self._pull_images(version)
            updates.advance(data_dir, "installed")
            self._set("switching", version, "Switching the launcher to the new version…")
            switched = True
            self.layout.switch(version, previous=self.current)
            updates.advance(data_dir, "switched")
        except BaseException as error:
            if switched:
                with contextlib.suppress(OSError):
                    self.layout.restore(pointer)
            if created:
                shutil.rmtree(self.layout.folder(version), ignore_errors=True)
            self._give_up()
            if isinstance(error, UpdateFailed):
                raise
            if isinstance(error, Exception):
                log.exception("installing %s failed", version)
                raise UpdateFailed(
                    f"Installing DataLab {version} failed ({type(error).__name__}). "
                    f"DataLab {self.current} is still the one in use."
                ) from None
            raise

    def restart(self, version: str) -> None:
        """Step 5: hand over to the helper, then quit."""
        command = [
            self._python,
            "-m",
            "datalab.updater",
            "relaunch",
            "--root",
            str(self.layout.root),
            "--data-dir",
            str(self.settings.data_dir),
            "--profile",
            self.settings.profile,
            "--to",
            version,
            "--from",
            self.current,
            "--pid",
            str(os.getpid()),
        ]
        try:
            self._spawn(command)
        except OSError as error:
            log.error("couldn't start the restart helper: %s", error)
            self._set(
                "restarting",
                version,
                f"DataLab {version} is installed, but DataLab couldn't restart itself. "
                "Quit DataLab and open it again to finish the update.",
            )
            return
        if self._shutdown is None or not self._shutdown():
            self._set(
                "restarting",
                version,
                f"DataLab {version} is installed. Quit DataLab and open it again to finish "
                "the update.",
            )

    # ------------------------------------------------------------------------

    def _install_beside(self, staged: Staged) -> None:
        """Install into `versions/<version>`, unless it's there already."""
        version = staged.version
        folder = self.layout.folder(version)
        if folder.resolve() == Path(sys.prefix).resolve():
            raise UpdateFailed("DataLab won't install over the version that's running.")
        if self.layout.complete(version):
            self._check_version(version)
            return
        if folder.exists():  # an earlier attempt that was cut off
            shutil.rmtree(folder)
        uv = self._uv
        assert uv is not None
        self._must(
            [uv, "venv", "--python", "3.13", "--no-config", str(folder)],
            "making its Python environment",
            300,
        )
        self._must(
            [
                uv,
                "pip",
                "install",
                "--no-config",
                "--python",
                str(self.layout.python(version)),
                # By its plain name, from its own folder: uv cuts a --constraints
                # path at its first space ("Application Support", "OneDrive - …").
                "--constraints",
                staged.constraints.name,
                str(staged.wheel),
            ],
            "installing the package",
            900,
            cwd=staged.constraints.parent,
        )
        self._check_version(version)
        (folder / _COMPLETE).write_text(
            json.dumps({"version": version, "installed_at": _now()}) + "\n", encoding="utf-8"
        )

    def _check_version(self, version: str) -> None:
        result = self._must(
            [str(self.layout.executable(version)), "--version"], "checking the new version", 120
        )
        said = result.stdout.strip().removeprefix("datalab ").strip()
        if parse_version(said) != parse_version(version):
            raise UpdateFailed(f"The installed DataLab says it's {said!r}, not {version}.")

    def _pull_images(self, version: str) -> None:
        self._must(
            [
                str(self.layout.executable(version)),
                "--profile",
                self.settings.profile,
                "pull-images",
            ],
            "downloading its container images (is Docker Desktop running?)",
            30 * 60,
        )

    def _must(
        self, command: list[str], what: str, timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(command, timeout, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateFailed(
                f"The update stopped while {what} ({type(error).__name__}). "
                f"DataLab {self.current} is still the one in use."
            ) from None
        if result.returncode != 0:
            log.warning("update step failed (%s): %s", what, (result.stderr or "")[-2000:])
            raise UpdateFailed(
                f"The update stopped while {what}. DataLab {self.current} is still the one in use."
            )
        return result

    def _give_up(self) -> None:
        try:
            updates.abandon(
                self.settings.data_dir,
                self.settings.database_file,
                app_version=self.current,
                known=self._known(),
            )
        except updates.UpdateError as error:
            log.error("couldn't clear the update marker: %s", error)

    def _set(self, state: Step, version: str | None, message: str) -> None:
        now = _now()
        started = now if state == "downloading" else self._progress.started_at or now
        self._progress = Progress(state, version, message, started, now)


def _known_migrations() -> set[str]:
    from datalab import db

    return db.known_migrations()


def _find_uv(platform: str) -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home() / ".local" / "bin"
    candidate = home / ("uv.exe" if platform == "win32" else "uv")
    return str(candidate) if candidate.is_file() else None


def _spawn_detached(command: list[str]) -> None:
    """Start the helper so it outlives this DataLab: its own session (Mac) or
    process group, without a console (Windows)."""
    options: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":  # UNTESTED on Windows
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ restarting


def launch_command(layout: Layout, profile: str, platform: str = sys.platform) -> list[str]:
    """Open DataLab as the launcher does: in a Terminal window on Mac, a
    PowerShell window on Windows, running the version `current` names."""
    shim = str(layout.shim)
    if platform == "darwin":
        env = [
            f"{name}={shlex.quote(value)}"
            for name in ("DATALAB_DATA_DIR", INSTALL_DIR_ENV)
            if (value := os.environ.get(name))
        ]
        shell = " ".join([*env, shlex.quote(shim), "--profile", profile, "serve"])
        script = shell.replace("\\", "\\\\").replace('"', '\\"')
        return [
            "osascript",
            "-e",
            'tell application "Terminal" to activate',
            "-e",
            f'tell application "Terminal" to do script "{script}"',
        ]
    if platform == "win32":  # UNTESTED on Windows
        quoted = shim.replace("'", "''")
        return [
            "powershell.exe",
            "-NoExit",
            "-Command",
            f"& '{quoted}' --profile {profile} serve",
        ]
    raise UpdateFailed("Restarting DataLab works on Mac and Windows.")


def _open(command: list[str], platform: str = sys.platform) -> None:
    options: dict = {"stdin": subprocess.DEVNULL, "close_fds": True}
    if platform == "win32":  # UNTESTED on Windows: a window of its own
        options["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    else:
        options.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(command, **options)


def alive(pid: int) -> bool:
    """Whether a process is still running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":  # UNTESTED on Windows
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class Relaunch:
    """What the restart helper does, with its waits and effects replaceable for tests."""

    layout: Layout
    data_dir: Path
    profile: str
    to_version: str
    from_version: str
    old_pid: int
    alive: Callable[[int], bool] = alive
    owner: Callable[[Path], int | None] = datalock.holder
    launch: Callable[[list[str]], None] = _open
    platform: str = sys.platform
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def run(self) -> str:
        """How it went: "finished", "went-back", "stuck" or "old-still-running"."""
        if not self._wait(lambda: not self.alive(self.old_pid), QUIT_SECONDS):
            self._note("the old DataLab didn't quit; not restarting")
            return "old-still-running"
        self._open()
        if self._new_one_finishes():
            self._note(f"DataLab {self.to_version} started")
            return "finished"
        if self._running_new() is not None:
            # Still starting (a long migration?), or stuck: never interrupted.
            self._note(f"DataLab {self.to_version} is taking a long time to start; left running")
            return "stuck"
        # The new version didn't start, or stopped: go back to the old one,
        # whose startup recovery sorts out the marker.
        self._note(f"DataLab {self.to_version} didn't start; opening {self.from_version} again")
        pointer = self.layout.pointer()
        if pointer[0] != self.from_version and self.layout.complete(self.from_version):
            self.layout.restore((self.from_version, self.to_version))
        self._open()
        return "went-back"

    def _new_one_finishes(self) -> bool:
        deadline = self.clock() + START_SECONDS
        took = self._wait(lambda: self._running_new() is not None or self._finished(), TAKE_SECONDS)
        if not took:
            return self._finished()
        while self.clock() < deadline:
            if self._finished():
                return True
            if self._running_new() is None:
                # Stopped before finishing, or finished and quit very fast.
                return self._finished()
            self.sleep(1)
        return self._finished()

    def _finished(self) -> bool:
        try:
            marker = updates.read_marker(self.data_dir)
        except updates.UnreadableMarker:
            return False
        return marker is None or not updates.same_version(marker.to_version, self.to_version)

    def _running_new(self) -> int | None:
        pid = self.owner(self.data_dir)
        if pid is not None and pid != self.old_pid and self.alive(pid):
            return pid
        return None

    def _open(self) -> None:
        try:
            self.launch(launch_command(self.layout, self.profile, self.platform))
        except (OSError, UpdateFailed) as error:
            self._note(f"couldn't open DataLab: {type(error).__name__}")

    def _wait(self, done: Callable[[], bool], seconds: float) -> bool:
        deadline = self.clock() + seconds
        while not done():
            if self.clock() >= deadline:
                return False
            self.sleep(0.5)
        return True

    def _note(self, text: str) -> None:
        # Versions and times only, like logs/updates.jsonl.
        with contextlib.suppress(OSError):
            path = self.data_dir / "logs" / "updater.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(f"{_now()} {text}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m datalab.updater")
    commands = parser.add_subparsers(dest="command", required=True)
    relaunch = commands.add_parser("relaunch", help="(the updater's restart helper)")
    relaunch.add_argument("--root", type=Path, required=True)
    relaunch.add_argument("--data-dir", type=Path, required=True)
    relaunch.add_argument("--profile", choices=["real", "practice"], required=True)
    relaunch.add_argument("--to", required=True)
    relaunch.add_argument("--from", dest="from_version", required=True)
    relaunch.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    outcome = Relaunch(
        Layout(args.root),
        args.data_dir,
        args.profile,
        args.to,
        args.from_version,
        args.pid,
    ).run()
    return 0 if outcome == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
