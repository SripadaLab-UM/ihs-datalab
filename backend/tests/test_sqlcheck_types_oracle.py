"""The SQL check's long-text and spelling rules, against a real Oracle: the
synthetic database, which has CLOB columns (STG_SURVEYDICTIONARY) and
mixed-case quoted ones (VW_BASELINE_SURVEY's "Bdate", "interest0").

Each query the check refuses must really fail on Oracle, with the error the
check predicts, so it never refuses SQL Oracle would run; and each corrected
query must pass the check and run. The catalog is the database's own
(metadata only), as practice DataLab builds it. Production runs 19c, where
these errors are ORA-00932; the synthetic database runs 23ai, which says
ORA-22848 or ORA-22849 for most of them.

Run with `uv run pytest -m oracle` (DATALAB_ORACLE_PASSWORD, synthetic/README.md).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import oracledb
import pytest

from datalab.config import PRACTICE_ORACLE, QueryLimits
from datalab.data.catalog import Catalog
from datalab.data.oracle import SYNTHETIC_MARKER, OracleDatabase, QueryFailed
from datalab.data.oracle_errors import EXPLANATIONS
from datalab.data.sqlcheck import SqlRejected, check_sql

pytestmark = pytest.mark.oracle

PASSWORD = os.environ.get("DATALAB_ORACLE_PASSWORD", "")
LOB_ERRORS = {"ORA-00932", "ORA-22848", "ORA-22849"}
D = "IHS_2025.STG_SURVEYDICTIONARY"
B = "IHS_2025.VW_BASELINE_SURVEY"
FIXED = "TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000))"


@pytest.fixture(scope="module")
def oracle() -> Iterator[tuple[oracledb.Cursor, dict]]:
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    connection = oracledb.connect(
        user=PRACTICE_ORACLE.user, password=PASSWORD, dsn=PRACTICE_ORACLE.dsn
    )
    with connection:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {SYNTHETIC_MARKER}")
        cursor.execute(f"SET ROLE {', '.join(PRACTICE_ORACLE.read_only_roles)}")
        catalog = Catalog.from_database(connection, sorted(PRACTICE_ORACLE.allowed_schemas))
        yield cursor, catalog.column_index()


def check(columns: dict, sql: str):
    return check_sql(sql, allowed_schemas=PRACTICE_ORACLE.allowed_schemas, columns=columns)


def run(cursor: oracledb.Cursor, sql: str) -> str | None:
    """None if Oracle runs it, else its error code."""
    try:
        cursor.execute(sql)
        cursor.fetchmany(5)
    except oracledb.DatabaseError as error:
        return getattr(error.args[0], "full_code", None) or str(error).split(":")[0]
    return None


def test_the_synthetic_database_has_what_these_tests_need(oracle):
    _, columns = oracle
    assert columns["IHS_2025"]["STG_SURVEYDICTIONARY"]["QUESTIONTEXT"] == "CLOB"
    assert "Bdate" in columns["IHS_2025"]["VW_BASELINE_SURVEY"], (
        "an older synthetic database: synthetic/db.sh generate loads the current one"
    )


# (refused query, its correction): the correction must pass and run.
LONG_TEXT = [
    (
        f"SELECT DISTINCT SURVEYNAME, RESULTIDENTIFIER, SUBSTR(QUESTIONTEXT, 1, 500) AS QTEXT "
        f"FROM {D} WHERE SURVEYNAME LIKE '%a%' ORDER BY SURVEYNAME, RESULTIDENTIFIER",
        f"SELECT DISTINCT SURVEYNAME, RESULTIDENTIFIER, {FIXED} AS QTEXT "
        f"FROM {D} WHERE SURVEYNAME LIKE '%a%' ORDER BY SURVEYNAME, RESULTIDENTIFIER",
    ),
    (
        f"SELECT SURVEYNAME, RESULTIDENTIFIER, MIN(QUESTIONTEXT) FROM {D} "
        "GROUP BY SURVEYNAME, RESULTIDENTIFIER",
        f"SELECT SURVEYNAME, RESULTIDENTIFIER, MIN({FIXED}) FROM {D} "
        "GROUP BY SURVEYNAME, RESULTIDENTIFIER",
    ),
    (f"SELECT DISTINCT QUESTIONTEXT FROM {D}", f"SELECT DISTINCT {FIXED} FROM {D}"),
    (f"SELECT DISTINCT UPPER(QUESTIONTEXT) FROM {D}", f"SELECT DISTINCT UPPER({FIXED}) FROM {D}"),
    (
        f"SELECT COUNT(*) FROM {D} GROUP BY QUESTIONTEXT",
        f"SELECT COUNT(*) FROM {D} GROUP BY {FIXED}",
    ),
    (
        f"SELECT SURVEYNAME FROM {D} ORDER BY QUESTIONTEXT",
        f"SELECT SURVEYNAME FROM {D} ORDER BY {FIXED}",
    ),
    (f"SELECT QUESTIONTEXT FROM {D} ORDER BY 1", f"SELECT {FIXED} FROM {D} ORDER BY 1"),
    (
        f"SELECT ROW_NUMBER() OVER (PARTITION BY QUESTIONTEXT ORDER BY SURVEYNAME) FROM {D}",
        f"SELECT ROW_NUMBER() OVER (PARTITION BY {FIXED} ORDER BY SURVEYNAME) FROM {D}",
    ),
    (
        f"SELECT QUESTIONTEXT FROM {D} UNION SELECT ANSWERCHOICES FROM {D}",
        f"SELECT QUESTIONTEXT FROM {D} UNION ALL SELECT ANSWERCHOICES FROM {D}",
    ),
    (
        f"SELECT QUESTIONTEXT FROM {D} MINUS SELECT ANSWERCHOICES FROM {D}",
        f"SELECT {FIXED} FROM {D} MINUS SELECT TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000)) FROM {D}",
    ),
    (f"SELECT MAX(QUESTIONTEXT) FROM {D}", f"SELECT MAX({FIXED}) FROM {D}"),
    (
        f"SELECT COUNT(QUESTIONTEXT) FROM {D}",
        f"SELECT COUNT(LENGTH(QUESTIONTEXT)) FROM {D}",
    ),
    (
        f"SELECT COUNT(DISTINCT QUESTIONTEXT) FROM {D}",
        f"SELECT COUNT(DISTINCT {FIXED}) FROM {D}",
    ),
    (
        f"SELECT LAG(QUESTIONTEXT) OVER (ORDER BY SURVEYNAME) FROM {D}",
        f"SELECT LAG({FIXED}) OVER (ORDER BY SURVEYNAME) FROM {D}",
    ),
    (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT = 'x'", f"SELECT 1 FROM {D} WHERE {FIXED} = 'x'"),
    (
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT IN ('a', 'b')",
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT LIKE 'a' OR QUESTIONTEXT LIKE 'b'",
    ),
    (
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT BETWEEN 'a' AND 'b'",
        f"SELECT 1 FROM {D} WHERE {FIXED} BETWEEN 'a' AND 'b'",
    ),
    (
        f"SELECT 1 FROM {D} a JOIN {D} b ON a.SURVEYNAME = b.SURVEYNAME "
        "AND a.QUESTIONTEXT = b.QUESTIONTEXT",
        f"SELECT 1 FROM {D} a JOIN {D} b ON a.SURVEYNAME = b.SURVEYNAME "
        "AND TO_CHAR(SUBSTR(a.QUESTIONTEXT, 1, 1000)) = TO_CHAR(SUBSTR(b.QUESTIONTEXT, 1, 1000))",
    ),
    (
        f"SELECT DECODE(QUESTIONTEXT, 'x', 1, 0) FROM {D}",
        f"SELECT DECODE({FIXED}, 'x', 1, 0) FROM {D}",
    ),
    (
        f"WITH q AS (SELECT SUBSTR(QUESTIONTEXT, 1, 500) t FROM {D}) SELECT DISTINCT t FROM q",
        f"WITH q AS (SELECT {FIXED} t FROM {D}) SELECT DISTINCT t FROM q",
    ),
]


@pytest.mark.parametrize(("refused", "fixed"), LONG_TEXT)
def test_long_text_the_check_refuses_oracle_refuses(oracle, refused, fixed):
    cursor, columns = oracle
    with pytest.raises(SqlRejected):
        check(columns, refused)
    assert run(cursor, refused) in LOB_ERRORS
    check(columns, fixed)
    assert run(cursor, fixed) is None


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT SURVEYNAME, QUESTIONTEXT, ANSWERCHOICES FROM {D} ORDER BY SURVEYNAME",
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT LIKE '%a%' AND ANSWERCHOICES IS NOT NULL",
        f"SELECT 1 FROM {D} WHERE REGEXP_LIKE(QUESTIONTEXT, 'a', 'i')",
        f"SELECT DISTINCT LENGTH(QUESTIONTEXT), INSTR(QUESTIONTEXT, 'a') FROM {D}",
        f"SELECT DISTINCT TO_CHAR(QUESTIONTEXT) FROM {D}",
        f"SELECT DISTINCT INITCAP(SURVEYNAME), NVL2(QUESTIONTEXT, 'y', 'n') FROM {D}",
        f"SELECT NVL(QUESTIONTEXT, ANSWERCHOICES) FROM {D}",
        f"SELECT QUESTIONTEXT FROM {D} UNION ALL SELECT ANSWERCHOICES FROM {D}",
        f"SELECT q FROM (SELECT QUESTIONTEXT q, SURVEYNAME s FROM {D}) ORDER BY s",
        f"SELECT LISTAGG({FIXED}, '; ') WITHIN GROUP (ORDER BY SURVEYNAME) FROM {D} "
        "WHERE ROWNUM <= 3",
    ],
)
def test_long_text_the_check_allows_oracle_runs(oracle, sql):
    cursor, columns = oracle
    check(columns, sql)
    assert run(cursor, sql) is None


@pytest.mark.parametrize(
    ("refused", "fixed"),
    [
        (f"SELECT Bdate FROM {B}", f'SELECT "Bdate" FROM {B}'),
        (
            f"SELECT COUNT(*) FROM {B} WHERE EXTRACT(YEAR FROM bdate) < 1995",
            f'SELECT COUNT(*) FROM {B} WHERE EXTRACT(YEAR FROM "Bdate") < 1995',
        ),
        (f'SELECT "BDATE" FROM {B}', f'SELECT "Bdate" FROM {B}'),
        (f"SELECT interest0 FROM {B}", f'SELECT "interest0" FROM {B}'),
        (f'SELECT "participantidentifier" FROM {B}', f"SELECT participantidentifier FROM {B}"),
    ],
)
def test_spelling_the_check_refuses_oracle_refuses(oracle, refused, fixed):
    cursor, columns = oracle
    with pytest.raises(SqlRejected, match="double quotes"):
        check(columns, refused)
    assert run(cursor, refused) == "ORA-00904"
    check(columns, fixed)
    assert run(cursor, fixed) is None


def test_what_a_refusal_from_oracle_says(tmp_path: Path):
    """Through DataLab's own database code: the code and DataLab's words."""
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    database = OracleDatabase(PRACTICE_ORACLE, PASSWORD, QueryLimits())

    def refusal(sql: str) -> str:
        with pytest.raises(QueryFailed) as info:
            database.extract_to_csv(
                sql,
                {},
                tmp_path / "q.csv",
                max_rows=10,
                max_bytes=10**6,
                preview_rows=1,
                cancel=threading.Event(),
            )
        return str(info.value)

    lob = refusal(f"SELECT DISTINCT QUESTIONTEXT FROM {D}")
    assert lob.split(":")[0] in LOB_ERRORS and "TO_CHAR(SUBSTR(col, 1, 1000))" in lob
    spelled = refusal(f"SELECT Bdate FROM {B}")
    assert spelled.startswith("ORA-00904: a name in the query isn't a column Oracle can find")
    assert '"BDATE"' in spelled and "double quotes" in spelled
    # 23ai quotes the value that isn't a number; DataLab doesn't pass it on.
    number = refusal(f"SELECT TO_NUMBER(SURVEYNAME) FROM {D} WHERE SURVEYNAME IS NOT NULL")
    assert number == f"ORA-01722: {EXPLANATIONS['ORA-01722']}"
