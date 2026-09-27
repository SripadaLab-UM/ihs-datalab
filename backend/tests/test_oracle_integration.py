"""Against a real Oracle: the synthetic database (see synthetic/README.md).

Run with `uv run pytest -m oracle`. These tests call OracleDatabase directly,
bypassing the SQL check, to prove the database session itself is read-only.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import oracledb
import pytest

from datalab.config import PRACTICE_ORACLE, QueryLimits
from datalab.data.catalog import Catalog
from datalab.data.oracle import (
    LimitExceeded,
    NotSyntheticDatabase,
    OracleDatabase,
    QueryCancelled,
    QueryFailed,
    QueryTimedOut,
)

pytestmark = pytest.mark.oracle

PASSWORD = os.environ.get("DATALAB_ORACLE_PASSWORD", "")
# Any table the synthetic database has in each cohort.
TABLE_2025 = os.environ.get("DATALAB_TEST_TABLE_2025", "IHS_2025.VFITBITDAILYDATA")
TABLE_2026 = os.environ.get("DATALAB_TEST_TABLE_2026", "IHS_2026.VFITBITDAILYDATA")
# A query that runs for a long time on any Oracle, for deadline tests.
SLOW_SQL = "SELECT COUNT(*) FROM all_objects a CROSS JOIN all_objects b CROSS JOIN all_objects c"


@pytest.fixture(scope="module")
def database() -> OracleDatabase:
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    return OracleDatabase(PRACTICE_ORACLE, PASSWORD, QueryLimits(deadline_seconds=3))


def extract(database: OracleDatabase, sql: str, out: Path, **kwargs):
    options = {"max_rows": 1_000_000, "max_bytes": 10**9, "preview_rows": 5}
    options.update(kwargs)
    return database.extract_to_csv(sql, {}, out, cancel=threading.Event(), **options)


def test_default_roles_include_a_write_role(database):
    """The synthetic account mirrors the real one, so SET ROLE really matters."""
    raw = oracledb.connect(user=PRACTICE_ORACLE.user, password=PASSWORD, dsn=PRACTICE_ORACLE.dsn)
    with raw, raw.cursor() as cursor:
        cursor.execute("SELECT role FROM session_roles")
        assert "IHS_2026_ROLE" in {r[0] for r in cursor}


def test_datalab_sessions_are_read_only(database):
    privileges = database.session_privileges()
    assert privileges.enabled_roles == set(PRACTICE_ORACLE.read_only_roles)
    assert privileges.system_privileges == {"CREATE SESSION"}
    assert privileges.is_read_only


@pytest.mark.parametrize(
    "sql",
    [
        f"DELETE FROM {TABLE_2026}",
        f"ALTER TABLE {TABLE_2026} ADD (DATALAB_TEST NUMBER)",
        "CREATE TABLE DATALAB_TEST (x NUMBER)",
    ],
)
def test_writes_fail_even_without_the_sql_check(database, tmp_path, sql):
    with pytest.raises(QueryFailed):
        extract(database, sql, tmp_path / "out.csv")
    assert not (tmp_path / "out.csv").exists()


def test_extract_writes_every_row(database, tmp_path):
    result = extract(database, f"SELECT * FROM {TABLE_2025}", tmp_path / "out.csv")
    lines = (tmp_path / "out.csv").read_text().splitlines()
    assert result.row_count == len(lines) - 1 > 0
    assert len(result.preview) == min(5, result.row_count)


def test_row_cap_removes_partial_output(database, tmp_path):
    with pytest.raises(LimitExceeded):
        extract(database, f"SELECT * FROM {TABLE_2025}", tmp_path / "out.csv", max_rows=1)
    assert list(tmp_path.iterdir()) == []


def test_deadline_cancels_in_the_database(database, tmp_path):
    with pytest.raises(QueryTimedOut):
        extract(database, SLOW_SQL, tmp_path / "out.csv")
    assert list(tmp_path.iterdir()) == []


def test_stop_cancels_in_the_database(database, tmp_path):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(QueryCancelled):
        database.extract_to_csv(
            SLOW_SQL,
            {},
            tmp_path / "out.csv",
            max_rows=10,
            max_bytes=10**6,
            preview_rows=1,
            cancel=cancel,
        )


def test_practice_profile_refuses_a_database_without_the_marker(database, monkeypatch):
    # Simulate "something else answering on the practice port" by looking for
    # a marker that doesn't exist.
    import datalab.data.oracle as oracle_module

    monkeypatch.setattr(oracle_module, "SYNTHETIC_MARKER", "DATALAB_SYNTHETIC.NO_SUCH_MARKER")
    with pytest.raises(NotSyntheticDatabase):
        database.connect()


def test_catalog_from_database(database):
    connection = database.connect()
    try:
        catalog = Catalog.from_database(connection, sorted(PRACTICE_ORACLE.allowed_schemas))
    finally:
        connection.close()
    assert catalog.get(TABLE_2025) is not None
    assert catalog.get(TABLE_2025).columns  # type: ignore[union-attr]


def test_extract_names_each_columns_type_as_oracle_does(database, tmp_path):
    sql = (
        "SELECT CAST('a' AS VARCHAR2(64 CHAR)) a, CAST('a' AS NVARCHAR2(10)) b, "
        "TO_CLOB('x') c, CAST(1 AS BINARY_DOUBLE) d, CAST(1 AS FLOAT) e, "
        "CAST(1 AS NUMBER(10,2)) f, CAST(1 AS NUMBER(5)) g, 1 h, SYSDATE i, "
        "CAST(SYSTIMESTAMP AS TIMESTAMP) j, CAST(HEXTORAW('AB') AS RAW(8)) k FROM DUAL"
    )
    result = extract(database, sql, tmp_path / "out.csv")
    assert result.column_types == [
        "VARCHAR2(64)",
        "NVARCHAR2(10)",
        "CLOB",
        "BINARY_DOUBLE",
        "FLOAT(126)",
        "NUMBER(10,2)",
        "NUMBER(5)",
        "NUMBER",
        "DATE",
        "TIMESTAMP(6)",
        "RAW(8)",
    ]


def test_a_database_that_isnt_there_fails_the_query_cleanly(tmp_path):
    """Connecting is part of the query: a refused connection is a QueryFailed."""
    import dataclasses

    closed = dataclasses.replace(PRACTICE_ORACLE, port=1)
    database = OracleDatabase(closed, PASSWORD or "x", QueryLimits(deadline_seconds=3))
    with pytest.raises(QueryFailed):
        extract(database, "SELECT 1 FROM DUAL", tmp_path / "out.csv")
    assert list(tmp_path.iterdir()) == []
