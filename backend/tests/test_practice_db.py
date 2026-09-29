"""Practice DataLab's synthetic database (datalab.practice_db), against a
stand-in for the `docker` command: set up once, started when needed, its
data kept across reinstalls, reset only when asked, on 127.0.0.1 only."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from datalab import cli, practice_db, setup
from datalab.config import PRACTICE_ORACLE
from datalab.practice_db import (
    LABEL,
    Outcome,
    PracticeDatabase,
    PracticeDatabaseKeeper,
    PracticeDatabaseProblem,
    Target,
)

TARGET = Target(
    container="test-practice-oracle",
    volume="test-practice-data",
    port=1599,
    legacy="test-legacy-oracle",
)
DEV_IMAGE = "container-registry.oracle.com/database/free:latest-lite"


def other_container(docker, name, *, status="running", image=DEV_IMAGE, labels=None) -> None:
    """A container DataLab didn't make, publishing the practice port."""
    docker.containers[name] = {
        "status": status,
        "health": "healthy",
        "labels": labels or {},
        "publish": f"127.0.0.1:{TARGET.port}:1521",
        "volume": None,
        "image": image,
    }


class FakeDocker:
    """Just enough of `docker` for the practice database: containers,
    volumes and images, each remembered, and every command logged."""

    def __init__(self) -> None:
        self.containers: dict[str, dict] = {}
        self.volumes: dict[str, dict] = {}
        self.images: set[str] = set()
        self.calls: list[list[str]] = []
        self.pull_errors: list[str] = []
        self.down = False

    def __call__(self, args: list[str], *, timeout=None, show=False):
        self.calls.append(args)
        if self.down:
            return 1, "", "Cannot connect to the Docker daemon. Is the docker daemon running?"
        head = args[:2]
        if head == ["container", "inspect"]:
            found = self.containers.get(args[2])
            if found is None:
                return 1, "", f"Error: No such container: {args[2]}"
            return 0, json.dumps([self._inspect(found)]), ""
        if head == ["volume", "inspect"]:
            found = self.volumes.get(args[-1])
            if found is None:
                return 1, "", f"Error: No such volume: {args[-1]}"
            return 0, json.dumps(found["labels"]), ""
        if head == ["image", "inspect"]:
            return (0, "sha256:x", "") if args[-1] in self.images else (1, "", "No such image")
        if args[0] == "pull":
            if self.pull_errors:
                return 1, "", self.pull_errors.pop(0)
            self.images.add(args[1])
            return 0, "", ""
        if head == ["volume", "create"]:
            self.volumes[args[-1]] = {"labels": {args[3].split("=")[0]: "1"}, "data": False}
            return 0, args[-1], ""
        if head == ["volume", "rm"]:
            self.volumes.pop(args[2])
            return 0, "", ""
        if args[0] == "run":
            assert args[1] == "--detach"
            options = dict(zip(args[2:-1:2], args[3:-1:2], strict=True))
            name = options["--name"]
            volume = options["--volume"].split(":")[0]
            self.containers[name] = {
                "status": "running",
                "health": "healthy",
                "labels": {options["--label"].split("=")[0]: "1"},
                "publish": options["--publish"],
                "volume": volume,
                "image": args[-1],
            }
            return 0, "id", ""
        if args[0] in ("start", "stop"):
            self.containers[args[-1]]["status"] = "running" if args[0] == "start" else "exited"
            return 0, "", ""
        if args[:3] == ["rm", "-f", "-v"]:
            self.containers.pop(args[3], None)
            return 0, "", ""
        if args[0] == "rmi":
            if any(c["image"] == args[1] for c in self.containers.values()):
                return 1, "", "image is being used by a container"
            self.images.discard(args[1])
            return 0, "", ""
        if args[0] == "logs":
            found = self.containers[args[-1]]
            return 0, found.get("log", "DATABASE IS READY TO USE!\n"), ""
        if args[:2] == ["ps", "--filter"]:
            port = args[2].removeprefix("publish=")
            names = [
                f"{name}\t{c['image']}"
                for name, c in self.containers.items()
                if c["status"] == "running" and c["publish"].split(":")[-2] == port
            ]
            return 0, "\n".join(names), ""
        raise AssertionError(f"unexpected docker {args}")

    @staticmethod
    def _inspect(found: dict) -> dict:
        ip, port, _ = (
            found["publish"].rsplit(":", 2)
            if found["publish"].count(":") == 2
            else ("", *found["publish"].split(":"))
        )
        return {
            "State": {
                "Status": found["status"],
                "Health": {"Status": found["health"]},
                "StartedAt": "2026-09-28T16:48:00Z",
            },
            "Config": {"Labels": found["labels"], "Image": found["image"]},
            "HostConfig": {"PortBindings": {"1521/tcp": [{"HostIp": ip, "HostPort": port}]}},
            "Mounts": [
                {"Type": "volume", "Name": found["volume"], "Destination": "/opt/oracle/oradata"}
            ]
            if found.get("volume")
            else [],
        }


class FakeOracle:
    """The database inside: whether a volume holds the made-up data."""

    def __init__(self, docker: FakeDocker) -> None:
        self.docker = docker
        self.loads: list[str] = []

    def _volume(self) -> dict:
        container = self.docker.containers[TARGET.container]
        return self.docker.volumes[container["volume"]]

    def has_data(self, dsn: str, wait: float = 180) -> bool:
        assert dsn == TARGET.dsn
        return self._volume()["data"]

    def generate(self, dsn: str, say) -> None:
        assert dsn == "127.0.0.1:1599/FREEPDB1"
        self.loads.append(dsn)
        self._volume()["data"] = True


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def oracle(docker) -> FakeOracle:
    return FakeOracle(docker)


def database(docker, oracle, *, port_open: bool = False, **extra) -> PracticeDatabase:
    return PracticeDatabase(
        TARGET,
        docker=docker,
        port_open=lambda _: port_open,
        has_data=oracle.has_data,
        generate=oracle.generate,
        finds_marker=lambda dsn: False,
        sleep=lambda _: None,
        **extra,
    )


def test_the_first_setup_downloads_creates_and_loads_the_data(docker, oracle):
    said = []
    phases = []
    done = database(docker, oracle).ensure(said.append, phases.append)
    assert done == Outcome("The practice database is set up, with its made-up data.")
    assert oracle.loads == [TARGET.dsn]
    assert practice_db.IMAGE in docker.images
    assert phases[:3] == ["checking", "downloading", "creating"]
    assert phases[-2:] == ["loading", "ready"]
    container = docker.containers[TARGET.container]
    assert container["image"] == practice_db.IMAGE
    assert LABEL in container["labels"] and LABEL in docker.volumes[TARGET.volume]["labels"]
    run = next(call for call in docker.calls if call[0] == "run")
    assert run[run.index("--volume") + 1] == f"{TARGET.volume}:/opt/oracle/oradata"


def test_it_is_published_on_127_0_0_1_only(docker, oracle):
    database(docker, oracle).ensure()
    run = next(call for call in docker.calls if call[0] == "run")
    assert run[run.index("--publish") + 1] == "127.0.0.1:1599:1521"
    assert "-p" not in run and "--network" not in run
    assert Target().publish == "127.0.0.1:1522:1521"


def test_the_image_is_oracles_pinned_by_digest():
    assert practice_db.IMAGE.startswith("container-registry.oracle.com/database/free@sha256:")
    assert len(practice_db.IMAGE.split("@sha256:")[1]) == 64


def test_a_second_launch_starts_it_and_never_loads_again(docker, oracle):
    database(docker, oracle).ensure()
    docker.containers[TARGET.container]["status"] = "exited"  # the computer restarted
    done = database(docker, oracle).ensure()
    assert done.message == "The practice database is running, with its made-up data."
    assert oracle.loads == [TARGET.dsn]  # only the first time
    assert ["start", TARGET.container] in docker.calls


def test_a_reinstall_keeps_the_data(docker, oracle):
    """Even with the container gone (Docker Desktop reset its containers,
    say), the volume still holds the data, and it isn't loaded again."""
    database(docker, oracle).ensure()
    docker.containers.clear()
    database(docker, oracle).ensure()
    assert oracle.loads == [TARGET.dsn]
    assert docker.volumes[TARGET.volume]["data"] is True


def test_a_load_that_stopped_part_way_is_done_again(docker, oracle):
    database(docker, oracle).ensure()
    docker.volumes[TARGET.volume]["data"] = False  # no marker: it didn't finish
    database(docker, oracle).ensure()
    assert len(oracle.loads) == 2


def test_reset_only_when_asked_and_from_scratch(docker, oracle):
    db = database(docker, oracle)
    db.ensure()
    for _ in range(3):
        db.ensure()
    assert len(oracle.loads) == 1
    phases = []
    db.reset(phase=phases.append)
    assert "resetting" in phases
    assert ["rm", "-f", "-v", TARGET.container] in docker.calls
    assert ["volume", "rm", TARGET.volume] in docker.calls
    assert len(oracle.loads) == 2


def test_someone_elses_container_of_that_name_is_never_used_or_changed(docker, oracle):
    docker.containers[TARGET.container] = {
        "status": "exited",
        "health": None,
        "labels": {},
        "publish": "127.0.0.1:1599:1521",
        "volume": "theirs",
        "image": "x",
    }
    db = database(docker, oracle)
    for action in (db.ensure, db.reset, db.stop):
        with pytest.raises(PracticeDatabaseProblem, match="isn't one DataLab made"):
            action()
    assert db.remove(volume=True) == []
    assert not [c for c in docker.calls if c[0] in ("start", "rm", "run", "stop")]


def test_someone_elses_volume_of_that_name_is_never_used(docker, oracle):
    docker.volumes[TARGET.volume] = {"labels": {}, "data": True}
    with pytest.raises(PracticeDatabaseProblem, match="isn't one DataLab made"):
        database(docker, oracle).ensure()
    assert TARGET.container not in docker.containers


def test_a_container_reachable_from_other_computers_is_stopped(docker, oracle):
    database(docker, oracle).ensure()
    docker.containers[TARGET.container]["publish"] = "0.0.0.0:1599:1521"
    with pytest.raises(PracticeDatabaseProblem, match="reachable from other computers"):
        database(docker, oracle).ensure()
    assert docker.containers[TARGET.container]["status"] == "exited"


def test_no_login_until_the_image_says_its_ready(docker, oracle):
    """Healthy isn't enough: the image sets SYSTEM's password just after,
    and a login while it does can stop it from working (seen: ORA-04021,
    then refused logins locking the account)."""
    db = database(docker, oracle)
    db.ensure()
    container = docker.containers[TARGET.container]
    container["status"] = "exited"
    checks = []

    def sleep(_):
        checks.append(1)
        if len(checks) == 3:
            container["log"] = "...\nDATABASE IS READY TO USE!\n"

    db.sleep = sleep
    real_start = docker.__call__

    def start_then_quiet(args, **options):
        result = real_start(args, **options)
        if args[0] == "start":
            container["log"] = "Starting Oracle Database instance FREE.\n"
        return result

    db.docker = start_then_quiet

    def logged_in_too_early(dsn, wait=180):
        assert len(checks) >= 3, "logged in before the image said it's ready"
        return True

    db.has_data = logged_in_too_early
    db.ensure()
    logs = [c for c in docker.calls if c[0] == "logs"]
    assert logs[-1][:2] == ["logs", "--since"]


def test_a_refused_system_password_is_tried_only_a_few_times(monkeypatch):
    import oracledb

    from datalab.practice_db import generate

    tries = []

    class Refused:
        code = 1017

    def connect(**_):
        tries.append(1)
        raise oracledb.DatabaseError(Refused())

    monkeypatch.setattr(oracledb, "connect", connect)
    monkeypatch.setattr(generate.time, "sleep", lambda _: None)
    with pytest.raises(oracledb.DatabaseError):
        generate.connect_when_ready(TARGET.dsn)
    assert len(tries) == generate.MAX_REFUSED_LOGINS < 10


def test_a_container_of_ours_without_its_volume_isnt_used(docker, oracle):
    database(docker, oracle).ensure()
    docker.containers[TARGET.container]["volume"] = "elsewhere"
    with pytest.raises(PracticeDatabaseProblem, match="doesn't keep its data in the volume"):
        database(docker, oracle).ensure()


# ----------------------------------------------- a database DataLab didn't make


def no_system_login(dsn, wait=180):
    raise AssertionError("logged in as SYSTEM to a database DataLab didn't make")


def test_another_synthetic_database_on_the_port_is_used_as_it_is(docker, oracle):
    """synthetic/db.sh's development container, or CI's: used, never loaded or reset."""
    other_container(docker, "datalab-synthetic-oracle")
    asked = []
    db = database(docker, oracle, port_open=True)
    db.has_data = no_system_login
    db.finds_marker = lambda dsn: asked.append(dsn) or True
    done = db.ensure()
    assert done.adopted and "datalab-synthetic-oracle" in done.message
    assert asked == [TARGET.dsn]  # the app user, once
    with pytest.raises(PracticeDatabaseProblem, match="won't reset it"):
        db.reset()
    assert oracle.loads == []
    assert not [c for c in docker.calls if c[0] in ("run", "rm", "pull", "start")]


def test_no_login_at_all_to_a_port_no_oracle_free_container_publishes(docker, oracle):
    """An SSH tunnel to a real database, say: nothing logs in to it, as
    SYSTEM or anyone, so no account there can be locked or alerted on."""
    db = database(docker, oracle, port_open=True)
    db.has_data = no_system_login

    def no_login(dsn):
        raise AssertionError("logged in to something that isn't an Oracle Free container")

    db.finds_marker = no_login
    with pytest.raises(PracticeDatabaseProblem, match="Something else on this computer"):
        db.ensure()
    # A container publishing the port, but not Oracle Free: no login either.
    other_container(docker, "tunnel", image="alpine/socat")
    with pytest.raises(PracticeDatabaseProblem, match="Something else on this computer"):
        db.ensure()
    assert not [c for c in docker.calls if c[0] in ("run", "pull")]


def test_the_app_user_marker_check_never_retries_and_never_is_system(monkeypatch):
    import oracledb

    logins = []

    def connect(**options):
        logins.append(options)
        raise oracledb.DatabaseError("ORA-01017: invalid username/password")

    monkeypatch.setattr(oracledb, "connect", connect)
    with pytest.raises(oracledb.DatabaseError):
        practice_db.app_user_finds_marker(TARGET.dsn)
    assert len(logins) == 1
    assert logins[0]["user"] == "DATALAB_RO" and logins[0]["retry_count"] == 0


def test_a_refused_marker_check_is_a_busy_port(docker, oracle):
    other_container(docker, "datalab-synthetic-oracle")
    db = database(docker, oracle, port_open=True)
    db.has_data = no_system_login

    def refused(dsn):
        raise OSError("ORA-01017")

    db.finds_marker = refused
    with pytest.raises(PracticeDatabaseProblem, match="Something else on this computer"):
        db.ensure()


def test_a_stopped_development_container_is_started_not_duplicated(docker, oracle):
    """Upgrading from synthetic/db.sh: its container, stopped, holds the port.
    It's started and used, rather than a second database made beside it."""
    other_container(docker, TARGET.legacy, status="exited")
    db = database(docker, oracle)
    db.port_open = lambda _: docker.containers[TARGET.legacy]["status"] == "running"
    db.has_data = no_system_login
    db.finds_marker = lambda dsn: True
    done = db.ensure()
    assert done.adopted and ["start", TARGET.legacy] in docker.calls
    assert TARGET.container not in docker.containers
    assert not [c for c in docker.calls if c[0] in ("run", "pull")]
    with pytest.raises(PracticeDatabaseProblem, match="won't reset it"):
        db.reset()


def test_a_stopped_development_container_is_left_if_the_person_says_no(docker, oracle):
    other_container(docker, TARGET.legacy, status="exited")
    db = database(docker, oracle, use_legacy=lambda name: False)
    with pytest.raises(PracticeDatabaseProblem, match=r"docker start test-legacy-oracle"):
        db.ensure()
    assert docker.containers[TARGET.legacy]["status"] == "exited"
    assert TARGET.container not in docker.containers


def test_a_development_container_on_another_port_is_ignored(docker, oracle):
    other_container(docker, TARGET.legacy, status="exited")
    docker.containers[TARGET.legacy]["publish"] = "127.0.0.1:1600:1521"
    database(docker, oracle).ensure()
    assert TARGET.container in docker.containers
    assert ["start", TARGET.legacy] not in docker.calls


# ------------------------------------------------------------------ docker


def test_a_busy_registry_is_tried_again_then_explained(docker, oracle):
    docker.pull_errors = ["Error response from daemon: received unexpected HTTP status: 503"] * 2
    database(docker, oracle).ensure()
    assert [c[0] for c in docker.calls].count("pull") == 3
    docker.images.clear()
    docker.pull_errors = ["unexpected HTTP status: 503 Service Unavailable"] * 10
    with pytest.raises(PracticeDatabaseProblem, match="Oracle's side, not yours") as caught:
        database(docker, oracle).pull()
    assert "HTTP 503" in str(caught.value)


def test_a_refused_download_isnt_tried_again(docker, oracle):
    docker.pull_errors = ["Error response from daemon: unauthorized: authentication required"]
    with pytest.raises(PracticeDatabaseProblem, match=r"couldn't be downloaded: .*unauthorized"):
        database(docker, oracle).pull()
    assert [c[0] for c in docker.calls].count("pull") == 1


def test_docker_not_running_says_so(docker, oracle):
    docker.down = True
    with pytest.raises(practice_db.DockerUnavailable):
        database(docker, oracle).ensure()


@pytest.mark.parametrize(
    "error",
    [
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
        "request returned 500 Internal Server Error for API route and version "
        "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/containers/json, check if "
        "the server supports the requested API version",
        "error during connect: open //./pipe/docker_engine: The system cannot find the file",
        "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
    ],
)
def test_docker_desktop_starting_counts_as_the_engine_being_down(error):
    assert practice_db._engine_down(error)


def test_other_docker_errors_arent_the_engine_being_down():
    assert not practice_db._engine_down("Error response from daemon: No such image")


def test_names_that_could_reach_docker_as_options_are_refused():
    for name in ("-v", "--privileged", "a b", ""):
        with pytest.raises(ValueError):
            Target(container=name)


def test_a_bad_port_override_only_stops_practice(monkeypatch, tmp_path, capsys):
    from datalab import config

    monkeypatch.setenv("DATALAB_PRACTICE_DB_PORT", "1532")
    assert config._practice_db_port() == 1532
    monkeypatch.setenv("DATALAB_PRACTICE_DB_PORT", "22; rm")
    assert config._practice_db_port() == 1522  # never at import
    assert config.practice_db_port_problem()
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    assert config.load_settings("real").profile == "real"
    with pytest.raises(ValueError, match="DATALAB_PRACTICE_DB_PORT"):
        config.load_settings("practice")
    with pytest.raises(ValueError):
        practice_db.target_from_env()
    assert PRACTICE_ORACLE.host == "127.0.0.1"


# ------------------------------------------------------------ one at a time


def test_only_one_process_loads_at_a_time(docker, oracle, tmp_path):
    """The lock in the practice data folder: while one holds it, another
    `ensure` waits (up to its limit), then says why it didn't."""
    from datalab import datalock

    lock_dir = tmp_path / "practice-db"
    held = datalock.hold(lock_dir)
    try:
        with pytest.raises(practice_db.LoadingElsewhere, match="setting up the practice database"):
            database(docker, oracle, lock_dir=lock_dir).ensure()
        assert oracle.loads == [] and not docker.calls
    finally:
        held.release()
    database(docker, oracle, lock_dir=lock_dir).ensure()
    assert oracle.loads == [TARGET.dsn]


def test_a_load_finished_by_another_process_counts_as_done(docker, oracle):
    from datalab.practice_db.generate import AlreadyLoaded

    def loaded_meanwhile(dsn, say):
        raise AlreadyLoaded("The synthetic database has its data already.")

    db = database(docker, oracle)
    db.generate = loaded_meanwhile
    assert db.ensure().message == "The practice database is set up, with its made-up data."


def test_build_refuses_a_database_that_has_its_data(monkeypatch):
    from datalab.practice_db import generate

    class Cursor:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, *args, **binds):
            self.sql.append(sql)
            self.result = {"v$version": [("Oracle Database 23ai Free",)]}.get(
                "v$version" if "v$version" in sql else "", [("FREEPDB1",)]
            )
            if "dba_tables" in sql:
                self.result = [(1,)]

        def __iter__(self):
            return iter(self.result)

        def fetchone(self):
            return self.result[0]

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(generate, "connect_when_ready", lambda dsn, pwd: Connection())
    with pytest.raises(generate.AlreadyLoaded):
        generate.build(TARGET.dsn, say=lambda _: None)
    assert not [sql for sql in cursor.sql if "DROP" in sql or "CREATE" in sql]


# ------------------------------------------------------------------ keeper


def test_the_keeper_sets_it_up_in_the_background_then_says_ready(docker, oracle):
    ready = []
    keeper = PracticeDatabaseKeeper(database(docker, oracle), on_ready=lambda: ready.append(1))
    assert keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "ready" and ready == [1]
    assert not keeper.adopted


def test_the_keeper_waits_for_docker_desktop_to_start(docker, oracle):
    db = database(docker, oracle)
    phases = []

    def sleep(seconds):
        phases.append(keeper.phase)
        docker.down = len(phases) < 3

    db.sleep = sleep
    docker.down = True
    keeper = PracticeDatabaseKeeper(db)
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "ready"
    # While it waits, it isn't "problem": the pages keep asking.
    assert phases == ["waiting-for-docker"] * 3


def test_the_keeper_stops_waiting_for_docker_and_says_so(docker, oracle):
    db = database(docker, oracle)
    docker.down = True
    keeper = PracticeDatabaseKeeper(db)
    keeper.DOCKER_PATIENCE = 30
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "problem"
    assert "stopped waiting" in keeper.message and "Try again" in keeper.message
    assert "carries on by itself" not in keeper.message


def test_the_keeper_reports_a_problem_in_words(docker, oracle):
    docker.pull_errors = ["unauthorized"]
    keeper = PracticeDatabaseKeeper(database(docker, oracle))
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "problem" and "couldn't be downloaded" in keeper.message


def test_the_keeper_says_when_it_adopted_one(docker, oracle):
    other_container(docker, "datalab-synthetic-oracle")
    db = database(docker, oracle, port_open=True)
    db.finds_marker = lambda dsn: True
    keeper = PracticeDatabaseKeeper(db)
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "ready" and keeper.adopted


# -------------------------------------------------------------------- cli


def scratch_folder(monkeypatch, folder: Path) -> int:
    """A practice data folder of the test's own, on a free port of its own, so
    nothing running on this computer (a practice DataLab on 8766, say) changes
    what the command does."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.toml").write_text(f"port = {port}\n", encoding="utf-8")
    monkeypatch.setenv("DATALAB_DATA_DIR", str(folder))
    return port


def test_practice_db_refuses_the_real_profile(monkeypatch, tmp_path, capsys):
    scratch_folder(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", lambda *a, **k: called.append(1))
    assert cli.main(["--profile", "real", "practice-db", "setup"]) == 2
    assert "practice DataLab's synthetic database only" in capsys.readouterr().out
    assert called == []


def test_practice_db_setup_runs_the_setup(monkeypatch, tmp_path, capsys):
    scratch_folder(monkeypatch, tmp_path)
    seen = []

    def ensure(self, say, **k):
        seen.append(self.lock_dir)
        return Outcome("all set")

    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", ensure)
    assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 0
    assert "all set" in capsys.readouterr().out
    assert seen == [tmp_path / "practice-db"]  # the lock in practice's own folder


def test_practice_db_setup_leaves_it_to_a_running_practice_datalab(monkeypatch, tmp_path, capsys):
    from datalab import datalock

    scratch_folder(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", lambda *a, **k: called.append(1))
    monkeypatch.setattr(datalock, "in_use", lambda folder: folder == tmp_path)
    assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 0
    assert "looks after its database itself" in capsys.readouterr().out
    assert called == []


def test_practice_db_problems_are_one_line_not_a_traceback(monkeypatch, tmp_path, capsys):
    import oracledb

    from datalab.practice_db.guard import NotTheSyntheticDatabase

    scratch_folder(monkeypatch, tmp_path)
    for error in (
        NotTheSyntheticDatabase("Refusing to run: not Oracle Free."),
        oracledb.DatabaseError("ORA-12514: listener does not know of service"),
        ValueError("The practice database's container name '-x' isn't usable."),
    ):

        def ensure(self, say, error=error, **k):
            raise error

        monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", ensure)
        assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 1
        out = capsys.readouterr().out.strip()
        assert len(out.splitlines()) == 1 and "Traceback" not in out
    monkeypatch.undo()  # the real ensure again
    scratch_folder(monkeypatch, tmp_path)
    monkeypatch.setenv("DATALAB_PRACTICE_DB_CONTAINER", "-bad")
    assert cli.main(["--profile", "practice", "practice-db", "status"]) == 1
    assert "isn't usable" in capsys.readouterr().out


def test_practice_db_reset_asks_first(monkeypatch, tmp_path, capsys):
    scratch_folder(monkeypatch, tmp_path)
    resets = []
    monkeypatch.setattr(
        practice_db.PracticeDatabase,
        "reset",
        lambda self, say, **k: resets.append(1) or Outcome("reset"),
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main(["--profile", "practice", "practice-db", "reset"]) == 1
    assert resets == [] and "Nothing was changed." in capsys.readouterr().out
    assert cli.main(["--profile", "practice", "practice-db", "reset", "--yes"]) == 0
    assert resets == [1]


def serve_health(body: bytes) -> tuple[int, object]:
    """Something answering GET /api/health on a free port, until shut down."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], server


def test_another_app_on_practices_port_doesnt_stop_setup_or_reset(monkeypatch, tmp_path, capsys):
    """Only a practice DataLab counts as running: not any listener on its port."""
    port = scratch_folder(monkeypatch, tmp_path)
    other, server = serve_health(b'{"hello": "not DataLab"}')
    try:
        (tmp_path / "settings.toml").write_text(f"port = {other}\n", encoding="utf-8")
        monkeypatch.setattr(
            practice_db.PracticeDatabase, "ensure", lambda self, say, **k: Outcome("all set")
        )
        monkeypatch.setattr(
            practice_db.PracticeDatabase, "reset", lambda self, say, **k: Outcome("reset")
        )
        assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 0
        assert "all set" in capsys.readouterr().out
        assert cli.main(["--profile", "practice", "practice-db", "reset", "--yes"]) == 0
        assert "reset" in capsys.readouterr().out
    finally:
        server.shutdown()
    assert port != other


def test_a_practice_datalab_on_its_port_is_left_to_look_after_it(monkeypatch, tmp_path, capsys):
    scratch_folder(monkeypatch, tmp_path)
    port, server = serve_health(b'{"profile": "practice", "version": "0.3.0b3"}')
    try:
        (tmp_path / "settings.toml").write_text(f"port = {port}\n", encoding="utf-8")
        called = []
        monkeypatch.setattr(
            practice_db.PracticeDatabase, "ensure", lambda *a, **k: called.append(1)
        )
        monkeypatch.setattr(practice_db.PracticeDatabase, "reset", lambda *a, **k: called.append(1))
        assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 0
        assert "looks after its database itself" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="Quit it first"):
            cli.main(["--profile", "practice", "practice-db", "reset", "--yes"])
        assert called == []
    finally:
        server.shutdown()


class PullRun:
    """subprocess.run for `docker pull` of the gateway and proxy images."""

    def __init__(self) -> None:
        self.pulled: list[str] = []

    def __call__(self, args, **_):
        self.pulled.append(args[-1])
        return type("Done", (), {"returncode": 0, "stderr": ""})()


def test_pull_images_on_practice_downloads_oracle_for_a_first_setup_only(monkeypatch, tmp_path):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    run = PullRun()
    monkeypatch.setattr("subprocess.run", run)
    needed = [True]
    monkeypatch.setattr(practice_db.PracticeDatabase, "image_needed", lambda self: needed[0])
    monkeypatch.setattr(
        practice_db.PracticeDatabase, "pull", lambda self, **k: run.pulled.append("oracle") or True
    )
    assert cli.main(["--profile", "practice", "pull-images"]) == 0
    assert run.pulled[-1] == "oracle"
    # An existing practice database (an update, a reinstall): not downloaded again.
    run.pulled.clear()
    needed[0] = False
    assert cli.main(["--profile", "practice", "pull-images"]) == 0
    assert "oracle" not in run.pulled
    run.pulled.clear()
    needed[0] = True
    assert cli.main(["--profile", "real", "pull-images"]) == 0
    assert "oracle" not in run.pulled


def test_a_busy_registry_never_stops_an_install_or_an_update(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("subprocess.run", PullRun())
    monkeypatch.setattr(practice_db.PracticeDatabase, "image_needed", lambda self: True)

    def busy(self, **k):
        raise PracticeDatabaseProblem(practice_db.registry_busy_message("HTTP status: 503"))

    monkeypatch.setattr(practice_db.PracticeDatabase, "pull", busy)
    assert cli.main(["--profile", "practice", "pull-images"]) == 0
    out = capsys.readouterr().out
    assert "Oracle's side, not yours" in out and "Carrying on" in out


def test_the_image_is_needed_only_when_theres_no_practice_database_yet(docker, oracle):
    assert database(docker, oracle).image_needed()
    assert not database(docker, oracle, port_open=True).image_needed()
    database(docker, oracle).ensure()
    docker.images.clear()  # kept container and volume: nothing to download
    assert not database(docker, oracle).image_needed()
    docker.containers.clear()
    assert not database(docker, oracle).image_needed()  # the volume is there


# -------------------------------------------------------------- uninstall


def test_uninstall_asks_about_the_practice_database(monkeypatch, docker, oracle, capsys):
    db = database(docker, oracle)
    db.ensure()
    monkeypatch.setattr(practice_db, "PracticeDatabase", lambda: db)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    setup._uninstall_practice_database(None)
    out = capsys.readouterr().out
    assert f"the Docker container {TARGET.container} and its volume {TARGET.volume}" in out
    assert "Kept (stopped)" in out
    assert docker.containers[TARGET.container]["status"] == "exited"
    assert TARGET.volume in docker.volumes
    setup._uninstall_practice_database(True)
    assert TARGET.container not in docker.containers and TARGET.volume not in docker.volumes
    out = capsys.readouterr().out
    assert "Removed Oracle Database Free's image." in out
    assert practice_db.IMAGE not in docker.images


def test_uninstall_asks_about_oracles_image_when_asked_about_the_database(
    monkeypatch, docker, oracle, capsys
):
    db = database(docker, oracle)
    db.ensure()
    monkeypatch.setattr(practice_db, "PracticeDatabase", lambda: db)
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    setup._uninstall_practice_database(None)
    out = capsys.readouterr().out
    assert "Deleted." in out and "image was kept" in out
    assert practice_db.IMAGE in docker.images


def test_the_data_folders_answer_isnt_the_practice_databases(monkeypatch, tmp_path, capsys):
    """Without --delete-data or --keep-data, the practice database gets a
    question of its own, after the data folders'."""
    folder = tmp_path / "practice-data"
    folder.mkdir()
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(setup, "_datalab_running", lambda: False)
    monkeypatch.setattr(setup, "_docker_quiet", lambda *a, **k: None)
    monkeypatch.setattr(setup, "_forget", lambda *a: None)
    monkeypatch.setattr(setup, "default_data_dir", lambda profile: folder)
    given = []
    monkeypatch.setattr(setup, "_uninstall_practice_database", given.append)
    questions = []
    monkeypatch.setattr("builtins.input", lambda q: questions.append(q) or "y")
    assert setup.uninstall(delete_data=None) == 0
    assert given == [None] and len(questions) == 1  # asked about the folders only, here
    assert not folder.exists()
    given.clear()
    folder.mkdir()
    assert setup.uninstall(delete_data=False) == 0
    assert given == [False] and folder.exists()


def test_uninstall_leaves_a_database_datalab_didnt_make(monkeypatch, docker, oracle, capsys):
    docker.containers[TARGET.container] = {
        "status": "running",
        "health": "healthy",
        "labels": {},
        "publish": "127.0.0.1:1599:1521",
        "volume": "x",
        "image": "x",
    }
    monkeypatch.setattr(practice_db, "PracticeDatabase", lambda: database(docker, oracle))
    setup._uninstall_practice_database(True)
    assert capsys.readouterr().out == ""
    assert TARGET.container in docker.containers


# ----------------------------------------------------------- development


def test_db_sh_manages_an_unlabelled_container_only_by_its_own_name(monkeypatch):
    from datalab.practice_db import __main__ as dev

    made = []

    class Recorder:
        def __init__(self, target, manage_unlabelled):
            made.append((target.container, manage_unlabelled, target.legacy))

        def describe(self):
            return "ok"

    monkeypatch.setattr(dev, "PracticeDatabase", Recorder)
    monkeypatch.delenv("DATALAB_PRACTICE_DB_CONTAINER", raising=False)
    assert dev.main(["status"]) == 0
    monkeypatch.setenv("DATALAB_PRACTICE_DB_CONTAINER", "stale-name")
    assert dev.main(["status"]) == 0
    assert dev.main(["status", "--force"]) == 0
    assert made == [
        ("datalab-synthetic-oracle", True, None),
        ("stale-name", False, None),
        ("stale-name", True, None),
    ]


def test_db_sh_reset_doesnt_say_ready_when_it_isnt(monkeypatch, capsys):
    from datalab.practice_db import __main__ as dev

    class NotUp:
        def __init__(self, target, manage_unlabelled):
            pass

        def remove(self, volume):
            return []

        def up(self, say, show_download):
            return False

    monkeypatch.setattr(dev, "PracticeDatabase", NotUp)
    assert dev.main(["reset"]) == 1
    out = capsys.readouterr().out
    assert "ready" not in out and "left alone" in out


def test_practice_never_reads_the_real_profiles_settings(monkeypatch, tmp_path):
    """The practice database's commands, and DataLab looking after it, only
    ever load practice's settings; its database is PRACTICE_ORACLE's port."""
    from datalab import config

    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    loaded = []
    real_load = config.load_settings

    def load(profile=None):
        loaded.append(config.resolve_profile(profile))
        return real_load(profile)

    monkeypatch.setattr(cli, "load_settings", load)
    monkeypatch.setattr(setup, "load_settings", load)
    monkeypatch.setattr(
        practice_db.PracticeDatabase, "ensure", lambda self, say, **k: Outcome("ok")
    )
    monkeypatch.setattr(practice_db.PracticeDatabase, "describe", lambda self: "ok")
    for command in ("setup", "start", "status"):
        assert cli.main(["--profile", "practice", "practice-db", command]) == 0
    assert set(loaded) == {"practice"}
    assert practice_db.target_from_env().port == PRACTICE_ORACLE.port
    assert practice_db.target_from_env().dsn.startswith("127.0.0.1:")
