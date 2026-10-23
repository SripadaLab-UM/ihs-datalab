"""Installing a DataLab release on the lab's server, and going back to the one
before (`datalab server install` / `rollback` / `status`; docs/DISTRIBUTION.md,
"The lab's shared DataLab").

The server keeps each release in its own folder and runs the one `current`
points at (the systemd unit's `ExecStart` is `<root>/current/venv/bin/datalab`):

```
<root>/releases/<id>/    the release's package, requirements.txt, images.json,
                         its Python environment (venv/), and .complete
<root>/current           symlink: the release the service runs
<root>/previous          symlink: the one it ran before
```

An install, run as the service's own user from the release that's running:

1. **Check the release's files**: a signed release from GitHub (`--version`;
   SHA256SUMS signed with the lab's release key, as the desktop updater
   checks) or a folder built with scripts/build-release.sh (`--from`; the
   operator's own build). `requirements.txt` must pin everything by hash and
   the package by its checksum; `images.json`, when there is one, every image
   by digest.
2. **Install beside the running release** and pull its images. The service
   keeps running meanwhile.
3. **Wait for work to finish** (a query, a workflow run, pipeline tests),
   then **stop the service**, back up the database (the update marker,
   updates.py, so the new release's start-up sees an update), and switch
   `current`, keeping the old one as `previous`.
4. **Start the service** and wait until it answers on its socket as the new
   version. If it doesn't, `current` goes back and the old release starts;
   its start-up recovery puts the database back if the new one changed it.

`rollback` stops the service, has the previous release restore the
database backup if the newer one changed the database (`datalab rollback`,
which refuses to drop what was recorded since unless told `--yes`), swaps
`current` and `previous`, and starts it again.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from datalab import __version__, release_keys, signing, updates
from datalab.config import Settings
from datalab.releases import (
    MAX_SIGNATURE_BYTES,
    MAX_SUMS_BYTES,
    CheckProblem,
    ChecksumMismatch,
    ReleaseSource,
    check_listing,
    parse_sums,
    parse_version,
    releases_from,
)
from datalab.updater import (
    PYPI,
    busy_reason,
    check_images,
    check_requirements,
    clean_environment,
)

_COMPLETE = ".complete"
WAIT_SECONDS = 30 * 60  # for running work to finish
START_SECONDS = 120  # for the new release to answer


class InstallFailed(RuntimeError):
    """A step failed; the message says what is running now."""


class Run(Protocol):
    def __call__(
        self, command: Sequence[str], timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]: ...


def _runner(root: Path) -> Run:
    """Commands without uv or pip settings from the environment; uv keeps the
    Python it installs in the server's `tools/python` (as deploy did)."""
    env = clean_environment()
    env["UV_PYTHON_INSTALL_DIR"] = str(root / "tools" / "python")

    def run(
        command: Sequence[str], timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            cwd=cwd,
        )

    return run


@dataclass(frozen=True)
class Staged:
    release_id: str
    version: str
    folder: Path
    wheel: Path
    wheel_sha256: str


class ServerLayout:
    """`<root>/releases/<id>/`, `current` and `previous`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def of_this_process(cls) -> ServerLayout:
        """The layout this DataLab runs from: `<root>/releases/<id>/venv`."""
        prefix = Path(sys.prefix).resolve()
        if prefix.name != "venv" or prefix.parent.parent.name != "releases":
            raise InstallFailed(
                "Run this from the server's installed DataLab "
                "(<root>/current/venv/bin/datalab), not a development copy."
            )
        return cls(prefix.parent.parent.parent)

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    def folder(self, release_id: str) -> Path:
        return self.releases / release_id

    def executable(self, release_id: str) -> Path:
        return self.folder(release_id) / "venv" / "bin" / "datalab"

    def python(self, release_id: str) -> Path:
        return self.folder(release_id) / "venv" / "bin" / "python"

    def complete(self, release_id: str) -> dict | None:
        try:
            record = json.loads((self.folder(release_id) / _COMPLETE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def pointer(self, name: str) -> str | None:
        """The release `current` or `previous` points at."""
        link = self.root / name
        if not link.is_symlink():
            return None
        target = Path(os.readlink(link))
        return target.name if target.parent.name == "releases" else None

    def point(self, name: str, release_id: str | None) -> None:
        """Point `current` or `previous` at a release, replacing the link whole."""
        link = self.root / name
        if release_id is None:
            link.unlink(missing_ok=True)
            return
        temporary = self.root / f".{name}.new"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(self.folder(release_id))
        temporary.replace(link)

    def known_migrations(self, release_id: str) -> set[str]:
        """The database migrations a release's package has."""
        found = self.folder(release_id).glob(
            "venv/lib/python*/site-packages/datalab/db/migrations/*.sql"
        )
        return {p.name for p in found}


class ServerInstaller:
    def __init__(
        self,
        settings: Settings,
        layout: ServerLayout,
        *,
        uv: str | None = None,
        run: Run | None = None,
        say: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        health: Callable[[], str | None] | None = None,
        source: ReleaseSource | None = None,
        keys: Sequence[str] | None = None,
    ) -> None:
        if settings.server is None:
            raise InstallFailed('This is for the lab\'s server (deployment = "server").')
        self.settings = settings
        self.server = settings.server
        self.layout = layout
        self._uv = uv or _find_uv(layout.root)
        self._run = run or _runner(layout.root)
        self._say = say
        self._sleep = sleep
        self._health = health or self._version_on_socket
        self._source = source
        self._keys = tuple(release_keys.trusted_keys() if keys is None else keys)

    # ------------------------------------------------------------ staging

    def stage_folder(self, folder: Path) -> Staged:
        """A release built with scripts/build-release.sh: its package and
        requirements.txt (and images.json, when CI made one)."""
        wheels = sorted(folder.glob("datalab-*-py3-none-any.whl"))
        if len(wheels) != 1:
            raise InstallFailed(
                f"{folder} should hold one DataLab package; it holds {len(wheels)}."
            )
        wheel = wheels[0]
        wheel_sha256 = _sha256(wheel)
        version = wheel.name.split("-")[1]
        release_id = f"{version}-{wheel_sha256[:8]}"
        target = self.layout.folder(release_id)
        if self.layout.complete(release_id) is None:
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            for name in (wheel.name, "requirements.txt", "images.json"):
                if (folder / name).is_file():
                    shutil.copyfile(folder / name, target / name)
        return self._checked(release_id, version, target, wheel.name, wheel_sha256)

    def stage_release(self, version: str) -> Staged:
        """A release from GitHub, signed with the lab's release key."""
        wanted = parse_version(version)
        if wanted is None:
            raise InstallFailed(f"{version!r} isn't a version.")
        if not self._keys:
            raise InstallFailed("This DataLab trusts no release key, so it installs no release.")
        source = self._source or ReleaseSource(self.settings.updates.repository)
        try:
            releases, _ = releases_from(source.list(), source.repository)
            release = next((r for r in releases if parse_version(r.version) == wanted), None)
            if release is None:
                raise InstallFailed(f"There's no release {version} of {source.repository}.")
            sums = source.read_small(release.sums, MAX_SUMS_BYTES)
            signature = source.read_small(release.signature, MAX_SIGNATURE_BYTES)
            if not signing.verify(sums, signature, self._keys):
                raise InstallFailed(
                    f"{release.tag}'s SHA256SUMS isn't signed by the lab's release key."
                )
            expected = check_listing(release, parse_sums(sums.decode("utf-8", "replace")))
            wheel_sha256 = expected[release.wheel.name]
            release_id = f"{release.version}-{wheel_sha256[:8]}"
            target = self.layout.folder(release_id)
            if self.layout.complete(release_id) is None:
                shutil.rmtree(target, ignore_errors=True)
                target.mkdir(parents=True)
                for asset in release.files:
                    source.download(asset, target / asset.name, sha256=expected[asset.name])
        except CheckProblem as problem:
            raise InstallFailed(f"Couldn't reach GitHub's releases ({problem}).") from None
        except ChecksumMismatch as error:
            raise _failed_checks(error) from None
        return self._checked(release_id, release.version, target, release.wheel.name, wheel_sha256)

    def _checked(
        self, release_id: str, version: str, folder: Path, wheel: str, wheel_sha256: str
    ) -> Staged:
        try:
            requirements = (folder / "requirements.txt").read_text(encoding="utf-8")
            check_requirements(requirements, wheel, wheel_sha256)
            if _sha256(folder / wheel) != wheel_sha256:
                raise ChecksumMismatch("The package isn't the one its checksum names.")
            if (folder / "images.json").is_file():
                check_images((folder / "images.json").read_bytes(), folder / wheel)
        except OSError as error:
            raise InstallFailed(f"The release's files can't be read ({error}).") from None
        except ChecksumMismatch as error:
            raise _failed_checks(error) from None
        return Staged(release_id, version, folder, folder / wheel, wheel_sha256)

    # ------------------------------------------------------------ install

    def install(self, staged: Staged, *, wait: float = WAIT_SECONDS) -> None:
        running = self.layout.pointer("current")
        if running == staged.release_id:
            self._say(f"{staged.release_id} is already the release that runs.")
            return
        self._prepare(staged)
        self._wait_for_work(wait)
        self._say(f"Stopping {self.server.service}…")
        self._systemctl("stop")
        try:
            updates.begin(
                self.settings.data_dir,
                self.settings.database_file,
                from_version=__version__,
                to_version=staged.version,
            )
        except Exception as error:
            self._systemctl("start")
            raise InstallFailed(
                f"Couldn't back up the database ({error}); {running} is running again."
            ) from None
        updates.advance(self.settings.data_dir, "installed")
        self.layout.point("previous", running)
        self.layout.point("current", staged.release_id)
        updates.advance(self.settings.data_dir, "switched")
        self._say(f"Starting {staged.release_id}…")
        self._systemctl("start")
        if self._answers_as(staged.version):
            self._say(f"DataLab {staged.version} ({staged.release_id}) is running.")
            return
        # Back to the old release; its start-up recovery sorts out the database.
        self._systemctl("stop")
        self.layout.point("current", running)
        self.layout.point("previous", staged.release_id)
        self._systemctl("start")
        raise InstallFailed(
            f"{staged.release_id} didn't start (see `journalctl -u {self.server.service}`), "
            f"so {running} is running again."
        )

    def _prepare(self, staged: Staged) -> None:
        """Its Python environment and images, beside the running release."""
        record = self.layout.complete(staged.release_id)
        if record is not None and record.get("wheel_sha256") == staged.wheel_sha256:
            self._say(f"{staged.release_id} is installed already.")
        else:
            if not self._uv:
                raise InstallFailed("uv, which installs DataLab, wasn't found.")
            venv = staged.folder / "venv"
            shutil.rmtree(venv, ignore_errors=True)
            self._say(f"Installing {staged.release_id}…")
            self._must([self._uv, "venv", "--no-config", "--python", "3.13", str(venv)], 300)
            self._must(
                [
                    self._uv,
                    "pip",
                    "install",
                    "--no-config",
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    "--default-index",
                    PYPI,
                    "--python",
                    str(venv / "bin" / "python"),
                    # From its own folder: it names the package as ./<wheel>.
                    "-r",
                    "requirements.txt",
                ],
                15 * 60,
                cwd=staged.folder,
            )
            installed = self._must(
                [str(self.layout.executable(staged.release_id)), "--version"], 60
            ).stdout.strip()
            if installed.split()[-1:] != [staged.version]:
                raise InstallFailed(
                    f"The installed package says {installed!r}, not {staged.version}."
                )
        self._say("Pulling its container images…")
        self._must(
            [
                str(self.layout.executable(staged.release_id)),
                "--profile",
                self.settings.profile,
                "pull-images",
            ],
            30 * 60,
        )
        (staged.folder / _COMPLETE).write_text(
            json.dumps({"wheel_sha256": staged.wheel_sha256, "version": staged.version}),
            encoding="utf-8",
        )

    def _wait_for_work(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        said = None
        while True:
            reason = self._busy()
            if reason is None:
                return
            if reason != said:
                self._say(f"Waiting: {reason}")
                said = reason
            if time.monotonic() >= deadline:
                raise InstallFailed(f"Gave up waiting ({reason}). Nothing was changed.")
            self._sleep(10)

    def _busy(self) -> str | None:
        database = self.settings.database_file
        if not database.exists():
            return None
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            return busy_reason(connection, lambda: False)
        finally:
            connection.close()

    # ------------------------------------------------------------ rollback

    def rollback(self, *, drop_since: bool = False) -> None:
        running, previous = self.layout.pointer("current"), self.layout.pointer("previous")
        if previous is None or self.layout.complete(previous) is None:
            raise InstallFailed("There's no previous release to go back to.")
        self._wait_for_work(WAIT_SECONDS)
        self._say(f"Stopping {self.server.service}…")
        self._systemctl("stop")
        if self._database_is_newer_than(previous):
            command = [
                str(self.layout.executable(previous)),
                "--profile",
                self.settings.profile,
                "rollback",
            ]
            result = self._run([*command, "--yes"] if drop_since else command, 5 * 60)
            self._say(result.stdout.strip())
            if result.returncode != 0:
                self._systemctl("start")
                raise InstallFailed(f"The database wasn't rolled back; {running} is running again.")
        self.layout.point("current", previous)
        self.layout.point("previous", running)
        self._systemctl("start")
        version = (self.layout.complete(previous) or {}).get("version")
        if version and not self._answers_as(version):
            raise InstallFailed(
                f"{previous} didn't answer after the rollback; see "
                f"`journalctl -u {self.server.service}`."
            )
        self._say(f"{previous} is running again.")

    def _database_is_newer_than(self, release_id: str) -> bool:
        database = self.settings.database_file
        if not database.exists():
            return False
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            applied = {r[0] for r in connection.execute("SELECT name FROM schema_migrations")}
        finally:
            connection.close()
        return bool(applied - self.layout.known_migrations(release_id))

    # ------------------------------------------------------------ status

    def status(self) -> list[str]:
        lines = []
        for name in ("current", "previous"):
            release_id = self.layout.pointer(name)
            record = self.layout.complete(release_id) if release_id else None
            version = (record or {}).get("version", "?") if release_id else ""
            lines.append(f"{name:<9} {release_id or '-'}  {version}")
        answering = self._health()
        lines.append(f"answering {answering or 'no'}")
        return lines

    # ------------------------------------------------------------ helpers

    def _answers_as(self, version: str) -> bool:
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if self._health() == version:
                return True
            self._sleep(2)
        return False

    def _version_on_socket(self) -> str | None:
        try:
            transport = httpx.HTTPTransport(uds=self.server.socket)
            with httpx.Client(transport=transport, timeout=5) as client:
                response = client.get("http://localhost/api/health")
                return str(response.json().get("version")) if response.is_success else None
        except (httpx.HTTPError, ValueError, OSError):
            return None

    def _systemctl(self, action: str) -> None:
        command = ["systemctl", action, self.server.service]
        if os.geteuid() != 0:
            command = ["sudo", "-n", *command]
        result = self._run(command, 180)
        if result.returncode != 0:
            raise InstallFailed(
                f"`{' '.join(command)}` failed: {(result.stderr or '').strip()[-300:]}"
            )

    def _must(
        self, command: list[str], timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(command, timeout, cwd)
        except (OSError, subprocess.SubprocessError) as error:
            name = Path(command[0]).name
            raise InstallFailed(f"{name} failed ({type(error).__name__}).") from None
        if result.returncode != 0:
            raise InstallFailed(
                f"{' '.join(command[:3])}… failed: {(result.stderr or '').strip()[-600:]}"
            )
        return result


def _failed_checks(error: Exception) -> InstallFailed:
    return InstallFailed(f"The release's files didn't pass their checks ({error}).")


def _find_uv(root: Path) -> str | None:
    pinned = root / "tools" / "uv-venv" / "bin" / "uv"
    if pinned.is_file():
        return str(pinned)
    return shutil.which("uv")


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
