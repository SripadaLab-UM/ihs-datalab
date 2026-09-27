"""The SQL check's function list, against a real Oracle: the synthetic database.

The check allows Oracle's built-in SQL functions by name. That is safe only if
an unqualified built-in name always runs the built-in, so these tests try to
take each name over, in the two ways an account could: a same-named function
in the session user's own schema, and a public synonym. Each decoy returns
'HIJACKED'; the call must give the same result with and without it.

Run with `uv run pytest -m oracle`. Needs SYSTEM on the synthetic database
(dev-only password, synthetic/README.md) and refuses any other database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import oracledb
import pytest

from datalab.config import PRACTICE_ORACLE
from datalab.data.oracle import SYNTHETIC_MARKER
from datalab.data.sqlcheck import ORACLE_FUNCTIONS, PSEUDO_COLUMNS

from .sql_samples import ROWS, SAMPLE_CALLS

pytestmark = pytest.mark.oracle

PASSWORD = os.environ.get("DATALAB_ORACLE_PASSWORD", "")
# Dev-only: the synthetic container's SYSTEM password (synthetic/db.sh).
ADMIN_PASSWORD = os.environ.get("SYNTH_ORACLE_PWD", "SynthDev2026")

# Plain CREATE, never OR REPLACE: a real object of the same name makes the test
# fail rather than be replaced (and then dropped).
DECOY = """CREATE FUNCTION {owner}."{name}"(
  p1 VARCHAR2 DEFAULT NULL, p2 VARCHAR2 DEFAULT NULL, p3 VARCHAR2 DEFAULT NULL,
  p4 VARCHAR2 DEFAULT NULL, p5 VARCHAR2 DEFAULT NULL, p6 VARCHAR2 DEFAULT NULL)
  RETURN VARCHAR2 AS BEGIN RETURN 'HIJACKED'; END;"""


@pytest.fixture(scope="module")
def sessions() -> Iterator[tuple[oracledb.Cursor, oracledb.Cursor]]:
    if not PASSWORD:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    ro = oracledb.connect(user=PRACTICE_ORACLE.user, password=PASSWORD, dsn=PRACTICE_ORACLE.dsn)
    user = ro.cursor()
    # Never create decoys anywhere but the synthetic database.
    user.execute(f"SELECT COUNT(*) FROM {SYNTHETIC_MARKER}")
    user.execute(f"SET ROLE {', '.join(PRACTICE_ORACLE.read_only_roles)}")
    admin_connection = oracledb.connect(
        user="SYSTEM", password=ADMIN_PASSWORD, dsn=PRACTICE_ORACLE.dsn
    )
    admin = admin_connection.cursor()
    admin.execute(DECOY.format(owner="SYSTEM", name="DL_DECOY"))
    try:
        admin.execute("GRANT EXECUTE ON SYSTEM.DL_DECOY TO PUBLIC")
        yield admin, user
    finally:
        admin.execute("DROP FUNCTION SYSTEM.DL_DECOY")
        admin_connection.close()
        ro.close()


def call(user: oracledb.Cursor, sql: str) -> tuple[str, str]:
    try:
        user.execute(sql)
        row = user.fetchone()
        value = row[0] if row else None
        return ("ok", str(value.read() if hasattr(value, "read") else value))
    except oracledb.DatabaseError as error:
        return ("error", str(error).splitlines()[0])


def results(admin: oracledb.Cursor, user: oracledb.Cursor, name: str, sql: str):
    """The call's result as it is, with a same-named function in the user's
    schema, and with a same-named public synonym."""
    baseline = call(user, sql)
    admin.execute(DECOY.format(owner=PRACTICE_ORACLE.user, name=name))
    try:
        own = call(user, sql)
    finally:
        admin.execute(f'DROP FUNCTION {PRACTICE_ORACLE.user}."{name}"')
    admin.execute(f'CREATE PUBLIC SYNONYM "{name}" FOR SYSTEM.DL_DECOY')
    try:
        public = call(user, sql)
    finally:
        admin.execute(f'DROP PUBLIC SYNONYM "{name}"')
    return baseline, own, public


def test_a_name_that_isnt_built_in_can_be_taken_over(sessions):
    """The control: without it, the test below could pass by testing nothing."""
    admin, user = sessions
    sql = "SELECT DL_NOT_BUILT_IN('x') FROM DUAL"
    _, own, public = results(admin, user, "DL_NOT_BUILT_IN", sql)
    assert own == public == ("ok", "HIJACKED")


@pytest.mark.parametrize("name", sorted(ORACLE_FUNCTIONS))
def test_built_in_names_cant_be_taken_over(sessions, name):
    admin, user = sessions
    baseline, own, public = results(admin, user, name, f"SELECT {SAMPLE_CALLS[name]} FROM {ROWS}")
    assert baseline[0] == "ok", f"the sample call for {name} doesn't run: {baseline[1]}"
    assert own == baseline
    assert public == baseline


@pytest.mark.parametrize("name", sorted(PSEUDO_COLUMNS))
def test_pseudo_columns_cant_be_taken_over(sessions, name):
    """Bare names the check lets through without a catalog column."""
    admin, user = sessions
    sql = f"SELECT COUNT({name}) FROM DUAL CONNECT BY NOCYCLE LEVEL <= 2"
    baseline, own, public = results(admin, user, name, sql)
    assert baseline[0] == "ok", baseline[1]
    assert own == baseline
    assert public == baseline
