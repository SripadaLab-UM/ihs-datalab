"""DataLab builds its own catalog the first time it reaches the database."""

from __future__ import annotations

import dataclasses
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datalab import datalock, db
from datalab.app import _LazyOracle, catalog_auto_build, catalog_folder, catalog_problem, create_app
from datalab.config import PRACTICE_ORACLE, QueryLimits, Settings
from datalab.credentials import MissingCredential
from datalab.data.access_log import AccessLog
from datalab.data.autocatalog import RETRY_SECONDS, CatalogAutoBuild
from datalab.data.catalog import Catalog
from datalab.data.oracle import QueryFailed
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


def test_datalab_only_ever_builds_its_own_catalog_folder(tmp_path: Path) -> None:
    """A catalog folder the lab's settings name (the knowledge base's) is
    never written; without one, DataLab keeps its own in the data folder."""
    own = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    lab = dataclasses.replace(own, catalog_dir=tmp_path / "kb" / "generated" / "schema")
    no_database = dataclasses.replace(own, oracle=None)

    assert catalog_folder(own) == tmp_path / "data" / "catalog"
    assert catalog_auto_build(own, Catalog([]), sample_catalog) is not None
    assert catalog_folder(lab) == tmp_path / "kb" / "generated" / "schema"
    assert catalog_auto_build(lab, Catalog([]), sample_catalog) is None
    assert catalog_auto_build(no_database, Catalog([]), sample_catalog) is None
    assert "datalab catalog" in (catalog_problem(lab, Catalog([]), None) or "")


def test_a_first_start_builds_the_catalog_and_the_next_one_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Practice DataLab on a fresh install: the catalog is built at startup,
    in the background, and Settings shows it."""
    reads = []

    def from_database(lazy: _LazyOracle) -> Catalog:
        reads.append(lazy)
        return sample_catalog()

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    app = create_app(settings, manage_containers=False, protect_api=False)
    with TestClient(app) as client:
        for _ in range(100):
            health = client.get("/api/health").json()
            if health["catalog_tables"]:
                break
            time.sleep(0.02)
        assert health["catalog_tables"] == len(sample_catalog())
        assert health["catalog_problem"] is None
        assert client.get("/api/sql/catalog").json()
    datalock.release_all()

    # The next start loads it from the data folder, and doesn't read the database.
    reads.clear()
    with TestClient(create_app(settings, manage_containers=False, protect_api=False)) as client:
        assert client.get("/api/health").json()["catalog_tables"] == len(sample_catalog())
    assert reads == []


def test_a_database_it_cant_reach_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def from_database(lazy: _LazyOracle) -> Catalog:
        raise MissingCredential("No database password saved for DATALAB_RO.")

    monkeypatch.setattr(_LazyOracle, "build_catalog", from_database)
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with TestClient(create_app(settings, manage_containers=False, protect_api=False)) as client:
        for _ in range(100):
            problem = client.get("/api/health").json()["catalog_problem"]
            if "Last try" in problem:
                break
            time.sleep(0.02)
        assert problem.endswith("Last try: No database password saved for DATALAB_RO.")


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

    with pytest.raises(QueryFailed, match=r"no catalog.*No database password saved"):
        await service.run_query(session_id="s1", sql=sql, binds=None, results_dir=tmp_path / "r")
    assert database.calls == []
    assert [r.status for r in log.for_session("s1")] == ["rejected"]

    reachable.append(True)
    clock.now += RETRY_SECONDS
    outcome = await service.run_query(
        session_id="s1", sql=sql, binds=None, results_dir=tmp_path / "r"
    )
    assert outcome.row_count == 2 and len(catalog)


@pytest.mark.oracle
def test_practice_datalab_builds_its_catalog_from_the_synthetic_database(tmp_path: Path) -> None:
    if not os.environ.get("DATALAB_ORACLE_PASSWORD"):
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    with TestClient(create_app(settings, manage_containers=False, protect_api=False)) as client:
        for _ in range(300):
            health = client.get("/api/health").json()
            if health["catalog_tables"]:
                break
            time.sleep(0.1)
        assert health["catalog_problem"] is None
        assert set(health["catalog_schemas"]) == set(PRACTICE_ORACLE.allowed_schemas)
    table = Catalog.load(tmp_path / "data" / "catalog").get("IHS_2025.VFITBITDAILYDATA")
    assert table is not None and table.columns
