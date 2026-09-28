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
on the port, practice DataLab uses it as it is only if Docker says it's an
Oracle Database Free container and the app user (never SYSTEM, and only
once) finds the marker: synthetic/db.sh's development container, or CI's.
It never loads data into that one or resets it. Anything else on the port
(an SSH tunnel to a real database, say) gets no login at all. The real
profile never gets here.

Loading and resetting hold a lock in the practice data folder, so two
DataLab processes never load at once.

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from datalab.config import PRACTICE_ORACLE, practice_db_port_problem
from datalab.practice_db.guard import ADMIN_PWD, RO_PWD

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
# synthetic/db.sh's development container, from before DataLab set up its own.
LEGACY_CONTAINER = "datalab-synthetic-oracle"
# The image of a container that may be adopted: Oracle Database Free.
_ORACLE_FREE = re.compile(r"(^|/)database/free([:@]|$)")
# Where Oracle Free keeps its database files: the volume.
DATA_PATH = "/opt/oracle/oradata"
SERVICE = "FREEPDB1"
# What Oracle's image prints once the database is set up and open.
READY_LINE = "DATABASE IS READY TO USE!"

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")

Phase = Literal[
    "checking",
    "waiting-for-docker",
    "downloading",
    "creating",
    "starting",
    "loading",
    "resetting",
    "ready",
    "problem",
]


@dataclass(frozen=True)
class Outcome:
    """What `ensure` or `reset` did, in words."""

    message: str
    # The synthetic database on the port is one DataLab didn't set up.
    adopted: bool = False


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
    # The development container an earlier setup may have left (None: none).
    legacy: str | None = LEGACY_CONTAINER

    def __post_init__(self) -> None:
        names = [("container", self.container), ("volume", self.volume)]
        if self.legacy is not None:
            names.append(("legacy container", self.legacy))
        for what, name in names:
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
    """Raises ValueError for an override that can't be used."""
    problem = practice_db_port_problem()
    if problem:
        raise ValueError(problem)
    return Target(
        container=os.environ.get("DATALAB_PRACTICE_DB_CONTAINER") or CONTAINER,
        volume=os.environ.get("DATALAB_PRACTICE_DB_VOLUME") or VOLUME,
        port=PRACTICE_ORACLE.port,
        legacy=os.environ.get("DATALAB_PRACTICE_DB_LEGACY_CONTAINER") or LEGACY_CONTAINER,
    )


@dataclass(frozen=True)
class Container:
    status: str  # running, exited, created, paused, restarting, dead
    health: str | None  # starting, healthy, unhealthy; None: no health check
    ours: bool  # carries DataLab's label
    # Where the database's port is published: (host address, host port).
    published: tuple[tuple[str, str], ...] = ()
    image: str = ""
    # The named volumes mounted at the database's files.
    data_volumes: tuple[str, ...] = ()
    # When it last started (Docker's timestamp), for its log since then.
    started_at: str = ""

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
    """Docker's engine isn't there yet: not running, or Docker Desktop still
    starting (its API answers 500 until the engine is up)."""
    lowered = error.lower()
    return (
        "cannot connect to the docker daemon" in lowered
        or "is the docker daemon running" in lowered
        or "error during connect" in lowered
        or "docker desktop is not running" in lowered
        or "docker_engine" in lowered  # Windows' pipe
        or "dockerdesktoplinuxengine" in lowered  # Docker Desktop's pipe and socket
        or ("500 internal server error" in lowered and "api route" in lowered)
    )


_DOCKER_DOWN = (
    "Docker Desktop isn't running, and the practice database runs in it. Start Docker "
    "Desktop and wait until it says 'Engine running'."
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


def app_user_finds_marker(dsn: str) -> bool:
    """Whether the app user DATALAB_RO sees the marker at `dsn`: one login,
    never retried, never as SYSTEM. For a synthetic database DataLab didn't
    set up (and only once Docker says it's Oracle Database Free)."""
    import oracledb

    from datalab.practice_db.guard import MARKER_SCHEMA, require_local_dsn

    require_local_dsn(dsn)
    with oracledb.connect(
        user="DATALAB_RO", password=RO_PWD, dsn=dsn, tcp_connect_timeout=5, retry_count=0
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {MARKER_SCHEMA}.MARKER")
        (rows,) = cursor.fetchone() or (0,)
        return bool(rows)


def load_data(dsn: str, say: Callable[[str], None]) -> None:
    from datalab.practice_db.generate import build

    build(dsn, say=say)


class LoadingElsewhere(PracticeDatabaseProblem):
    """Another DataLab process is setting up or resetting the practice database."""


@contextmanager
def _holding(lock_dir: Path | None, *, wait: float) -> Iterator[None]:
    """The practice database's lock (`<practice data folder>/practice-db/.lock`),
    for up to `wait` seconds. No folder: no lock (development, tests)."""
    if lock_dir is None:
        yield
        return
    from datalab import datalock

    deadline = time.monotonic() + wait
    while True:
        try:
            lock = datalock.hold(lock_dir)
            break
        except datalock.DataFolderInUse:
            if time.monotonic() >= deadline:
                raise LoadingElsewhere(
                    "Another DataLab window or command is setting up the practice database "
                    "right now. Wait until it's done, then try again."
                ) from None
            time.sleep(1)
    try:
        yield
    finally:
        lock.release()


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
    # As SYSTEM, of DataLab's own container only.
    has_data: Callable[..., bool] = database_has_data
    # As the app user, once, of a synthetic database DataLab didn't set up.
    finds_marker: Callable[[str], bool] = app_user_finds_marker
    generate: Callable[[str, Callable[[str], None]], None] = load_data
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    # Where loading and resetting take their lock (the practice data folder's
    # practice-db/). None: no lock (development, tests).
    lock_dir: Path | None = None
    # Whether to start the development container an earlier setup left
    # (Target.legacy) and use it, rather than make a second database on its port.
    use_legacy: Callable[[str], bool] = lambda _: True
    # Development only (synthetic/db.sh, for its own container name): also
    # look after a container or volume of this name without DataLab's label.
    manage_unlabelled: bool = False
    ready_timeout: float = 15 * 60

    # What's there ------------------------------------------------------

    def inspect(self, name: str | None = None) -> Container | None:
        name = name or self.target.container
        code, out, error = self._docker("container", "inspect", name)
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
        config = data.get("Config") or {}
        labels = config.get("Labels") or {}
        bindings = ((data.get("HostConfig") or {}).get("PortBindings") or {}).get("1521/tcp") or []
        mounts = data.get("Mounts") or []
        return Container(
            status=state.get("Status", "unknown"),
            health=health,
            ours=LABEL in labels,
            published=tuple((b.get("HostIp", ""), b.get("HostPort", "")) for b in bindings),
            image=config.get("Image") or "",
            started_at=state.get("StartedAt") or "",
            data_volumes=tuple(
                m.get("Name", "")
                for m in mounts
                if m.get("Type") == "volume" and m.get("Destination") == DATA_PATH
            ),
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

    def image_needed(self) -> bool:
        """Whether a first setup still needs Oracle's image here: it isn't
        downloaded, and there's no practice database yet (DataLab's own
        container or volume, or a synthetic database already on the port)."""
        if self._docker("image", "inspect", "--format", "{{.Id}}", self.target.image)[0] == 0:
            return False
        if self.inspect() is not None or self.volume_state() != "missing":
            return False
        return not self.port_open(self.target.port)

    # Changes -------------------------------------------------------------

    def ensure(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show_download: bool = False,
        wait_for_lock: float = 0,
    ) -> Outcome:
        """Make the practice database ready: create or start the container if
        needed, and load the data only if it has none."""
        with _holding(self.lock_dir, wait=wait_for_lock):
            return self._ensure(say, phase, show_download=show_download)

    def _ensure(
        self, say: Callable[[str], None], phase: Callable[[Phase], None], *, show_download: bool
    ) -> Outcome:
        if not self.up(say, phase, show_download=show_download):
            return self._use_other_database()
        if self.has_data(self.target.dsn):
            phase("ready")
            return Outcome("The practice database is running, with its made-up data.")
        phase("loading")
        say("Loading the made-up data (a minute or two)…")
        from datalab.practice_db.generate import AlreadyLoaded

        # AlreadyLoaded: another process finished it in the meantime.
        with suppress(AlreadyLoaded):
            self.generate(self.target.dsn, say)
        phase("ready")
        return Outcome("The practice database is set up, with its made-up data.")

    def up(
        self,
        say: Callable[[str], None] = _quiet,
        phase: Callable[[Phase], None] = _no_phase,
        *,
        show_download: bool = False,
    ) -> bool:
        """Create or start the container and wait until the database is open.
        False: there's no container of ours, and the port is someone else's
        (left alone, or the development container, started)."""
        phase("checking")
        container = self.inspect()
        created = False
        if container is None:
            if self.port_open(self.target.port) or self._start_legacy(say, phase):
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
        wait_for_lock: float = 0,
    ) -> Outcome:
        """Remove the container and its volume, then set them up from scratch."""
        with _holding(self.lock_dir, wait=wait_for_lock):
            container = self.inspect()
            if container is None and (
                self.port_open(self.target.port) or self._legacy() is not None
            ):
                raise PracticeDatabaseProblem(
                    f"The synthetic database on port {self.target.port} isn't one DataLab set "
                    "up, so DataLab won't reset it. Reset it where it came from (for "
                    "synthetic/db.sh: synthetic/db.sh generate)."
                )
            if container is not None:
                self._require_manageable(container, publish=False)
            self._require_volume_manageable()
            phase("resetting")
            say("Removing the practice database and its data…")
            self.remove(volume=True)
            return self._ensure(say, phase, show_download=show_download)

    def remove(self, *, volume: bool) -> list[str]:
        """Remove the container (and its volume), if they're DataLab's. What went."""
        removed = []
        container = self.inspect()
        if container is not None and (container.ours or self.manage_unlabelled):
            # -v: and any anonymous volume the container has (an old development one's).
            self._check(
                self._docker("rm", "-f", "-v", self.target.container, timeout=120), "remove it"
            )
            removed.append(f"the container {self.target.container}")
        state = self.volume_state() if volume else "missing"
        if state == "ours" or (state == "other" and self.manage_unlabelled):
            self._check(
                self._docker("volume", "rm", self.target.volume, timeout=120), "remove its data"
            )
            removed.append(f"the volume {self.target.volume}")
        return removed

    def remove_image(self) -> bool:
        """Remove Oracle's image, unless a container still uses it. Whether it went."""
        return self._docker("rmi", self.target.image, timeout=300)[0] == 0

    # Parts -----------------------------------------------------------

    def _use_other_database(self) -> Outcome:
        """Something already answers on the port. It's used only if Docker
        says it's an Oracle Database Free container and the app user finds
        the marker there: no SYSTEM login, and nothing at all for anything
        else (an SSH tunnel to a real database, say)."""
        busy = PracticeDatabaseProblem(
            f"Something else on this computer is using port {self.target.port}, so the "
            "practice database can't start there. Quit whatever uses it (another synthetic "
            "database, or an SSH tunnel), then try again."
        )
        publisher = self._publisher()
        if publisher is None or not _ORACLE_FREE.search(publisher[1]):
            raise busy
        try:
            ready = self.finds_marker(self.target.dsn)
        except Exception as error:  # a login refused, no marker table, not a database at all
            log.info(
                "port %s answered, but not as the synthetic database: %s",
                self.target.port,
                type(error).__name__,
            )
            ready = False
        if not ready:
            raise busy
        return Outcome(
            f"Using the synthetic database already running on port {self.target.port}, in the "
            f"container {publisher[0]} (one DataLab didn't set up, so it's left as it is).",
            adopted=True,
        )

    def _publisher(self) -> tuple[str, str] | None:
        """The running container publishing the port, and its image, if any."""
        code, out, _ = self._docker(
            "ps", "--filter", f"publish={self.target.port}", "--format", "{{.Names}}\t{{.Image}}"
        )
        lines = [line.split("\t", 1) for line in out.splitlines() if "\t" in line]
        if code != 0 or len(lines) != 1:
            return None
        name, image = lines[0]
        return name, image

    def _legacy(self) -> Container | None:
        """The development container an earlier setup left, if it publishes
        this port on 127.0.0.1 and runs Oracle Database Free."""
        if not self.target.legacy or self.target.legacy == self.target.container:
            return None
        found = self.inspect(self.target.legacy)
        if found is None or not found.local_only or not _ORACLE_FREE.search(found.image):
            return None
        if (("127.0.0.1", str(self.target.port))) not in found.published:
            return None
        return found

    def _start_legacy(self, say: Callable[[str], None], phase: Callable[[Phase], None]) -> bool:
        """Start the stopped development container rather than make a second
        database on its port (it couldn't start again after that). Whether it
        did."""
        legacy = self._legacy()
        if legacy is None or legacy.status == "running":
            return False
        name = self.target.legacy
        assert name is not None
        if not self.use_legacy(name):
            raise PracticeDatabaseProblem(
                f"The development synthetic database ({name}) is on this computer, stopped, "
                f"and uses port {self.target.port}. Start it (docker start {name}) to use it, "
                "or remove it to let DataLab set up its own."
            )
        phase("starting")
        say(f"Starting the synthetic database already on this computer ({name})…")
        self._check(self._docker("start", name, timeout=300), f"start {name}")
        deadline = self.clock() + self.ready_timeout
        while True:
            found = self.inspect(name)
            if found is not None and found.health == "healthy" and self._said_ready(found, name):
                return True
            if found is None or found.status != "running" or self.clock() > deadline:
                raise PracticeDatabaseProblem(
                    f"The synthetic database {name} didn't start. Start it yourself "
                    f"(docker start {name}), or remove it to let DataLab set up its own."
                )
            self.sleep(3)

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
        if publish and container.ours and self.target.volume not in container.data_volumes:
            raise PracticeDatabaseProblem(
                f"The container {self.target.container} doesn't keep its data in the volume "
                f"{self.target.volume}, as DataLab sets it up. Reset the practice database to "
                "set it up again."
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
            if (container.health == "healthy" and self._said_ready(container)) or (
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

    def _said_ready(self, container: Container, name: str | None = None) -> bool:
        """Whether the image said "DATABASE IS READY TO USE!" since it last
        started. Its health check passes a little earlier, while it's still
        setting SYSTEM's password; a login then can stop that from working
        (ORA-04021), and retries would lock the account."""
        since = ["--since", container.started_at] if container.started_at else []
        name = name or self.target.container
        code, out, error = self._docker("logs", *since, name, timeout=30)
        return code == 0 and READY_LINE in out + error

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

    def _begin(self, action: Callable[..., Outcome], phase: Phase, message: str) -> bool:
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

    def _run(self, action: Callable[..., Outcome]) -> None:
        waited = 0.0
        while True:
            try:
                # A `practice-db` command may be loading it: wait for that, then carry on.
                done = action(
                    say=lambda text: self._set(self.phase, text),
                    phase=self._set,
                    wait_for_lock=self.DOCKER_PATIENCE,
                )
            except DockerUnavailable as problem:
                # Docker Desktop is often still starting when DataLab opens.
                if waited >= self.DOCKER_PATIENCE:
                    self._set(
                        "problem",
                        f"{problem} DataLab stopped waiting for it: once it's running, press "
                        "Try again.",
                    )
                    return
                self._set(
                    "waiting-for-docker", f"{problem} DataLab carries on by itself once it is."
                )
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
            self.adopted = done.adopted
            self._set("ready", done.message)
            try:
                self.on_ready()
            except Exception:
                log.exception("after the practice database became ready")
            return
