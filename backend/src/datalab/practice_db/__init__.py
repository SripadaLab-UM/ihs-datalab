"""Practice DataLab's synthetic database: Oracle Database Free in a Docker
container on this computer, holding only made-up data (synthetic/README.md).

DataLab looks after it itself, so nobody runs a script or a shell command:

- **Set up once.** The installer (`datalab --profile practice practice-db
  setup`) downloads the pinned image, creates the container and loads the
  made-up data. The container has a fixed name and keeps its data in a named
  Docker volume, both labelled as practice DataLab's, so reinstalling or
  updating DataLab finds them again and never loads the data a second time.
- **Started when needed.** Practice DataLab starts it when it opens, if it
  isn't running (`PracticeDatabaseKeeper`, in the background), and loads the
  data only if the database has none: no marker table (guard.py), which the
  generator creates last, so a load that stopped part way is done again.
- **Reset only when asked:** `datalab --profile practice practice-db reset`,
  or Reset in Settings → Connections (confirmed). It removes the container
  and its volume and sets them up from scratch.
- **This computer only.** The port is published on 127.0.0.1 and nowhere
  else; a container of this name published anywhere else is refused.

A container or volume of this name without DataLab's label isn't DataLab's:
it's never started, reset or removed. When something else already answers
on the port and is the synthetic database (synthetic/db.sh's development
container, or CI's), practice DataLab uses it as it is, and never loads
data into it or resets it. The real profile never gets here.

The passwords are the synthetic database's fixed, public, dev-only ones.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from datalab.config import PRACTICE_ORACLE
from datalab.practice_db.guard import ADMIN_PWD

log = logging.getLogger(__name__)

# Oracle's own image, pinned by digest (the multi-platform index of
# database/free:latest-lite, for Intel and Apple silicon). DataLab downloads
# it from Oracle's registry; it's never redistributed.
IMAGE = (
    "container-registry.oracle.com/database/free"
    "@sha256:cf540c3fa190d7cffad08c491652ac07fc70314e510f6b87890449517d565e94"
)
IMAGE_SIZE = "about 2.5 GB"
CONTAINER = "datalab-practice-oracle"
VOLUME = "datalab-practice-oracle-data"
LABEL = "datalab.practice-db"
# Where Oracle Free keeps its database files: the volume.
DATA_PATH = "/opt/oracle/oradata"
SERVICE = "FREEPDB1"

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")

Phase = Literal[
    "checking", "downloading", "creating", "starting", "loading", "resetting", "ready", "problem"
]


class PracticeDatabaseProblem(RuntimeError):
    """Why the practice database can't be used now, in words safe to show."""


class DockerUnavailable(PracticeDatabaseProblem):
    """Docker isn't installed, isn't running, or didn't answer."""


@dataclass(frozen=True)
class Target:
    """Which container, volume and port: the fixed ones, or (for tests and
    development) DATALAB_PRACTICE_DB_CONTAINER, _VOLUME and _PORT."""

    container: str = CONTAINER
    volume: str = VOLUME
    port: int = 1522
    image: str = IMAGE

    def __post_init__(self) -> None:
        for what, name in (("container", self.container), ("volume", self.volume)):
            if not _NAME.fullmatch(name):
                raise ValueError(f"The practice database's {what} name {name!r} isn't usable.")

    @property
    def dsn(self) -> str:
        return f"127.0.0.1:{self.port}/{SERVICE}"

    @property
    def publish(self) -> str:
        # 127.0.0.1 only: the passwords are public, so no other computer may reach it.
        return f"127.0.0.1:{self.port}:1521"


def target_from_env() -> Target:
    return Target(
        container=os.environ.get("DATALAB_PRACTICE_DB_CONTAINER") or CONTAINER,
        volume=os.environ.get("DATALAB_PRACTICE_DB_VOLUME") or VOLUME,
        port=PRACTICE_ORACLE.port,
    )


@dataclass(frozen=True)
class Container:
    status: str  # running, exited, created, paused, restarting, dead
    health: str | None  # starting, healthy, unhealthy; None: no health check
    ours: bool  # carries DataLab's label
    # Where the database's port is published: (host address, host port).
    published: tuple[tuple[str, str], ...] = ()

    @property
    def local_only(self) -> bool:
        return bool(self.published) and all(ip == "127.0.0.1" for ip, _ in self.published)


# ------------------------------------------------------------------ docker

Docker = Callable[..., "tuple[int, str, str]"]


def run_docker(args: list[str], *, timeout: float | None = 60, show: bool = False):
    """`docker <args>`: (exit code, output, errors). With `show`, its output
    goes to the terminal (a download's progress) and only errors are kept."""
    if shutil.which("docker") is None:
        raise DockerUnavailable(
            "Docker isn't installed, and the practice database runs in Docker. Install Docker "
            "Desktop, then try again."
        )
    try:
        done = subprocess.run(
            ["docker", *args],
            stdout=None if show else subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise DockerUnavailable(
            "Docker didn't answer. If Docker Desktop is starting, wait until it says "
            "'Engine running', then try again."
        ) from None
    except OSError as error:
        raise DockerUnavailable(f"Docker couldn't be run ({type(error).__name__}).") from None
    return done.returncode, done.stdout or "", done.stderr or ""


def _engine_down(error: str) -> bool:
    lowered = error.lower()
    return (
        "cannot connect to the docker daemon" in lowered
        or "is the docker daemon running" in lowered
        or "error during connect" in lowered
        or "docker desktop is not running" in lowered
        or ("pipe" in lowered and "docker_engine" in lowered)
    )


_DOCKER_DOWN = (
    "Docker Desktop isn't running, and the practice database runs in it. Start Docker "
    "Desktop and wait until it says 'Engine running'; DataLab then carries on by itself."
)

# A registry that's busy or a network that dropped: worth trying again.
_TRANSIENT = re.compile(
    r"\b50[0234]\b|service unavailable|bad gateway|gateway time-?out|too ?many ?requests|\b429\b"
    r"|tls handshake timeout|i/o timeout|connection reset|unexpected eof|timeout exceeded"
    r"|context deadline exceeded|temporary failure",
    re.IGNORECASE,
)
PULL_RETRY_DELAYS = (5.0, 15.0, 30.0, 60.0, 120.0)


def registry_busy_message(error: str) -> str:
    code = re.search(r"\b(50[0234]|429)\b", error)
    why = f" (HTTP {code.group(1)})" if code else ""
    return (
        "Oracle's container registry (container-registry.oracle.com) is busy or didn't "
        f"answer{why}, so the practice database's image couldn't be downloaded. That's on "
        "Oracle's side, not yours: try again in a few minutes."
    )


# --------------------------------------------------------------- database


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def database_has_data(dsn: str, *, wait: float = 180) -> bool:
    """Whether the synthetic database at `dsn` has its data: the marker the
    generator creates last. Asked as SYSTEM, only of Oracle Database Free on
    this computer (guard.py)."""
    from datalab.practice_db.generate import connect_when_ready
    from datalab.practice_db.guard import (
        MARKER_SCHEMA,
        require_local_dsn,
        require_synthetic_server,
    )

    require_local_dsn(dsn)
    with connect_when_ready(dsn, ADMIN_PWD, timeout=wait) as connection:
        cursor = connection.cursor()
        require_synthetic_server(cursor)
        cursor.execute(
            "SELECT COUNT(*) FROM dba_tables WHERE owner = :o AND table_name = 'MARKER'",
            o=MARKER_SCHEMA,
        )
        (tables,) = cursor.fetchone() or (0,)
        if not tables:
            return False
        cursor.execute(f"SELECT COUNT(*) FROM {MARKER_SCHEMA}.MARKER")
        (rows,) = cursor.fetchone() or (0,)
        return bool(rows)


def load_data(dsn: str, say: Callable[[str], None]) -> None:
    from datalab.practice_db.generate import build

    build(dsn, say=say)


# ---------------------------------------------------------------- lifecycle


def _quiet(_: str) -> None:
    return None


def _no_phase(_: Phase) -> None:
    return None


@dataclass
class PracticeDatabase:
    """The container's lifecycle, through the `docker` command."""

    target: Target = field(default_factory=target_from_env)
    docker: Docker = run_docker
    port_open: Callable[[int], bool] = _port_open
    has_data: Callable[..., bool] = database_has_data
    generate: Callable[[str, Callable[[str], None]], None] = load_data
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    # Development only (synthetic/db.sh): also look after a container or
    # volume of this name that isn't labelled as DataLab's.
    manage_unlabelled: bool = False
    ready_timeout: float = 15 * 60

    # What's there ------------------------------------------------------

    def inspect(self) -> Container | None:
        code, out, error = self._docker("container", "inspect", self.target.container)
        if code != 0:
            if "no such" in error.lower():
                return None
            if _engine_down(error):
                raise DockerUnavailable(_DOCKER_DOWN)
            raise PracticeDatabaseProblem(
                f"Docker couldn't look at the practice database: {_first(error)}"
            )
        try:
            data = json.loads(out)[0]
        except (ValueError, IndexError, KeyError):
            raise PracticeDatabaseProblem(
                "Docker's answer about the practice database was unreadable."
            ) from None
        state = data.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        labels = (data.get("Config") or {}).get("Labels") or {}
        bindings = ((data.get("HostConfig") or {}).get("PortBindings") or {}).get("1521/tcp") or []
        return Container(
            status=state.get("Status", "unknown"),
            health=health,
            ours=LABEL in labels,
            published=tuple((b.get("HostIp", ""), b.get("HostPort", "")) for b in bindings),
        )

    def volume_state(self) -> Literal["missing", "ours", "other"]:
        code, out, error = self._docker(
            "volume", "inspect", "--format", "{{json .Labels}}", self.target.volume
        )
        if code != 0:
            if "no such" in error.lower():
                return "missing"
            if _engine_down(error):
                raise DockerUnavailable(_DOCKER_DOWN)
            raise PracticeDatabaseProblem(
                f"Docker couldn't look at the practice database's volume: {_first(error)}"
            )
        try:
            labels = json.loads(out.strip() or "null") or {}
        except ValueError:
            labels = {}
        return "ours" if LABEL in labels else "other"

    def describe(self) -> str:
        """One line for `practice-db status`."""
        container = self.inspect()
        if container is None:
            if self.port_open(self.target.port):
                return (
                    f"No practice database container ({self.target.container}); something else "
                    f"answers on 127.0.0.1:{self.target.port}."
                )
            return f"Not set up yet (no container {self.target.container})."
        health = f", {container.health}" if container.health else ""
        whose = "" if container.ours else " (not labelled as DataLab's)"
        return (
            f"Container {self.target.container}{whose}: {container.status}{health}, on "
            f"127.0.0.1:{self.target.port}; its data is in the volume {self.target.volume}."
        )

    # Changes -------------------------------------------------------------

    def ensure(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show_download: bool = False,
    ) -> str:
        """Make the practice database ready: create or start the container if
        needed, and load the data only if it has none. What happened, in words."""
        if not self.up(say, phase, show_download=show_download):
            return self._use_other_database()
        if self.has_data(self.target.dsn):
            phase("ready")
            return "The practice database is running, with its made-up data."
        phase("loading")
        say("Loading the made-up data (a minute or two)…")
        self.generate(self.target.dsn, say)
        phase("ready")
        return "The practice database is set up, with its made-up data."

    def up(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show_download: bool = False,
    ) -> bool:
        """Create or start the container and wait until the database is open.
        False: there's no container, and something else answers on the port
        (it's left alone)."""
        phase("checking")
        container = self.inspect()
        created = False
        if container is None:
            if self.port_open(self.target.port):
                return False
            self.pull(say, phase, show=show_download)
            phase("creating")
            say("Creating the practice database's container (on this computer only)…")
            # A volume kept from before already holds a database: no first start.
            created = self._create()
        else:
            self._require_manageable(container)
            if container.status != "running":
                phase("starting")
                say("Starting the practice database…")
                self._start()
        phase("starting")
        self._wait_until_healthy(say, first=created)
        return True

    def pull(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show: bool = False,
    ) -> bool:
        """Download the pinned image unless it's here. Whether it downloaded."""
        if self._docker("image", "inspect", "--format", "{{.Id}}", self.target.image)[0] == 0:
            return False
        phase("downloading")
        say(f"Downloading Oracle Database Free ({IMAGE_SIZE}), for the practice database…")
        delays = iter(PULL_RETRY_DELAYS)
        while True:
            code, _, error = self._docker("pull", self.target.image, timeout=None, show=show)
            if code == 0:
                return True
            if _engine_down(error):
                raise DockerUnavailable(_DOCKER_DOWN)
            delay = next(delays, None)
            if not _TRANSIENT.search(error):
                raise PracticeDatabaseProblem(
                    f"The practice database's image couldn't be downloaded: {_first(error)}"
                )
            if delay is None:
                raise PracticeDatabaseProblem(registry_busy_message(error))
            say(
                f"Oracle's registry didn't answer ({_first(error)[:80]}); "
                f"trying again in {delay:.0f} s…"
            )
            self.sleep(delay)

    def stop(self) -> str:
        container = self.inspect()
        if container is None:
            return "There's no practice database container to stop."
        self._require_manageable(container, publish=False)
        if container.status == "running":
            self._check(
                self._docker("stop", "--time", "60", self.target.container, timeout=120), "stop it"
            )
        return "Stopped the practice database. Its data is kept."

    def reset(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show_download: bool = False,
    ) -> str:
        """Remove the container and its volume, then set them up from scratch."""
        container = self.inspect()
        if container is None and self.port_open(self.target.port):
            raise PracticeDatabaseProblem(
                f"The synthetic database on port {self.target.port} isn't one DataLab set up, so "
                "DataLab won't reset it. Reset it where it came from (for synthetic/db.sh: "
                "synthetic/db.sh generate)."
            )
        if container is not None:
            self._require_manageable(container, publish=False)
        self._require_volume_manageable()
        phase("resetting")
        say("Removing the practice database and its data…")
        self.remove(volume=True)
        return self.ensure(say, phase, show_download=show_download)

    def remove(self, *, volume: bool) -> list[str]:
        """Remove the container (and its volume), if they're DataLab's. What went."""
        removed = []
        container = self.inspect()
        if container is not None and (container.ours or self.manage_unlabelled):
            self._check(self._docker("rm", "-f", self.target.container, timeout=120), "remove it")
            removed.append(f"the container {self.target.container}")
        state = self.volume_state() if volume else "missing"
        if state == "ours" or (state == "other" and self.manage_unlabelled):
            self._check(
                self._docker("volume", "rm", self.target.volume, timeout=120), "remove its data"
            )
            removed.append(f"the volume {self.target.volume}")
        return removed

    # Parts -----------------------------------------------------------

    def _use_other_database(self) -> str:
        """Something already answers on the port: fine if it's the synthetic
        database with its data (a development container), else a problem."""
        try:
            ready = self.has_data(self.target.dsn, wait=5)
        except Exception as error:  # not Oracle Free, a login refused, not a database at all
            log.info(
                "port %s answered, but not as the synthetic database: %s",
                self.target.port,
                type(error).__name__,
            )
            ready = False
        if not ready:
            raise PracticeDatabaseProblem(
                f"Something else on this computer is using port {self.target.port}, so the "
                "practice database can't start there. Quit whatever uses it (another synthetic "
                "database, or an SSH tunnel), then try again."
            )
        return (
            f"Using the synthetic database already running on port {self.target.port} (one "
            "DataLab didn't set up, so it's left as it is)."
        )

    def _require_manageable(self, container: Container, *, publish: bool = True) -> None:
        if not container.ours and not self.manage_unlabelled:
            raise PracticeDatabaseProblem(
                f"A Docker container called {self.target.container} exists, but it isn't one "
                "DataLab made, so DataLab won't use or change it. Rename or remove it yourself, "
                "then try again."
            )
        if publish and not container.local_only:
            if container.status == "running":
                self._docker("stop", "--time", "60", self.target.container, timeout=120)
            raise PracticeDatabaseProblem(
                f"The container {self.target.container} makes the practice database reachable "
                "from other computers (not only 127.0.0.1), so DataLab stopped it. Reset the "
                "practice database to set it up again, safely."
            )

    def _require_volume_manageable(self) -> None:
        if self.volume_state() == "other" and not self.manage_unlabelled:
            raise PracticeDatabaseProblem(
                f"A Docker volume called {self.target.volume} exists, but it isn't one DataLab "
                "made, so DataLab won't use or change it."
            )

    def _create(self) -> bool:
        """Create the container (and its volume, if there's none). Whether
        the volume is new."""
        self._require_volume_manageable()
        new_volume = self.volume_state() == "missing"
        if new_volume:
            self._check(
                self._docker("volume", "create", "--label", f"{LABEL}=1", self.target.volume),
                "create its volume",
            )
        self._check(
            self._docker(
                "run",
                "--detach",
                "--name",
                self.target.container,
                "--label",
                f"{LABEL}=1",
                "--publish",
                self.target.publish,
                "--volume",
                f"{self.target.volume}:{DATA_PATH}",
                "--env",
                f"ORACLE_PWD={ADMIN_PWD}",
                self.target.image,
                timeout=300,
            ),
            "create it",
        )
        return new_volume

    def _start(self) -> None:
        self._check(self._docker("start", self.target.container, timeout=300), "start it")

    def _wait_until_healthy(self, say: Callable[[str], None], *, first: bool) -> None:
        """Until the image's health check says the database is open."""
        deadline = self.clock() + self.ready_timeout
        said = self.clock()
        if first:
            say("The first start takes a minute or two…")
        while True:
            container = self.inspect()
            if container is None or container.status not in ("running", "created", "restarting"):
                state = container.status if container else "gone"
                raise PracticeDatabaseProblem(
                    f"The practice database stopped while starting (it's {state}). Try again; "
                    "if it happens again, reset the practice database."
                )
            if container.health == "healthy" or (
                container.health is None and container.status == "running"
            ):
                return
            if self.clock() > deadline:
                raise PracticeDatabaseProblem(
                    "The practice database didn't finish starting. Try again; if it happens "
                    "again, restart Docker Desktop."
                )
            if self.clock() - said > 30:
                say("Still starting the practice database…")
                said = self.clock()
            self.sleep(3)

    def _docker(self, *args: str, timeout: float | None = 60, show: bool = False):
        return self.docker(list(args), timeout=timeout, show=show)

    def _check(self, result: tuple[int, str, str], what: str) -> None:
        code, _, error = result
        if code == 0:
            return
        if _engine_down(error):
            raise DockerUnavailable(_DOCKER_DOWN)
        if "port is already allocated" in error or "address already in use" in error.lower():
            raise PracticeDatabaseProblem(
                f"Something else on this computer is using port {self.target.port}, so the "
                "practice database can't start there. Quit whatever uses it (another synthetic "
                "database, or an SSH tunnel), then try again."
            )
        raise PracticeDatabaseProblem(f"Docker couldn't {what}: {_first(error)}")


def _first(error: str) -> str:
    """The first line of Docker's message, shortened."""
    line = next((line.strip() for line in error.splitlines() if line.strip()), "no message")
    return line[:200]


# -------------------------------------------------------- for a running app


class PracticeDatabaseKeeper:
    """Practice DataLab's database while DataLab runs: set up or started in
    the background when it opens (the page shows how it's going), set up again
    from scratch when the person asks (Reset). Never for the real profile."""

    DOCKER_RETRY = 10.0
    DOCKER_PATIENCE = 10 * 60.0

    def __init__(
        self,
        database: PracticeDatabase | None = None,
        *,
        on_ready: Callable[[], None] = lambda: None,
    ) -> None:
        self.database = database or PracticeDatabase()
        # Called once it's ready (the app connects again and builds its catalog).
        self.on_ready = on_ready
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.phase: Phase = "checking"
        self.message = "Checking the practice database…"
        self.adopted = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Make it ready in the background, unless that's under way. Whether it began."""
        return self._begin(self.database.ensure, "checking", "Checking the practice database…")

    def reset(self) -> bool:
        return self._begin(
            self.database.reset, "resetting", "Removing the practice database and its data…"
        )

    def _begin(self, action: Callable[..., str], phase: Phase, message: str) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            # Said at once, so the page that asked shows it straight away.
            self._set(phase, message)
            self._thread = threading.Thread(
                target=self._run, args=(action,), name="practice-database", daemon=True
            )
            self._thread.start()
            return True

    def _set(self, phase: Phase, message: str | None = None) -> None:
        self.phase = phase
        if message is not None:
            self.message = message

    def _run(self, action: Callable[..., str]) -> None:
        waited = 0.0
        while True:
            try:
                done = action(say=lambda text: self._set(self.phase, text), phase=self._set)
            except DockerUnavailable as problem:
                # Docker Desktop is often still starting when DataLab opens.
                self._set("problem", str(problem))
                if waited >= self.DOCKER_PATIENCE:
                    return
                self.database.sleep(self.DOCKER_RETRY)
                waited += self.DOCKER_RETRY
                continue
            except PracticeDatabaseProblem as problem:
                self._set("problem", str(problem))
                return
            except Exception as error:
                log.exception("the practice database couldn't be set up")
                self._set(
                    "problem",
                    f"The practice database couldn't be set up ({type(error).__name__}). Try "
                    "again; if it happens again, reset it, or send feedback.",
                )
                return
            self.adopted = done.startswith("Using the synthetic database")
            self._set("ready", done)
            try:
                self.on_ready()
            except Exception:
                log.exception("after the practice database became ready")
            return
