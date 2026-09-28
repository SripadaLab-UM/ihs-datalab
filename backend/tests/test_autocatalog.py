"""DataLab builds its own catalog the first time it reaches the database."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import oracledb
import pytest
from fastapi.testclient import TestClient
from oracledb import errors as oracledb_errors

from datalab import datalock, db
from datalab.app import _LazyOracle, catalog_auto_build, catalog_folder, catalog_problem, create_app
from datalab.config import PRACTICE_ORACLE, QueryLimits, Settings
from datalab.credentials import MissingCredential
from datalab.data.access_log import AccessLog
from datalab.data.autocatalog import RETRY_SECONDS, CatalogAutoBuild
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.oracle import NotSyntheticDatabase, QueryFailed
from datalab.data.service import DataService
from tests.conftest import COHORTS, FakeDatabase, sample_catalog


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_an_empty_catalog_is_built_saved_and_shared(tmp_path: Path) -> None:
    shared = Catalog([])
    builds = []

    def build() -> Catalog:
        builds.append(1)
        return sample_catalog()

    auto = CatalogAutoBuild(shared, tmp_path / "catalog", build)
    assert auto.ensure() is True
    # Everything holding the catalog sees the tables at once.
    assert len(shared) == len(sample_catalog()) and shared.get("IHS_2025.VFITBITDAILYDATA")
    # And the next start loads them from the data folder.
    assert len(Catalog.load(tmp_path / "catalog")) == len(shared)
    assert not (tmp_path / ".catalog.building").exists()
    assert auto.ensure() is True and len(builds) == 1


def test_a_catalog_with_tables_is_never_rebuilt(tmp_path: Path) -> None:
    def build() -> Catalog:
        raise AssertionError("built again")

    assert CatalogAutoBuild(sample_catalog(), tmp_path / "catalog", build).ensure() is True
    assert not (tmp_path / "catalog").exists()


def test_a_database_it_cant_reach_yet_is_tried_again_later(tmp_path: Path) -> None:
    shared, clock, tries = Catalog([]), Clock(), []

    def build() -> Catalog:
        tries.append(clock.now)
        if len(tries) == 1:
            raise MissingCredential("No database password saved for DATALAB_RO.")
        return sample_catalog()

    auto = CatalogAutoBuild(shared, tmp_path / "catalog", build, clock=clock)
    assert auto.ensure() is False
    assert auto.problem == "No database password saved for DATALAB_RO."
    # Not again at once: every query would wait for the database otherwise.
    assert auto.ensure() is False and len(tries) == 1
    clock.now += RETRY_SECONDS
    assert auto.ensure() is True and len(shared) and auto.problem is None


def test_saving_a_password_tries_again_at_once(tmp_path: Path) -> None:
    clock, tries = Clock(), []

    def build() -> Catalog:
        tries.append(1)
        if len(tries) == 1:
            raise MissingCredential("No password.")
        return sample_catalog()

    auto = CatalogAutoBuild(Catalog([]), tmp_path / "catalog", build, clock=clock)
    assert auto.ensure() is False
    auto.try_again_soon()
    assert auto.ensure() is True


def test_a_database_with_no_tables_leaves_it_empty(tmp_path: Path) -> None:
    auto = CatalogAutoBuild(Catalog([]), tmp_path / "catalog", lambda: Catalog([]))
    assert auto.ensure() is False
    assert auto.problem and "no tables" in auto.problem
    assert not (tmp_path / "catalog").exists()


def test_a_half_built_folder_from_before_is_replaced(tmp_path: Path) -> None:
    (tmp_path / ".catalog.building" / "OLD").mkdir(parents=True)
    (tmp_path / "catalog").mkdir()
    auto = CatalogAutoBuild(Catalog([]), tmp_path / "catalog", sample_catalog)
    assert auto.ensure() is True
    assert sorted(p.name for p in (tmp_path / "catalog").iterdir()) == ["IHS_2024", "IHS_2025"]


def test_only_practice_datalab_builds_its_own_catalog_folder(tmp_path: Path) -> None:
    """A catalog folder the lab's settings name (the knowledge base's) is
    never written; without one, practice DataLab keeps its own in the data
    folder. The real profile doesn't build one (yet)."""
    own = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    lab = dataclasses.replace(own, catalog_dir=tmp_path / "kb" / "generated" / "schema")
    no_database = dataclasses.replace(own, oracle=None)
    real = dataclasses.replace(own, profile="real")

    assert catalog_folder(own) == tmp_path / "data" / "catalog"
    assert catalog_auto_build(own, Catalog([]), sample_catalog) is not None
    assert catalog_folder(lab) == tmp_path / "kb" / "generated" / "schema"
    assert catalog_auto_build(lab, Catalog([]), sample_catalog) is None
    assert catalog_auto_build(no_database, Catalog([]), sample_catalog) is None
    assert catalog_auto_build(real, Catalog([]), sample_catalog) is None
    assert "datalab catalog" in (catalog_problem(lab, Catalog([]), None) or "")
    assert "datalab catalog" in (catalog_problem(real, Catalog([]), None) or "")


def _oracle_error(message: str) -> oracledb.DatabaseError:
    return oracledb.DatabaseError(oracledb_errors._Error(message))


STOPPING = [
    _oracle_error("ORA-01017: invalid credential or not authorized; logon denied"),
    _oracle_error("ORA-28000: The account is locked."),
    _oracle_error("ORA-28001: the password has expired"),
    NotSyntheticDatabase("Practice DataLab only works with the local synthetic database."),
]


@pytest.mark.parametrize("error", STOPPING, ids=["01017", "28000", "28001", "not-synthetic"])
def test_errors_trying_again_cant_fix_stop_the_tries(tmp_path: Path, error: Exception) -> None:
    """A wrong password tried every 30 s would lock the account; the wrong
    database (a tunnel on the synthetic one's port) shouldn't be knocked on."""
    clock, tries = Clock(), []

    def build() -> Catalog:
        tries.append(1)
        if len(tries) == 1:
            raise error
        return sample_catalog()

    auto = CatalogAutoBuild(Catalog([]), tmp_path / "catalog", build, clock=clock)
    assert auto.ensure() is False
    assert auto.stopped and auto.state == "stopped"
    assert auto.problem == str(error).splitlines()[0]
    clock.now += 24 * 3600
    assert auto.ensure() is False and len(tries) == 1
    # Until a password is saved (or DataLab starts again).
    auto.try_again_soon()
    assert not auto.stopped
    assert auto.ensure() is True and auto.state == "ready"


def test_other_database_errors_are_tried_again_later(tmp_path: Path) -> None:
    def build() -> Catalog:
        raise _oracle_error("DPY-6005: cannot connect to database")

    auto = CatalogAutoBuild(Catalog([]), tmp_path / "catalog", build, clock=Clock())
    assert auto.ensure() is False
    assert not auto.stopped and auto.state == "waiting-for-database"


def _started(settings: Settings, *, protect: bool = False) -> TestClient:
    return TestClient(create_app(settings, manage_containers=False, protect_api=protect))


def _wait(client: TestClient, until) -> dict:
    status: dict = {}
    for _ in range(200):
        status = client.get("/api/catalog/status").json()
        if until(status):
            return status
        time.sleep(0.02)
    raise AssertionError(status)


def test_a_first_start_builds_the_catalog_and_the_next_one_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Practice DataLab on a fresh install: the catalog is built at startup,
    in the background, recorded in the audit log, and Settings shows it."""
    reads = []

    def from_database(lazy: _LazyOracle) -> Catalog:
        reads.append(lazy)
        return sample_catalog()

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with _started(settings) as client:
        status = _wait(client, lambda s: s["state"] == "ready")
        assert status == {"state": "ready", "tables": len(sample_catalog()), "detail": None}
        health = client.get("/api/health").json()
        assert health["catalog_tables"] == len(sample_catalog())
        assert health["catalog_state"] == "ready"
        assert client.get("/api/sql/catalog").json()
    datalock.release_all()
    audit = (tmp_path / "data" / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    [entry] = [json.loads(line) for line in audit.splitlines()]
    assert entry["event"] == "catalog_built" and entry["tables"] == len(sample_catalog())
    assert entry["schemas"] == ["IHS_2024", "IHS_2025"]

    # The next start loads it from the data folder, and doesn't read the database.
    reads.clear()
    with _started(settings) as client:
        assert client.get("/api/health").json()["catalog_tables"] == len(sample_catalog())
    assert reads == []


def test_health_says_only_how_the_catalog_is_doing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/health needs no sign-in: no database user, account state, Oracle
    code or path (with the OS user name in it) there. Settings, signed in,
    gets the details."""

    def from_database(lazy: _LazyOracle) -> Catalog:
        raise _oracle_error("ORA-28000: The account DATALAB_RO is locked.")

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with _started(settings, protect=True) as client:
        # Signed out: the details aren't there.
        assert client.get("/api/catalog/status").status_code == 401
        health: dict = {}
        for _ in range(200):
            health = client.get("/api/health").json()
            if health["catalog_state"] == "stopped":
                break
            time.sleep(0.02)
        assert health["catalog_state"] == "stopped"
        text = json.dumps(health)
        for secret in ("DATALAB_RO", "ORA-", "locked", str(tmp_path), Path.home().name):
            assert secret not in text

        browser = client.app.state.browser  # type: ignore[attr-defined]
        client.get(browser.sign_in_path())
        status = client.get("/api/catalog/status").json()
        assert status["state"] == "stopped"
        assert "ORA-28000" in status["detail"] and "setup --update" in status["detail"]


def test_a_database_it_cant_reach_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def from_database(lazy: _LazyOracle) -> Catalog:
        raise MissingCredential("No database password saved for DATALAB_RO.")

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with _started(settings) as client:
        status = _wait(client, lambda s: s["state"] == "waiting-for-database")
        assert "at the next query, at most every 30 s" in status["detail"]
        assert status["detail"].endswith("Last try: No database password saved for DATALAB_RO.")


def test_the_sql_table_browser_builds_it_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def from_database(lazy: _LazyOracle) -> Catalog:
        calls.append(1)
        if len(calls) == 1:  # the try at startup
            raise MissingCredential("No password yet.")
        return sample_catalog()

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with _started(settings) as client:
        _wait(client, lambda s: s["state"] == "waiting-for-database")
        service = client.app.state.services.data  # type: ignore[attr-defined]
        service._catalog_build.try_again_soon()
        assert client.get("/api/sql/catalog/search", params={"q": "fitbit"}).json()
        assert len(calls) == 2


async def test_the_agents_catalog_tools_build_it_first(tmp_path: Path) -> None:
    from datalab.data import agent_tools

    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    catalog = Catalog([])
    auto = CatalogAutoBuild(catalog, tmp_path / "catalog", sample_catalog)
    service = DataService(FakeDatabase(), log, QueryLimits(), COHORTS, catalog, catalog_build=auto)
    await service.ensure_catalog()
    assert len(catalog)
    source = Path(agent_tools.__file__).read_text(encoding="utf-8")
    for tool in ("search_catalog", "describe_table", "join_paths", "find_concept"):
        body = source.split(f"async def {tool}(", 1)[1].split("@server.tool", 1)[0]
        assert "await service.ensure_catalog()" in body, tool


async def test_a_query_waits_for_the_catalog_and_says_why_there_is_none(tmp_path: Path) -> None:
    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    database, catalog, clock = FakeDatabase(), Catalog([]), Clock()
    reachable = []

    def build() -> Catalog:
        if not reachable:
            raise MissingCredential("No database password saved for DATALAB_RO.")
        return sample_catalog()

    auto = CatalogAutoBuild(catalog, tmp_path / "catalog", build, clock=clock)
    service = DataService(database, log, QueryLimits(), COHORTS, catalog, catalog_build=auto)
    sql = "SELECT TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA"

    with pytest.raises(QueryFailed, match=r"no catalog.*Settings"):
        await service.run_query(session_id="s1", sql=sql, binds=None, results_dir=tmp_path / "r")
    assert database.calls == []
    assert [r.status for r in log.for_session("s1")] == ["rejected"]

    reachable.append(True)
    clock.now += RETRY_SECONDS
    outcome = await service.run_query(
        session_id="s1", sql=sql, binds=None, results_dir=tmp_path / "r"
    )
    assert outcome.row_count == 2 and len(catalog)


def test_the_dictionary_queries_get_the_round_trip_limit_not_the_connect_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Connection:
        call_timeout = 15_000
        seen: int | None = None

        def close(self) -> None:
            pass

    connection = Connection()

    class Database:
        def connect(self, *, timeout: float | None = None) -> Connection:
            assert timeout == 15
            return connection

    def from_database(conn: Connection, schemas: list[str]) -> Catalog:
        conn.seen = conn.call_timeout
        return sample_catalog()

    settings = Settings(
        profile="practice",
        data_dir=tmp_path,
        oracle=PRACTICE_ORACLE,
        limits=QueryLimits(round_trip_timeout_seconds=120),
    )
    lazy = _LazyOracle(settings)
    monkeypatch.setattr(lazy, "_get", lambda: Database())
    monkeypatch.setattr(Catalog, "from_database", from_database)
    assert len(lazy.build_catalog())
    assert connection.seen == 120_000


def test_a_catalog_with_names_that_arent_plain_is_never_saved(tmp_path: Path) -> None:
    for bad in (
        TableInfo("IHS_2025", "../../ESCAPE", "TABLE"),
        TableInfo("IHS/2025", "T", "TABLE"),
        TableInfo("IHS_2025", "T.YML", "TABLE"),
    ):
        with pytest.raises(ValueError, match="plain Oracle name"):
            Catalog([TableInfo("IHS_2025", "FIRST", "TABLE"), bad]).save(tmp_path / "catalog")
        assert not (tmp_path / "catalog").exists()
    # Quoted column names are fine: they're only YAML values.
    quoted = TableInfo("IHS_2025", "T$#_1", "TABLE", columns=[Column("Black tea", "NUMBER")])
    Catalog([quoted]).save(tmp_path / "catalog")
    loaded = Catalog.load(tmp_path / "catalog").get("IHS_2025.T$#_1")
    assert loaded is not None and loaded.columns[0].name == "Black tea"


@pytest.mark.oracle
def test_practice_datalab_builds_its_catalog_from_the_synthetic_database(tmp_path: Path) -> None:
    if not os.environ.get("DATALAB_ORACLE_PASSWORD"):
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with _started(settings) as client:
        status = _wait(client, lambda s: s["state"] != "building")
        assert status["state"] == "ready", status
        health = client.get("/api/health").json()
        assert set(health["catalog_schemas"]) == set(PRACTICE_ORACLE.allowed_schemas)
    table = Catalog.load(tmp_path / "data" / "catalog").get("IHS_2025.VFITBITDAILYDATA")
    assert table is not None and table.columns


def test_practice_starts_its_database_then_builds_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Practice DataLab opens at once, starts its database in the background
    (health says how it's going), and builds its catalog once it's up."""
    import threading

    from datalab.practice_db import PracticeDatabaseKeeper
    from tests.test_practice_db import FakeDocker, FakeOracle, database

    reads = []
    monkeypatch.setattr(
        _LazyOracle, "build_catalog", lambda lazy: reads.append(1) or sample_catalog()
    )
    docker = FakeDocker()
    oracle = FakeOracle(docker)
    release = threading.Event()
    held = database(docker, oracle)
    held.has_data = lambda dsn, wait=180: release.wait(5) and oracle.has_data(dsn)
    keeper = PracticeDatabaseKeeper(held)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    app = create_app(settings, manage_containers=False, protect_api=False, practice_database=keeper)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["practice_database"] in ("checking", "starting")
        release.set()
        for _ in range(200):
            if client.get("/api/health").json()["practice_database"] == "ready":
                break
            time.sleep(0.02)
        status = _wait(client, lambda s: s["state"] == "ready")
        assert status["tables"] == len(sample_catalog())
    assert oracle.loads and reads


def test_only_practice_looks_after_a_synthetic_database(tmp_path: Path) -> None:
    from datalab.practice_db import PracticeDatabaseKeeper

    settings = Settings(profile="real", data_dir=tmp_path / "data", oracle=None)
    with pytest.raises(ValueError, match="Only the practice DataLab"):
        create_app(settings, manage_containers=False, practice_database=PracticeDatabaseKeeper())
    datalock.release_all()
    health = TestClient(create_app(settings, manage_containers=False)).get("/api/health").json()
    assert health["practice_database"] is None
