"""Finding where in the SQL a check message points, and naming column types."""

from __future__ import annotations

from types import SimpleNamespace

import oracledb
import pytest

from datalab.data.oracle import _type_label
from datalab.data.sqlcheck import SqlRejected, check_sql
from datalab.playground import diagnose
from tests.conftest import COHORTS, sample_catalog


def marked(sql: str) -> str | None:
    """The text the check's refusal of `sql` points at."""
    with pytest.raises(SqlRejected) as caught:
        check_sql(sql, allowed_schemas=COHORTS, columns=sample_catalog().column_index())
    position = diagnose(sql, caught.value).position
    if position is None:
        return None
    assert position.line == position.end_line
    line = sql.split("\n")[position.line - 1]
    return line[position.column - 1 : position.end_column - 1]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT nope FROM IHS_2025.VFITBITDAILYDATA", "nope"),
        ("SELECT 1 FROM DUAL WHERE\n  UTL_HTTP.REQUEST('x') = 1", "UTL_HTTP.REQUEST"),
        ("SELECT X FROM IHS_2023.T", "IHS_2023.T"),
        ("SELECT 1 FROM T", "T"),
        ('SELECT "trackersteps" FROM IHS_2025.VFITBITDAILYDATA', '"trackersteps"'),
        ("SELECT TRACKERSTEPS, MYSTERY(1) FROM IHS_2025.VFITBITDAILYDATA", "MYSTERY"),
        ("SELECT SYS_CONTEXT('USERENV', 'IP') FROM DUAL", "SYS_CONTEXT"),
        ("UPDATE IHS_2025.T SET A = 1", "UPDATE"),
        ("SELECT FROM WHERE", "WHERE"),
        # A name inside a string is never the one marked.
        ("SELECT 'NOPE', nope FROM IHS_2025.VFITBITDAILYDATA", "nope"),
    ],
)
def test_the_problem_is_marked_where_it_is(sql, expected):
    assert marked(sql) == expected


def test_a_statement_level_problem_has_no_position():
    assert marked("SELECT 1 FROM DUAL; SELECT 2 FROM DUAL") is None


def test_positions_count_from_the_sql_as_typed():
    # Leading blank lines and a trailing semicolon don't shift the mark.
    assert marked("\n\n   SELECT FROM WHERE;") == "WHERE"


def column(kind, size=None, precision=None, scale=None, internal=None):
    return SimpleNamespace(
        type_code=kind, display_size=size, internal_size=internal, precision=precision, scale=scale
    )


def test_result_column_types_are_named_as_oracle_names_them():
    # VARCHAR2(64 CHAR) in UTF-8: 64 characters, 256 bytes.
    assert _type_label(column(oracledb.DB_TYPE_VARCHAR, 64, internal=256)) == "VARCHAR2(64)"
    assert _type_label(column(oracledb.DB_TYPE_NVARCHAR, 10, internal=20)) == "NVARCHAR2(10)"
    assert _type_label(column(oracledb.DB_TYPE_NUMBER, precision=10, scale=2)) == "NUMBER(10,2)"
    assert _type_label(column(oracledb.DB_TYPE_NUMBER, precision=5, scale=0)) == "NUMBER(5)"
    assert _type_label(column(oracledb.DB_TYPE_NUMBER, precision=0, scale=-127)) == "NUMBER"
    assert _type_label(column(oracledb.DB_TYPE_NUMBER, precision=126, scale=-127)) == "FLOAT(126)"
    assert _type_label(column(oracledb.DB_TYPE_BINARY_DOUBLE, 127)) == "BINARY_DOUBLE"
    assert _type_label(column(oracledb.DB_TYPE_LONG)) == "CLOB"
    assert _type_label(column(oracledb.DB_TYPE_LONG_RAW)) == "BLOB"
    assert _type_label(column(oracledb.DB_TYPE_DATE, 23)) == "DATE"
    assert _type_label(column(oracledb.DB_TYPE_TIMESTAMP, precision=0, scale=6)) == "TIMESTAMP(6)"
    assert (
        _type_label(column(oracledb.DB_TYPE_TIMESTAMP_TZ, precision=0, scale=6))
        == "TIMESTAMP(6) WITH TIME ZONE"
    )
