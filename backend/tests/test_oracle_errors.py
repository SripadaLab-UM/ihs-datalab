"""What a query Oracle refused says: the code and DataLab's own words."""

import oracledb
import pytest
from oracledb import errors as oracledb_errors

from datalab.data.oracle_errors import EXPLANATIONS, category, explain, oracle_code


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


def test_other_errors_show_only_their_code():
    # From the re-review: 23ai-style text can quote values, so it isn't shown.
    said = explain(error("ORA-01555: snapshot too old: rollback segment 'X1' of 42 bytes\nmore"))
    assert said == (
        "ORA-01555: Oracle refused the query. DataLab has no explanation of this code, and "
        "doesn't show Oracle's own text for it (it can quote data)."
    )
    bind = explain(error('DPY-4010: a bind variable replacement value for placeholder ":X"'))
    assert bind.startswith("DPY-4010: Oracle refused the query") and ":X" not in bind
    assert oracle_code(error("ORA-00942: table or view does not exist")) == "ORA-00942"


def test_a_parallel_query_error_wrapping_an_unexplained_code_names_no_host():
    wrapped = error(
        "ORA-12801: error signaled in parallel query server P001, instance dbhost01:IHSPROD1 (1)\n"
        'ORA-01555: snapshot too old: rollback segment number 7 with name "_SYSSMU7$" too small'
    )
    said = explain(wrapped)
    assert said.startswith("ORA-01555: Oracle refused the query (inside ORA-12801")
    for leak in ("dbhost01", "IHSPROD1", "P001", "SYSSMU7"):
        assert leak not in said


@pytest.mark.parametrize(
    ("code", "says"),
    [
        ("ORA-01017", "the saved password was refused"),
        ("ORA-28000", "the account is locked"),
        ("ORA-28001", "password has expired"),
    ],
)
def test_a_refused_sign_in_says_so_not_the_network(code, says):
    said = explain(error(f"{code}: invalid credential or not authorized; logon denied"))
    assert said.startswith(f"DataLab couldn't sign in to the database ({code}): ") and says in said
    assert "VPN" not in said and category(code) == "connection"


@pytest.mark.parametrize("code", ["ORA-00028", "ORA-01012", "ORA-02396", "ORA-01089"])
def test_a_session_ended_while_running_is_a_connection_failure(code):
    assert category(code) == "connection"
    assert explain(error(f"{code}: your session has been killed")).startswith(
        f"The database ended DataLab's session ({code})"
    )


def test_a_parallel_query_error_is_explained_by_the_one_it_wraps():
    # Production's first line names the parallel server's host and SID.
    wrapped = error(
        "ORA-12801: error signaled in parallel query server P001, instance dbhost01:IHSPROD1\n"
        "ORA-00932: inconsistent datatypes: expected - got CLOB"
    )
    said = explain(wrapped)
    assert said.startswith("ORA-00932: inconsistent data types")
    assert "dbhost01" not in said and "IHSPROD1" not in said and "P001" not in said
    alone = explain(error("ORA-12801: error signaled in parallel query server P001, instance h:S"))
    assert alone == "ORA-12801: a parallel query server failed. Try again, or narrow the query."
    assert category(oracle_code(wrapped)) == "sql"


@pytest.mark.parametrize(
    "message",
    [
        "DPY-6005: cannot connect to database (CONNECTION_ID=x). [Errno 61] dbhost01:1521",
        "ORA-12514: Cannot connect to database. Service IHSPROD is not registered with the "
        "listener at host 10.1.2.3 port 1521.",
        "ORA-03113: end-of-file on communication channel\nProcess ID: 1\nSession ID: 2",
    ],
)
def test_not_reaching_the_database_names_no_host(message):
    said = explain(error(message))
    code = message.split(":")[0]
    assert said == (
        f"DataLab couldn't reach the database ({code}). Check the network or VPN "
        "connection, then try again."
    )
    assert category(code) == "connection"


def test_categories():
    assert category("ORA-00942") == "permission"
    assert category("DPY-4024") == "timeout"
    assert category("ORA-12899") == "sql"  # a value too long, not the network
    assert category("ORA-00932") == "sql"
    assert category(None) == "sql"


def test_the_identifier_must_be_a_whole_name_in_the_query():
    # "BDATE" is in "BDATES_TABLE" as a substring, not as a name.
    said = explain(error('ORA-00904: "BDATE": invalid identifier'), "SELECT BDATES_TABLE FROM T")
    assert '"BDATE"' not in said
    quoted = explain(
        error('ORA-00904: "T"."Bdate": invalid identifier'), 'SELECT t."Bdate" FROM T t'
    )
    assert '"T"."Bdate"' in quoted


def test_long_text_conversion_errors():
    for code in ("ORA-22835", "ORA-64203"):
        said = explain(error(f"{code}: Buffer too small (actual: 4500, maximum: 4000)"))
        assert "1,000 characters" in said and "4500" not in said
