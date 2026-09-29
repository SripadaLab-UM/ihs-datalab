"""The SQL examples in the agent's instructions pass DataLab's SQL check and
run on the synthetic database (Oracle Free; production is 19c, and the
examples use only 19c built-ins).

Run with `uv run pytest -m oracle` (DATALAB_ORACLE_PASSWORD, synthetic/README.md).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import oracledb
import pytest

from datalab.config import PRACTICE_ORACLE
from datalab.data.catalog import Catalog
from datalab.data.oracle import SYNTHETIC_MARKER
from datalab.data.sqlcheck import check_sql

pytestmark = pytest.mark.oracle

SKILL = Path(__file__).parents[2] / "images/agent/skills/sql-extraction/SKILL.md"
EXAMPLES = re.findall(r"```sql\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.S)


def test_the_skill_has_its_correctness_examples():
    assert len(EXAMPLES) >= 2


@pytest.mark.parametrize("sql", EXAMPLES)
def test_a_skill_example_passes_the_check_and_runs(sql):
    password = os.environ.get("DATALAB_ORACLE_PASSWORD", "")
    if not password:
        pytest.skip("Set DATALAB_ORACLE_PASSWORD for the synthetic database")
    with oracledb.connect(
        user=PRACTICE_ORACLE.user, password=password, dsn=PRACTICE_ORACLE.dsn
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {SYNTHETIC_MARKER}")
        cursor.execute(f"SET ROLE {', '.join(PRACTICE_ORACLE.read_only_roles)}")
        columns = Catalog.from_database(
            connection, sorted(PRACTICE_ORACLE.allowed_schemas)
        ).column_index()
        check_sql(sql, allowed_schemas=PRACTICE_ORACLE.allowed_schemas, columns=columns)
        cursor.execute(sql)
        assert cursor.fetchall()
