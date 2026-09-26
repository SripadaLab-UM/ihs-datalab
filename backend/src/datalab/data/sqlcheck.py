"""Decide whether a SQL statement may run against the research database.

This is the second line of defence. The first is the database session itself,
which runs with only read-only roles enabled (see docs/SAFETY.md). The check
parses the SQL properly with sqlglot rather than pattern-matching, and fails
closed: anything it does not positively recognise as a plain read is refused.

Beyond "no writes", it blocks ways a SELECT could reach outside the database,
such as package calls (UTL_HTTP.REQUEST, DBMS_*), URI types, XQuery, and
database links, because those would let the database server send data out.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_SQL_BYTES = 64 * 1024

# Oracle functions sqlglot doesn't model as typed functions, and that are safe
# to call. Anything else it parses as an unknown ("anonymous") function is
# refused, so a user-defined or system function can't slip through.
ALLOWED_UNTYPED_FUNCTIONS = frozenset(
    {
        "TRUNC",
        "NUMTODSINTERVAL",
        "NUMTOYMINTERVAL",
        "TO_DSINTERVAL",
        "TO_YMINTERVAL",
        "FROM_TZ",
        "SYS_EXTRACT_UTC",
        "TZ_OFFSET",
        "RATIO_TO_REPORT",
        "STATS_MODE",
        "WIDTH_BUCKET",
        "BITAND",
        "REMAINDER",
        "HEXTORAW",
        "RAWTOHEX",
        "ORA_HASH",
        "JSON_VALUE",
        "JSON_QUERY",
    }
)

# Typed expressions that are never allowed, even inside a SELECT.
_FORBIDDEN_NODES: tuple[type[exp.Expr], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,
    exp.Lock,
    exp.XMLTable,
)

_SELECT_ROOTS: tuple[type[exp.Expr], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)

_SEQUENCE_PSEUDOCOLUMNS = frozenset({"NEXTVAL", "CURRVAL"})


class SqlRejected(ValueError):
    """The statement may not run. The message is written for the agent or user."""


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class CheckedSql:
    sql: str
    tables: tuple[TableRef, ...]
    binds: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def schemas(self) -> frozenset[str]:
        return frozenset(t.schema for t in self.tables)


def check_sql(sql: str, *, allowed_schemas: frozenset[str]) -> CheckedSql:
    """Return what the statement reads, or raise SqlRejected explaining why not."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SqlRejected("The query is empty.")
    if len(sql.encode()) > MAX_SQL_BYTES:
        raise SqlRejected(f"The query is longer than {MAX_SQL_BYTES // 1024} KB.")

    statement = _parse_single_statement(sql)
    if not isinstance(statement, _SELECT_ROOTS):
        raise SqlRejected("Only SELECT queries (optionally with WITH) are allowed.")

    _reject_forbidden_constructs(statement)
    tables = _referenced_tables(statement, allowed_schemas)
    binds = tuple(sorted({p.name for p in statement.find_all(exp.Placeholder) if p.name}))
    return CheckedSql(sql=sql, tables=tables, binds=binds, warnings=_warnings(statement))


def _parse_single_statement(sql: str) -> exp.Expr:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="oracle") if s is not None]
    except ParseError as error:
        first_line = str(error).splitlines()[0]
        raise SqlRejected(f"The query couldn't be parsed as Oracle SQL: {first_line}") from error
    if len(statements) != 1:
        raise SqlRejected("Send exactly one statement at a time.")
    return statements[0]


def _reject_forbidden_constructs(statement: exp.Expr) -> None:
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRejected(f"{node.key.upper()} isn't allowed; queries may only read data.")
        if isinstance(node, exp.Select) and (node.args.get("locks") or node.args.get("into")):
            raise SqlRejected("SELECT ... FOR UPDATE and SELECT ... INTO aren't allowed.")
        if isinstance(node, exp.Dot) and _contains_function_call(node):
            raise SqlRejected(
                "Calling package or object functions isn't allowed: "
                + node.sql(dialect="oracle")[:80]
            )
        if isinstance(node, exp.Anonymous) and node.name.upper() not in ALLOWED_UNTYPED_FUNCTIONS:
            raise SqlRejected(
                f"The function {node.name.upper()} isn't on the allowed list. "
                "Standard Oracle functions are fine; ask the DataLab maintainer "
                "to add one if needed."
            )
        if isinstance(node, exp.Column) and node.name.upper() in _SEQUENCE_PSEUDOCOLUMNS:
            raise SqlRejected("Using sequences (NEXTVAL/CURRVAL) isn't allowed.")


def _contains_function_call(node: exp.Dot) -> bool:
    return any(isinstance(n, exp.Func) for n in node.walk())


def _referenced_tables(
    statement: exp.Expr, allowed_schemas: frozenset[str]
) -> tuple[TableRef, ...]:
    cte_names = {cte.alias_or_name.upper() for cte in statement.find_all(exp.CTE)}
    refs: set[TableRef] = set()
    for table in statement.find_all(exp.Table):
        name = table.name
        if "@" in name or table.args.get("catalog"):
            raise SqlRejected("Database links and cross-database references aren't allowed.")
        schema = table.db.upper()
        if not schema:
            if name.upper() in cte_names or name.upper() == "DUAL":
                continue
            raise SqlRejected(
                f"Qualify every table with its cohort schema, for example IHS_2025.{name.upper()}."
            )
        if schema not in allowed_schemas:
            raise SqlRejected(f"The schema {schema} isn't available to DataLab.")
        refs.add(TableRef(schema=schema, name=name.upper()))
    return tuple(sorted(refs, key=str))


def _warnings(statement: exp.Expr) -> tuple[str, ...]:
    warnings: list[str] = []
    if any(True for _ in statement.find_all(exp.Star)):
        warnings.append("SELECT * returns every column; list the columns you need.")
    return tuple(warnings)
