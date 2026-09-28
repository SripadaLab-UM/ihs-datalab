"""Practice DataLab's synthetic database (datalab.practice_db), against a
stand-in for the `docker` command: set up once, started when needed, its
data kept across reinstalls, reset only when asked, on 127.0.0.1 only."""

from __future__ import annotations

import json

import pytest

from datalab import cli, practice_db, setup
from datalab.config import PRACTICE_ORACLE
from datalab.practice_db import (
    LABEL,
    PracticeDatabase,
    PracticeDatabaseKeeper,
    PracticeDatabaseProblem,
    Target,
)

TARGET = Target(container="test-practice-oracle", volume="test-practice-data", port=1599)


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
        if args[:2] == ["rm", "-f"]:
            self.containers.pop(args[2], None)
            return 0, "", ""
        raise AssertionError(f"unexpected docker {args}")

    @staticmethod
    def _inspect(found: dict) -> dict:
        ip, port, _ = (
            found["publish"].rsplit(":", 2)
            if found["publish"].count(":") == 2
            else ("", *found["publish"].split(":"))
        )
        return {
            "State": {"Status": found["status"], "Health": {"Status": found["health"]}},
            "Config": {"Labels": found["labels"]},
            "HostConfig": {"PortBindings": {"1521/tcp": [{"HostIp": ip, "HostPort": port}]}},
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
        sleep=lambda _: None,
        **extra,
    )


def test_the_first_setup_downloads_creates_and_loads_the_data(docker, oracle):
    said = []
    phases = []
    done = database(docker, oracle).ensure(said.append, phases.append)
    assert done == "The practice database is set up, with its made-up data."
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
    assert done == "The practice database is running, with its made-up data."
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
    assert ["rm", "-f", TARGET.container] in docker.calls
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


def test_another_synthetic_database_on_the_port_is_used_as_it_is(docker, oracle):
    """synthetic/db.sh's development container, or CI's: used, never loaded or reset."""
    db = database(docker, oracle, port_open=True)
    db.has_data = lambda dsn, wait=180: True
    assert db.ensure().startswith("Using the synthetic database already running on port 1599")
    with pytest.raises(PracticeDatabaseProblem, match="won't reset it"):
        db.reset()
    assert oracle.loads == []
    assert not [c for c in docker.calls if c[0] in ("run", "rm", "pull")]


def test_something_else_on_the_port_is_a_problem_not_a_takeover(docker, oracle):
    db = database(docker, oracle, port_open=True)

    def not_oracle(dsn, wait=180):
        raise OSError("not a database")

    db.has_data = not_oracle
    with pytest.raises(PracticeDatabaseProblem, match="Something else on this computer"):
        db.ensure()
    assert not [c for c in docker.calls if c[0] in ("run", "pull")]


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


def test_names_that_could_reach_docker_as_options_are_refused():
    for name in ("-v", "--privileged", "a b", ""):
        with pytest.raises(ValueError):
            Target(container=name)


def test_the_port_override_reaches_practice_settings_and_nothing_else(monkeypatch):
    from datalab import config

    monkeypatch.setenv("DATALAB_PRACTICE_DB_PORT", "1532")
    assert config._practice_db_port() == 1532
    monkeypatch.setenv("DATALAB_PRACTICE_DB_PORT", "22; rm")
    with pytest.raises(ValueError):
        config._practice_db_port()
    assert PRACTICE_ORACLE.host == "127.0.0.1"


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
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        docker.down = len(waits) < 3

    db.sleep = sleep
    docker.down = True
    keeper = PracticeDatabaseKeeper(db)
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "ready" and len(waits) == 3


def test_the_keeper_reports_a_problem_in_words(docker, oracle):
    docker.pull_errors = ["unauthorized"]
    keeper = PracticeDatabaseKeeper(database(docker, oracle))
    keeper.start()
    keeper._thread.join(5)  # type: ignore[union-attr]
    assert keeper.phase == "problem" and "couldn't be downloaded" in keeper.message


# -------------------------------------------------------------------- cli


def test_practice_db_refuses_the_real_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", lambda *a, **k: called.append(1))
    assert cli.main(["--profile", "real", "practice-db", "setup"]) == 2
    assert "practice DataLab's synthetic database only" in capsys.readouterr().out
    assert called == []


def test_practice_db_setup_runs_the_setup(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", lambda self, say, **k: "all set")
    assert cli.main(["--profile", "practice", "practice-db", "setup"]) == 0
    assert "all set" in capsys.readouterr().out


def test_practice_db_reset_asks_first(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    resets = []
    monkeypatch.setattr(
        practice_db.PracticeDatabase, "reset", lambda self, say, **k: resets.append(1) or "reset"
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main(["--profile", "practice", "practice-db", "reset"]) == 1
    assert resets == [] and "Nothing was changed." in capsys.readouterr().out
    assert cli.main(["--profile", "practice", "practice-db", "reset", "--yes"]) == 0
    assert resets == [1]


def test_pull_images_on_practice_also_downloads_oracle(monkeypatch, tmp_path):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    pulled = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, **_: pulled.append(args[-1]) or type("Done", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        practice_db.PracticeDatabase, "pull", lambda self, **k: pulled.append("oracle") or True
    )
    assert cli.main(["--profile", "practice", "pull-images"]) == 0
    assert pulled[-1] == "oracle"
    pulled.clear()
    assert cli.main(["--profile", "real", "pull-images"]) == 0
    assert "oracle" not in pulled


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
    monkeypatch.setattr(practice_db.PracticeDatabase, "ensure", lambda self, say, **k: "ok")
    monkeypatch.setattr(practice_db.PracticeDatabase, "describe", lambda self: "ok")
    for command in ("setup", "start", "status"):
        assert cli.main(["--profile", "practice", "practice-db", command]) == 0
    assert set(loaded) == {"practice"}
    assert practice_db.target_from_env().port == PRACTICE_ORACLE.port
    assert practice_db.target_from_env().dsn.startswith("127.0.0.1:")
