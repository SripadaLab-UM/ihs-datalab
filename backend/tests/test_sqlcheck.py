import pytest

from datalab.data.sqlcheck import SqlRejected, TableRef, check_sql

COHORTS = frozenset({"IHS_2024", "IHS_2025", "IHS_2026"})


def ok(sql: str):
    return check_sql(sql, allowed_schemas=COHORTS)


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
        assert "allowed list" in rejected("SELECT MY_CUSTOM_FN(a) FROM IHS_2025.T")

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
