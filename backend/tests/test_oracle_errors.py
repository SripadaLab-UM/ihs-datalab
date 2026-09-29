"""What a query Oracle refused says: the code and DataLab's own words."""

import oracledb
import pytest
from oracledb import errors as oracledb_errors

from datalab.data.oracle_errors import EXPLANATIONS, explain, oracle_code


def error(message: str) -> oracledb.DatabaseError:
    return oracledb.DatabaseError(oracledb_errors._Error(message))


def test_the_code_and_datalabs_explanation_not_oracles_text():
    # 23ai quotes the value that isn't a number: it must not be shown.
    said = explain(
        error("ORA-01722: unable to convert string value containing 'P-00123' to a number")
    )
    assert said.startswith("ORA-01722: a text value couldn't be converted to a number")
    assert "P-00123" not in said


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_every_explained_code(code):
    said = explain(error(f"{code}: text from the server db01.example.org\nHelp: https://x"))
    assert said.startswith(f"{code}: ")
    assert "db01" not in said and "Help" not in said


def test_a_clob_in_distinct():
    said = explain(error("ORA-00932: inconsistent datatypes: expected - got CLOB"))
    assert "CLOB" in said and "TO_CHAR(SUBSTR(col, 1, 1000))" in said
    assert "TO_CHAR(SUBSTR" in explain(error("ORA-22848: cannot use CLOB type as comparison key"))


def test_an_invalid_identifier_is_named_only_when_the_query_wrote_it():
    sql = "SELECT Bdate FROM IHS_2025.VW_BASELINE_SURVEY"
    said = explain(error('ORA-00904: "BDATE": invalid identifier'), sql)
    assert said.startswith(
        'ORA-00904: a name in the query isn\'t a column Oracle can find: "BDATE".'
    )
    assert "double quotes" in said
    other = explain(error('ORA-00904: "SECRET_THING": invalid identifier'), sql)
    assert "SECRET_THING" not in other and other.startswith("ORA-00904: a name in the query")


def test_other_errors_keep_their_first_line():
    assert explain(error("ORA-01555: snapshot too old\nmore")) == "ORA-01555: snapshot too old"
    assert explain(error("DPY-4010: a bind variable replacement value")) == (
        "DPY-4010: a bind variable replacement value"
    )
    assert oracle_code(error("ORA-00942: table or view does not exist")) == "ORA-00942"
