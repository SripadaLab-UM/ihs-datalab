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

import re
from collections.abc import Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, find_all_in_scope, traverse_scope
from sqlglot.schema import MappingSchema
from sqlglot.tokenizer_core import Token, TokenType

from datalab.data.sql_lobs import POSITION, lob_problem, sql_name
from datalab.textcheck import lone_surrogate, size_text

MAX_SQL_BYTES = 64 * 1024

# Oracle's built-in SQL functions (SQL Language Reference, "Functions"), less
# the ones below that can reach outside the database or describe its setup.
# sqlglot models many of these as typed functions; anything it parses as an
# unknown ("anonymous") function must be on this list, so a user-defined or
# system function can't slip through.
#
# Allowing built-in names is safe because an unqualified built-in name always
# runs the built-in: Oracle doesn't let a same-named function in the session's
# schema, or a public synonym, take its place. Tested for every name here on
# the synthetic database (tests/test_sqlcheck_oracle.py), which runs 23ai. The
# production database runs 19c (19.32), so every name here must be a built-in
# in 19c: checked against the 19c SQL Language Reference. Newer built-ins
# (ANY_VALUE, KURTOSIS_*, SKEWNESS_*) are left out: in 19c they aren't
# built-ins, so a same-named function could run.
ORACLE_FUNCTIONS = frozenset(
    """
    ABS ACOS ASIN ATAN ATAN2 BITAND CEIL COS COSH EXP FLOOR LN LOG MOD NANVL
    POWER REMAINDER ROUND SIGN SIN SINH SQRT TAN TANH TRUNC WIDTH_BUCKET

    CHR CONCAT INITCAP LOWER LPAD LTRIM NCHR NLS_INITCAP NLS_LOWER NLS_UPPER
    NLSSORT REGEXP_REPLACE REGEXP_SUBSTR REPLACE RPAD RTRIM SOUNDEX SUBSTR
    SUBSTRB SUBSTRC TRANSLATE TRIM UPPER ASCII INSTR INSTRB INSTRC LENGTH
    LENGTHB LENGTHC REGEXP_COUNT REGEXP_INSTR

    ADD_MONTHS CURRENT_DATE CURRENT_TIMESTAMP EXTRACT FROM_TZ LAST_DAY
    LOCALTIMESTAMP MONTHS_BETWEEN NEW_TIME NEXT_DAY NUMTODSINTERVAL
    NUMTOYMINTERVAL SYS_EXTRACT_UTC SYSDATE SYSTIMESTAMP TO_CHAR TO_DSINTERVAL
    TO_TIMESTAMP TO_TIMESTAMP_TZ TO_YMINTERVAL TZ_OFFSET GREATEST LEAST

    ASCIISTR BIN_TO_NUM CAST COMPOSE CONVERT DECOMPOSE HEXTORAW RAWTOHEX
    RAWTONHEX TO_BINARY_DOUBLE TO_BINARY_FLOAT TO_CLOB TO_DATE TO_MULTI_BYTE
    TO_NCHAR TO_NCLOB TO_NUMBER TO_SINGLE_BYTE UNISTR VALIDATE_CONVERSION

    DECODE ORA_HASH STANDARD_HASH VSIZE COALESCE NULLIF NVL NVL2
    SYS_CONNECT_BY_PATH

    JSON_VALUE JSON_QUERY JSON_OBJECT JSON_ARRAY JSON_SERIALIZE JSON_OBJECTAGG
    JSON_ARRAYAGG

    AVG COUNT MAX MIN SUM MEDIAN STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VAR_POP
    VAR_SAMP CORR CORR_S CORR_K COVAR_POP COVAR_SAMP REGR_SLOPE REGR_INTERCEPT
    REGR_R2 REGR_COUNT REGR_AVGX REGR_AVGY REGR_SXX REGR_SYY REGR_SXY
    APPROX_COUNT_DISTINCT APPROX_MEDIAN APPROX_PERCENTILE
    STATS_MODE STATS_BINOMIAL_TEST STATS_CROSSTAB STATS_F_TEST STATS_KS_TEST
    STATS_MW_TEST STATS_ONE_WAY_ANOVA STATS_T_TEST_ONE STATS_T_TEST_PAIRED
    STATS_T_TEST_INDEP STATS_T_TEST_INDEPU STATS_WSR_TEST LISTAGG
    GROUPING GROUPING_ID GROUP_ID PERCENTILE_CONT PERCENTILE_DISC

    ROW_NUMBER RANK DENSE_RANK NTILE LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE
    CUME_DIST PERCENT_RANK RATIO_TO_REPORT

    REGEXP_LIKE JSON_EXISTS LNNVL
    """.split()  # noqa: SIM905 (grouped by kind, as in the Oracle manual)
)

# Never allowed, even where sqlglot models them: these can fetch URLs or files
# from the database server (XML, URI types, BFILENAME), describe the server
# and its network (SYS_CONTEXT, USERENV), or reach object internals.
DENIED_FUNCTIONS = frozenset(
    """
    SYS_CONTEXT USERENV DUMP BFILENAME HTTPURITYPE DBURITYPE XDBURITYPE URITYPE
    EXTRACTVALUE EXISTSNODE UPDATEXML DEREF REF MAKE_REF VALUE TREAT CURSOR
    SCN_TO_TIMESTAMP TIMESTAMP_TO_SCN SYS_TYPEID ORA_INVOKING_USER
    ORA_INVOKING_USERID SYS_GUID
    """.split()  # noqa: SIM905 (grouped by kind, as in the Oracle manual)
)
_DENIED_PREFIXES = ("XML", "SYS_XML", "DBMS_", "UTL_")

# Words that are followed by "(" in SQL without being a function call.
_PAREN_KEYWORDS = frozenset(
    """
    SELECT FROM JOIN ON USING WHERE AND OR NOT IN EXISTS ANY ALL SOME LIKE BETWEEN
    CASE WHEN THEN ELSE AS OVER GROUP BY KEEP PARTITION ORDER HAVING DISTINCT
    UNION INTERSECT MINUS EXCEPT ROLLUP CUBE SETS FOR PRIOR START
    CONNECT TABLE LATERAL
    """.split()  # noqa: SIM905 (a word list reads better)
) | {
    # Clauses the tokenizer reads as one token.
    "GROUPING SETS",
    "ORDER BY",
    "GROUP BY",
    "PARTITION BY",
    "CONNECT BY",
    "ORDER SIBLINGS BY",
    "START WITH",
    "WITH",
}
# Types (CAST(x AS NUMBER(10, 2)), RETURNING VARCHAR2(100)) and interval
# precisions (INTERVAL '5' DAY(3) TO SECOND(2)): allowed only right after the
# word that introduces them, so a database function named YEAR can't hide as one.
_TYPE_WORDS = frozenset(
    """
    NUMBER VARCHAR2 VARCHAR NVARCHAR2 CHAR NCHAR RAW FLOAT DECIMAL DEC NUMERIC
    TIMESTAMP DAY HOUR MINUTE SECOND YEAR MONTH
    """.split()  # noqa: SIM905 (a word list reads better)
)
_TYPE_INTRODUCERS = frozenset({"AS", "RETURNING", "TO", "INTERVAL"})

# Oracle pseudo-columns and value keywords that look like a bare column name.
# All are reserved words, so no object can take their name.
PSEUDO_COLUMNS = frozenset(
    """
    ROWNUM ROWID LEVEL CONNECT_BY_ISLEAF CONNECT_BY_ISCYCLE USER SYSDATE
    SYSTIMESTAMP CURRENT_DATE CURRENT_TIMESTAMP LOCALTIMESTAMP
    """.split()  # noqa: SIM905 (a word list reads better)
)

# Tables and views the check may resolve columns in: schema -> table -> column
# -> type, with names exactly as Oracle stores them (so a quoted lowercase
# column is only found when quoted).
ColumnIndex = Mapping[str, Mapping[str, Mapping[str, str]]]
_DUAL = {"": {"DUAL": {"DUMMY": "VARCHAR2(1)"}}}

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


def check_sql(
    sql: str, *, allowed_schemas: frozenset[str], columns: ColumnIndex | None
) -> CheckedSql:
    """Return what the statement reads, or raise SqlRejected explaining why not.

    `columns` is the catalog: every column the query names must be a real
    column of a table in its scope, because Oracle runs an unknown bare name
    as a function. Only tests that aren't about columns pass None.
    """
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SqlRejected("The query is empty.")
    if (not_text := lone_surrogate(sql)) is not None:
        raise SqlRejected(not_text.message)
    if len(sql.encode()) > MAX_SQL_BYTES:
        raise SqlRejected(f"The query is longer than {size_text(MAX_SQL_BYTES)}.")

    statement = _parse_single_statement(sql)
    if not isinstance(statement, _SELECT_ROOTS):
        raise SqlRejected("Only SELECT queries (optionally with WITH) are allowed.")

    _reject_forbidden_constructs(statement)
    _check_calls_as_written(sql)
    tables = _referenced_tables(statement, allowed_schemas)
    if columns is not None:
        # An unquoted _col_1 isn't a legal Oracle name, so if the SQL never
        # says _col_, every such name is one qualify made up (see below).
        _check_columns(statement, columns, generated_names="_col_" not in sql.lower())
    binds = tuple(sorted({p.name for p in statement.find_all(exp.Placeholder) if p.name}))
    return CheckedSql(sql=sql, tables=tables, binds=binds, warnings=_warnings(statement))


def _parse_single_statement(sql: str) -> exp.Expr:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="oracle") if s is not None]
    except SqlglotError as error:  # a parse error, or one from the tokenizer
        first_line = str(error).splitlines()[0]
        raise SqlRejected(f"The query couldn't be parsed as Oracle SQL: {first_line}") from error
    if len(statements) != 1:
        raise SqlRejected("Send exactly one statement at a time.")
    return statements[0]


def _reject_forbidden_constructs(statement: exp.Expr) -> None:
    aliases = {a.alias.upper() for a in statement.find_all(exp.Alias) if a.alias}
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRejected(f"{node.key.upper()} isn't allowed; queries may only read data.")
        if isinstance(node, exp.Select) and (node.args.get("locks") or node.args.get("into")):
            raise SqlRejected("SELECT ... FOR UPDATE and SELECT ... INTO aren't allowed.")
        if isinstance(node, exp.Pivot):
            # The column check can't work out what a pivot produces, so names in
            # its query block would go unchecked.
            raise SqlRejected(
                "PIVOT and UNPIVOT aren't supported. Use conditional aggregation "
                "instead, for example SUM(CASE WHEN x = 1 THEN y END) AS y_1."
            )
        if isinstance(node, exp.Having):
            _reject_aliases_in_having(node, aliases)
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Func):
            raise SqlRejected(
                "Reading from a function (a table function) isn't allowed; read tables and views."
            )
        if isinstance(node, exp.Dot) and _contains_function_call(node):
            raise SqlRejected(
                "Calling package or object functions isn't allowed: "
                + node.sql(dialect="oracle")[:80]
            )
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()
            if name in DENIED_FUNCTIONS or name.startswith(_DENIED_PREFIXES):
                raise SqlRejected(
                    f"The function {name} isn't allowed: it can reach outside the "
                    "database or describe its setup. Queries may only read the data."
                )
            if isinstance(node, exp.Anonymous) and name not in ORACLE_FUNCTIONS:
                raise SqlRejected(
                    f"{name} isn't one of Oracle's built-in SQL functions, so it isn't "
                    "allowed (a function defined in the database could do more than "
                    "read). Use built-in SQL instead; the DataLab maintainer can add "
                    "a built-in that's missing."
                )
        if isinstance(node, (exp.Fetch, exp.Offset, exp.Limit)):
            # A row count must be a number or a bind: a name there could be a
            # function call, and the column check doesn't look at it.
            count = node.args.get("count") if isinstance(node, exp.Fetch) else node.expression
            if count is not None and not isinstance(count, (exp.Literal, exp.Placeholder)):
                raise SqlRejected("FETCH and OFFSET take a number or a bind variable.")
        if isinstance(node, exp.Column) and node.name.upper() in _SEQUENCE_PSEUDOCOLUMNS:
            raise SqlRejected("Using sequences (NEXTVAL/CURRVAL) isn't allowed.")


def _check_calls_as_written(sql: str) -> None:
    """Check every call by the name as written in the SQL.

    The parser maps some names onto its own functions (IFNULL becomes
    COALESCE, and MD5 or LEFT become typed functions), but Oracle runs the
    name as written: one that isn't a built-in could be a function defined in
    the database. A quoted name must match exactly, since Oracle treats a
    quoted lowercase "nvl" as a different object from NVL.
    """
    try:
        tokens = sqlglot.tokenize(sql, dialect="oracle")
    except SqlglotError as error:
        raise SqlRejected(f"The query couldn't be read as Oracle SQL: {error}") from error
    for i, token in enumerate(tokens[:-1]):
        if tokens[i + 1].token_type != TokenType.L_PAREN or not _is_word(token):
            continue
        previous = tokens[i - 1] if i else None
        if previous is not None and previous.token_type == TokenType.DOT:
            # schema.function(...), package.function(...) or a method: none is
            # a built-in, and Oracle runs a schema's function even in FROM.
            raise SqlRejected(
                "Calling package, schema or object functions isn't allowed: "
                f"...{previous.text}{token.text}(...)"
            )
        quoted = token.token_type == TokenType.IDENTIFIER
        name = token.text if quoted else token.text.upper()
        if name in ORACLE_FUNCTIONS:
            continue
        if not quoted:
            if name in _PAREN_KEYWORDS or _is_cte_column_list(tokens, i):
                continue
            before = previous.text.upper() if previous is not None else ""
            if name in _TYPE_WORDS and (
                before in _TYPE_INTRODUCERS
                or (previous is not None and previous.token_type == TokenType.STRING)
            ):
                continue
        shown = f'"{name}"' if quoted else name
        if name.upper() in DENIED_FUNCTIONS or name.upper().startswith(_DENIED_PREFIXES):
            raise SqlRejected(
                f"The function {shown} isn't allowed: it can reach outside the "
                "database or describe its setup. Queries may only read the data."
            )
        raise SqlRejected(
            f"{shown} isn't one of Oracle's built-in SQL functions, so it isn't "
            "allowed (a function defined in the database could do more than read)."
            + (" Write built-in names without quotes." if quoted else "")
            + " Use built-in SQL instead; the DataLab maintainer can add a built-in "
            "that's missing."
        )


def _reject_aliases_in_having(having: exp.Having, aliases: set[str]) -> None:
    """Oracle before 23ai doesn't read a select-list alias in HAVING, and no
    version reads one from another branch of a UNION: it would look for a
    function of that name instead. So a bare name in HAVING may not match any
    alias in the statement; repeat the expression."""
    for column in having.find_all(exp.Column):
        if not column.table and column.name.upper() in aliases:
            raise SqlRejected(
                f"HAVING can't use the name {column.name.upper()}, which is also an "
                "alias in this query: repeat the expression instead (Oracle may "
                "read the name as a function)."
            )


def _is_word(token: Token) -> bool:
    text = token.text
    return token.token_type == TokenType.IDENTIFIER or (
        bool(text) and (text[0].isalpha() or text[0] == "_")
    )


def _is_cte_column_list(tokens: list[Token], i: int) -> bool:
    """`WITH name (a, b) AS (...)`: the name, its column list, then AS (."""
    if i == 0 or tokens[i - 1].token_type not in (TokenType.WITH, TokenType.COMMA):
        return False
    depth = 0
    for j in range(i + 1, len(tokens)):
        if tokens[j].token_type == TokenType.L_PAREN:
            depth += 1
        elif tokens[j].token_type == TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                rest = tokens[j + 1 : j + 3]
                return (
                    len(rest) == 2
                    and rest[0].text.upper() == "AS"
                    and rest[1].token_type == TokenType.L_PAREN
                )
    return False


def _check_columns(
    statement: exp.Expr, columns: ColumnIndex, *, generated_names: bool = False
) -> None:
    """Every column must be a real column of a table in its scope.

    Oracle treats a name that isn't a column (ORA_DATABASE_NAME, a dotted
    DBMS_UTILITY.PORT_STRING, or a function defined in the database) as a call
    with no arguments, so an unknown column is refused rather than run.
    """
    _record_output_names(statement)
    tree = statement.copy()
    for column in list(tree.find_all(exp.Column)):
        bare = not column.table and not column.this.args.get("quoted")
        if bare and column.name.upper() in PSEUDO_COLUMNS:
            column.replace(exp.null())
    for order in list(tree.find_all(exp.Order)):
        for ordered in order.expressions:
            # ORDER BY 1 is a position, which Oracle never runs as a call. Left
            # in, qualify turns it into a bare column named after the output it
            # points to, which for SELECT 'x' is X: not a name ORDER BY may use.
            if isinstance(ordered, exp.Ordered) and _is_position(ordered.this):
                position = exp.null()
                position.meta[POSITION] = int(ordered.this.name)
                ordered.this.replace(position)
    schema = MappingSchema({**_DUAL, **columns}, dialect="oracle", normalize=False)
    try:
        tree = qualify(
            tree,
            schema=schema,
            dialect="oracle",
            validate_qualify_columns=True,
            quote_identifiers=False,
            infer_schema=False,
        )
    except OptimizeError as error:
        if (spelling := _spelling_problem(statement, columns)) is not None:
            raise SqlRejected(spelling) from error
        detail = str(error).split(". Line:")[0]
        raise SqlRejected(
            f"{detail}. Every name in a query must be a column of a table it reads "
            "(Oracle would run an unknown name as a function). Check the names with "
            "describe_table; quote lowercase column names exactly; in a recursive "
            "WITH query, qualify its columns with the query's name (r.n)."
        ) from error
    _require_resolved_columns(tree, generated_names=generated_names)
    if (lob := lob_problem(tree, columns)) is not None:
        raise SqlRejected(lob)


def _spelling_problem(statement: exp.Expr, columns: ColumnIndex) -> str | None:
    """When a name that isn't a column matches one only by letter case: what
    to write instead. Oracle upper-cases an unquoted name (Bdate is BDATE),
    and a quoted one must match exactly, so a column stored with lower-case
    letters ("Bdate", "Black tea") must be written in double quotes, exactly.
    Only explains a refusal: it never lets a name through."""
    names: set[str] = set()
    for table in statement.find_all(exp.Table):
        names |= set(columns.get(table.db.upper(), {}).get(table.name.upper(), {}))
    by_upper: dict[str, list[str]] = {}
    for name in sorted(names):
        by_upper.setdefault(name.upper(), []).append(name)
    for column in statement.find_all(exp.Column):
        identifier = column.this
        if not isinstance(identifier, exp.Identifier):
            continue
        quoted = bool(identifier.args.get("quoted"))
        read_as = identifier.name if quoted else identifier.name.upper()
        if read_as in names:
            continue
        spelled = [n for n in by_upper.get(identifier.name.upper(), []) if n != read_as]
        if not spelled:
            continue
        right = sql_name(spelled[0])
        if right.startswith('"'):
            why = (
                "is spelled with lower-case letters"
                if any(c.islower() for c in spelled[0])
                else "isn't a plain Oracle name"
            )
            return f"{right} {why}, so Oracle needs it in double quotes, exactly: {right}."
        return (
            f"\"{identifier.name}\" doesn't match the column's spelling: in double quotes a "
            f'name must match exactly. Write {right} (without quotes, or as "{right}").'
        )
    return None


def _require_resolved_columns(tree: exp.Expr, *, generated_names: bool) -> None:
    """After qualify, every column must name a source of its own query block,
    or of an enclosing one (a correlated reference).

    qualify sometimes accepts a name as a reference to an alias defined
    somewhere else in the statement (another UNION branch, a WITH query's
    column list) and leaves it unqualified. Oracle doesn't read those names
    as aliases; it runs them as functions. The one unqualified name allowed
    is an ORDER BY reference to an alias of the same query, which Oracle does
    resolve. (ORDER BY positions never get here: _check_columns takes them
    out first.) qualify also rewrites `ORDER BY COUNT(*)` for an unaliased
    expression to its own name for it, _COL_<n>: allowed when
    `generated_names` says the SQL itself never used such a name.
    """
    for scope in traverse_scope(tree):
        for column in find_all_in_scope(scope.expression, exp.Column):
            if column.table:
                if not _visible(scope, column.table):
                    raise SqlRejected(
                        f"{column.sql(dialect='oracle')} doesn't name a table in this "
                        "part of the query, so DataLab won't run it."
                    )
            elif not (
                column.find_ancestor(exp.Order) is not None
                and (
                    column.name in _output_names(scope.expression)
                    or (generated_names and _is_generated_output(scope.expression, column.name))
                )
            ):
                raise SqlRejected(
                    f"{column.name.upper()} isn't a column of a table this part of the "
                    "query reads (Oracle would run it as a function). Use the table's "
                    "column, or repeat the expression instead of an alias."
                )


def _is_position(node: exp.Expr) -> bool:
    """An unsigned whole number, as in ORDER BY 2."""
    return isinstance(node, exp.Literal) and not node.is_string and node.this.isdigit()


def _visible(scope: Scope | None, table: str) -> bool:
    while scope is not None:
        if table in scope.sources:
            return True
        scope = scope.parent
    return False


def _output_names(query: exp.Expr) -> set[str]:
    """The column names a query produces (for a UNION, its first branch's)."""
    while isinstance(query, exp.SetOperation):
        query = query.left
    if not isinstance(query, exp.Select):
        return set()
    return query.meta.get(_OUTPUTS, set())


_OUTPUTS = "datalab_outputs"


def _record_output_names(statement: exp.Expr) -> None:
    """Note each query block's own output names before qualify, which gives
    every expression an alias: `SELECT 'BOOM' ... ORDER BY boom` has no
    output named BOOM, and Oracle would run BOOM as a function. Only an
    explicit alias or a bare column counts."""
    for select in statement.find_all(exp.Select):
        names = set()
        for e in select.expressions:
            if isinstance(e, (exp.Alias, exp.Column)):
                identifier = e.args["alias"] if isinstance(e, exp.Alias) else e.this
                if isinstance(identifier, exp.Identifier):
                    # As Oracle stores it: unquoted names upper-case, quoted exact.
                    quoted = identifier.args.get("quoted")
                    names.add(identifier.name if quoted else identifier.name.upper())
        select.meta[_OUTPUTS] = names


def _is_generated_output(query: exp.Expr, name: str) -> bool:
    """_COL_<n>: qualify's name for an unaliased select expression."""
    while isinstance(query, exp.SetOperation):
        query = query.left
    if not isinstance(query, exp.Select) or not re.fullmatch(r"_COL_\d+", name):
        return False
    return name in {e.alias for e in query.expressions if isinstance(e, exp.Alias)}


def _contains_function_call(node: exp.Dot) -> bool:
    return any(isinstance(n, exp.Func) for n in node.walk())


def _referenced_tables(
    statement: exp.Expr, allowed_schemas: frozenset[str]
) -> tuple[TableRef, ...]:
    refs: set[TableRef] = set()
    for table in statement.find_all(exp.Table):
        name = table.name
        if "@" in name or table.args.get("catalog"):
            raise SqlRejected("Database links and cross-database references aren't allowed.")
        schema = table.db.upper()
        if not schema:
            if name.upper() == "DUAL" or name.upper() in _ctes_in_scope(table):
                continue
            raise SqlRejected(
                f"Qualify every table with its cohort schema, for example IHS_2025.{name.upper()}."
            )
        if schema not in allowed_schemas:
            raise SqlRejected(f"The schema {schema} isn't available to DataLab.")
        refs.add(TableRef(schema=schema, name=name.upper()))
    return tuple(sorted(refs, key=str))


def _ctes_in_scope(table: exp.Table) -> set[str]:
    """The CTE names an unqualified table reference can actually mean.

    Only WITH clauses that enclose the reference count. Inside a CTE's own
    body, only the CTEs defined before it (and itself, for recursive CTEs)
    are visible. Any other unqualified name would be resolved by Oracle as a
    real object, so it must not be skipped here.
    """
    visible: set[str] = set()
    child: exp.Expr = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            ctes = [c for c in node.expressions if isinstance(c, exp.CTE)]
            if isinstance(child, exp.CTE) and child in ctes:
                visible |= {c.alias_or_name.upper() for c in ctes[: ctes.index(child) + 1]}
            else:
                visible |= {c.alias_or_name.upper() for c in ctes}
        # sqlglot 30 stores the WITH clause as "with_" (older versions: "with").
        elif (with_ := node.args.get("with_") or node.args.get("with")) is not None and (
            child is not with_
        ):
            # The reference is in the main query of a statement with a WITH clause.
            visible |= {c.alias_or_name.upper() for c in with_.expressions}
        child, node = node, node.parent
    return visible


def _warnings(statement: exp.Expr) -> tuple[str, ...]:
    warnings: list[str] = []
    # COUNT(*) counts rows; it doesn't return every column.
    if any(not isinstance(star.parent, exp.Count) for star in statement.find_all(exp.Star)):
        warnings.append("SELECT * returns every column; list the columns you need.")
    return tuple(warnings)
