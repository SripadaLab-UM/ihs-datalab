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
from datalab.data.oracle import SYNTHETIC_MARKER, OracleDatabase, QueryFailed, QueryTimedOut
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


# Each function and form the check treats as refusing a CLOB, on its own:
# every one must fail on Oracle (from the code review).
REFUSED_FORMS = (
    [
        f"SELECT DISTINCT {form} FROM {D}"
        for form in (
            "LTRIM(QUESTIONTEXT)",
            "RTRIM(QUESTIONTEXT)",
            "TRIM(BOTH 'x' FROM QUESTIONTEXT)",
            "SUBSTRB(QUESTIONTEXT, 1, 5)",
            "SUBSTRC(QUESTIONTEXT, 1, 5)",
            "NLS_UPPER(QUESTIONTEXT)",
            "NLS_LOWER(QUESTIONTEXT)",
            "LPAD(QUESTIONTEXT, 10)",
            "RPAD(QUESTIONTEXT, 10)",
            "REPLACE(QUESTIONTEXT, 'a', 'b')",
            "REGEXP_REPLACE(QUESTIONTEXT, 'a', 'b')",
            "REGEXP_SUBSTR(QUESTIONTEXT, 'a')",
            "CONCAT(SURVEYNAME, QUESTIONTEXT)",
            "SURVEYNAME || QUESTIONTEXT",
            "NVL(QUESTIONTEXT, 'none')",
            "NVL2(SURVEYNAME, QUESTIONTEXT, SURVEYNAME)",
            "DECODE(SURVEYNAME, 'x', QUESTIONTEXT, SURVEYNAME)",
            "CASE WHEN SURVEYNAME = 'x' THEN QUESTIONTEXT END",
        )
    ]
    + [
        f"SELECT {call} FROM {D}"
        for call in (
            "LEAST(QUESTIONTEXT, 'x')",
            "NULLIF(SURVEYNAME, QUESTIONTEXT)",
            "STATS_MODE(QUESTIONTEXT)",
            "MEDIAN(QUESTIONTEXT)",
            "STDDEV(QUESTIONTEXT)",
            "VARIANCE(QUESTIONTEXT)",
            "SUM(QUESTIONTEXT)",
            "AVG(QUESTIONTEXT)",
            "APPROX_COUNT_DISTINCT(QUESTIONTEXT)",
            "FIRST_VALUE(QUESTIONTEXT) OVER (ORDER BY SURVEYNAME)",
            "LAST_VALUE(QUESTIONTEXT) OVER (ORDER BY SURVEYNAME)",
            "NTH_VALUE(QUESTIONTEXT, 1) OVER (ORDER BY SURVEYNAME)",
            "LEAD(QUESTIONTEXT) OVER (ORDER BY SURVEYNAME)",
            "CASE QUESTIONTEXT WHEN 'x' THEN 1 END",
            "CASE SURVEYNAME WHEN QUESTIONTEXT THEN 1 END",
            "MAX(SURVEYNAME) KEEP (DENSE_RANK FIRST ORDER BY QUESTIONTEXT)",
        )
    ]
    + [
        f"SELECT 1 FROM {D} WHERE SURVEYNAME IN (SELECT QUESTIONTEXT FROM {D})",
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT NOT IN ('x')",
        f"SELECT 1 FROM {D} WHERE QUESTIONTEXT <> 'x'",
        f"SELECT 1 FROM {D} a NATURAL JOIN {D} b",
        f"SELECT 1 FROM {D} a JOIN {D} b USING (QUESTIONTEXT)",
        f"SELECT LEVEL FROM {D} START WITH SURVEYNAME IS NULL "
        "CONNECT BY PRIOR QUESTIONTEXT = QUESTIONTEXT",
        f"SELECT COUNT(*) FROM {D} GROUP BY ROLLUP(QUESTIONTEXT)",
        f"SELECT QUESTIONTEXT FROM {D} INTERSECT SELECT ANSWERCHOICES FROM {D}",
        f"SELECT DISTINCT TO_CLOB(SURVEYNAME) FROM {D}",
    ]
)


@pytest.mark.parametrize("sql", REFUSED_FORMS)
def test_each_form_the_check_refuses_oracle_refuses(oracle, sql):
    cursor, columns = oracle
    with pytest.raises(SqlRejected):
        check(columns, sql)
    assert run(cursor, sql) in LOB_ERRORS


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
        # The first result decides the type (from the code review).
        f"SELECT DISTINCT NVL(SURVEYNAME, QUESTIONTEXT) FROM {D}",
        f"SELECT DISTINCT NVL2(SURVEYNAME, SURVEYNAME, QUESTIONTEXT) FROM {D}",
        f"SELECT DISTINCT DECODE(SURVEYNAME, 'x', SURVEYNAME, QUESTIONTEXT) FROM {D}",
        f"SELECT GREATEST(SURVEYNAME, QUESTIONTEXT) FROM {D}",
        f"SELECT DISTINCT TRANSLATE(QUESTIONTEXT, 'a', 'b'), INITCAP(QUESTIONTEXT) FROM {D}",
        f"SELECT 1 FROM {D} a JOIN {D} b USING (SURVEYNAME) WHERE ROWNUM <= 3",
        f"SELECT LEVEL FROM {D} START WITH QUESTIONTEXT LIKE 'x%' "
        "CONNECT BY NOCYCLE PRIOR SURVEYNAME = SURVEYNAME AND LEVEL < 2",
        f"SELECT COUNT(LENGTH(QUESTIONTEXT)), MAX(LENGTH(QUESTIONTEXT)) FROM {D}",
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


# Long text built in the query: 2,100 three-byte characters (6,300 bytes), and
# 4,500 one-byte ones. The synthetic dictionary's text is all short.
MULTIBYTE = (
    "(SELECT "
    + " || ".join(["TO_CLOB(TO_CHAR(UNISTR(RPAD('\\4e2d', 3500, '\\4e2d'))))"] * 3)
    + " c FROM DUAL)"
)
LONG_ASCII = "(SELECT TO_CLOB(RPAD('x', 4000, 'x')) || TO_CLOB(RPAD('y', 500, 'y')) c FROM DUAL)"


def test_the_1000_character_conversion_is_safe_for_any_text(oracle):
    """What the check's message and the guidance say (external review: no
    silent truncation): 1,000 characters always fit Oracle's 4,000 bytes;
    more may not; a cut shows in LENGTH."""
    cursor, _ = oracle
    cursor.execute(f"SELECT LENGTH(c), LENGTHB(TO_CHAR(SUBSTR(c, 1, 1000))) FROM {MULTIBYTE}")
    assert cursor.fetchone() == (2100, 3000)
    assert run(cursor, f"SELECT DISTINCT TO_CHAR(SUBSTR(c, 1, 1000)) FROM {MULTIBYTE}") is None
    assert run(cursor, f"SELECT DISTINCT TO_CHAR(SUBSTR(c, 1, 1500)) FROM {MULTIBYTE}") == (
        "ORA-64203"
    )
    assert run(cursor, f"SELECT DISTINCT TO_CHAR(c) FROM {LONG_ASCII}") == "ORA-22835"
    cursor.execute(
        f"SELECT LENGTH(c), LENGTH(TO_CHAR(SUBSTR(c, 1, 1000))), "
        f"CASE WHEN LENGTH(c) > 1000 THEN 1 ELSE 0 END FROM {LONG_ASCII}"
    )
    assert cursor.fetchone() == (4500, 1000, 1)  # cut, and it shows


def test_blobs_as_the_message_says(oracle):
    """The synthetic database has no BLOB column, so one is made in the
    query (TO_BLOB, which the check itself wouldn't allow): DISTINCT, ORDER
    BY, = and MAX fail; LENGTH and IS NULL work."""
    cursor, _ = oracle
    blob = "(SELECT TO_BLOB(HEXTORAW('ABCD')) b FROM DUAL)"
    for sql in (
        f"SELECT DISTINCT b FROM {blob}",
        f"SELECT 1 FROM {blob} ORDER BY b",
        f"SELECT MAX(b) FROM {blob}",
        f"SELECT 1 FROM {blob} WHERE b = HEXTORAW('AB')",
    ):
        assert run(cursor, sql) in LOB_ERRORS, sql
    assert run(cursor, f"SELECT LENGTH(b) FROM {blob} WHERE b IS NOT NULL") is None


def test_a_round_trip_longer_than_the_call_timeout_is_a_timeout(tmp_path: Path):
    """DPY-4024 (one round trip over round_trip_timeout_seconds) is a timeout,
    with its reason and limit, not a generic failure (external review)."""
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    database = OracleDatabase(
        PRACTICE_ORACLE, PASSWORD, QueryLimits(round_trip_timeout_seconds=1, deadline_seconds=60)
    )
    slow = "SELECT COUNT(*) FROM all_objects a CROSS JOIN all_objects b CROSS JOIN all_objects c"
    with pytest.raises(QueryTimedOut) as info:
        database.extract_to_csv(
            slow,
            {},
            tmp_path / "q.csv",
            max_rows=10,
            max_bytes=10**6,
            preview_rows=1,
            cancel=threading.Event(),
        )
    assert info.value.reason == "call_timeout" and info.value.limit_seconds == 1
    assert info.value.code == "DPY-4024" and info.value.category == "timeout"
    assert str(info.value).startswith(
        "Oracle took longer than 1 s to answer one request (DPY-4024)"
    )
