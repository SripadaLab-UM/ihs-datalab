"""The SQL Playground against the synthetic database: types, results, and Stop.

Run with `uv run pytest -m oracle` (needs DATALAB_ORACLE_PASSWORD).
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.config import PRACTICE_ORACLE, QueryLimits, Settings
from datalab.data.catalog import Catalog
from datalab.data.oracle import OracleDatabase

pytestmark = pytest.mark.oracle

PASSWORD = os.environ.get("DATALAB_ORACLE_PASSWORD", "")
TABLE_2025 = os.environ.get("DATALAB_TEST_TABLE_2025", "IHS_2025.VFITBITDAILYDATA")


@pytest.fixture(scope="module")
def oracle() -> tuple[OracleDatabase, Catalog]:
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    database = OracleDatabase(PRACTICE_ORACLE, PASSWORD, QueryLimits(deadline_seconds=60))
    connection = database.connect()
    try:
        catalog = Catalog.from_database(connection, sorted(PRACTICE_ORACLE.allowed_schemas))
    finally:
        connection.close()
    return database, catalog


@pytest.fixture
def client(oracle, tmp_path):
    database, catalog = oracle
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=PRACTICE_ORACLE)
    app = create_app(
        settings, database=database, catalog=catalog, manage_containers=False, protect_api=False
    )
    with TestClient(app) as client:
        yield client


def test_a_result_has_the_columns_types_the_catalog_has(client, oracle):
    _, catalog = oracle
    table = catalog.get(TABLE_2025)
    assert table is not None
    names = [c.name for c in table.columns[:4]]
    sql = f"SELECT {', '.join(names)} FROM {TABLE_2025} FETCH FIRST 10 ROWS ONLY"
    started = client.post("/api/sql/runs", json={"sql": sql}).json()
    done = client.get(f"/api/sql/runs/{started['id']}", params={"wait": 30}).json()
    assert done["state"] == "succeeded", done["message"]
    assert [c["name"] for c in done["columns"]] == names
    assert [c["type"] for c in done["columns"]] == [c.type for c in table.columns[:4]]
    page = client.get(f"/api/sql/results/{done['query_id']}").json()
    assert len(page["rows"]) == done["row_count"] <= 10
    assert [c["type"] for c in page["columns"]] == [c["type"] for c in done["columns"]]


def test_stop_cancels_a_playground_query_in_the_database(client):
    # Joins a view to itself three times: far too long to finish.
    slow = (
        f"SELECT COUNT(*) FROM {TABLE_2025} a CROSS JOIN {TABLE_2025} b CROSS JOIN {TABLE_2025} c"
    )
    started = client.post("/api/sql/runs", json={"sql": slow}).json()
    assert started["state"] == "running", started["message"]
    time.sleep(1)
    begun = time.monotonic()
    stopped = client.post(f"/api/sql/runs/{started['id']}/stop").json()
    assert stopped["state"] == "stopped"
    assert time.monotonic() - begun < 10
    [item] = client.get("/api/sql/history").json()
    assert item["status"] == "cancelled"
    assert item["has_result"] is False
