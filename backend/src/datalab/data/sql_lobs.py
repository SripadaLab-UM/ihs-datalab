"""Long-text (CLOB, NCLOB) and BLOB columns where Oracle can't use them.

Oracle reads a LOB but won't compare, sort or group one: SELECT DISTINCT,
GROUP BY, ORDER BY, PARTITION BY, UNION/INTERSECT/MINUS, =, <, IN, BETWEEN,
a join's ON, MIN/MAX/COUNT and the like over a LOB all fail (ORA-00932 on
19c, ORA-22848 or ORA-22849 on 23ai). The SQL check refuses those before the
query runs, with the fix, rather than let Oracle refuse it with a message the
agent can't act on. Tested against the synthetic database, which has CLOB
columns (tests/test_sqlcheck_oracle.py).

Only what the catalog's types show is refused: a type the check can't work
out is never a reason to refuse. SUBSTR, UPPER, TRIM, || and similar return a
LOB when given one, so they count as one; TO_CHAR, LENGTH, INSTR and
the like don't. LIKE, REGEXP_LIKE and IS NULL work on a LOB and are allowed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, find_all_in_scope, traverse_scope

# Set on the NULL that stands in for ORDER BY <position> (sqlcheck._check_columns).
POSITION = "datalab_order_position"

_LOB_TYPES = ("CLOB", "NCLOB", "BLOB")
_PLAIN_NAME = re.compile(r"[A-Z][A-Z0-9_$#]*")

# Functions that return a LOB when their first argument is one (checked on
# the synthetic database). INITCAP, NLS_INITCAP and TRANSLATE are left out:
# Oracle gave ordinary text for them.
_KEEPS_LOB_OF_FIRST: tuple[type[exp.Expr], ...] = (
    exp.Substring,
    exp.Upper,
    exp.Lower,
    exp.Trim,
    exp.Pad,
    exp.Replace,
    exp.RegexpReplace,
    exp.RegexpSubstr,
)
_KEEPS_LOB_OF_FIRST_NAMES = frozenset({"SUBSTRB", "SUBSTRC", "NLS_UPPER", "NLS_LOWER"})
_MAKES_LOB_NAMES = {"TO_CLOB": "CLOB", "TO_NCLOB": "NCLOB"}

# Functions that fail with a LOB argument: aggregates, analytics, and ones
# that compare their arguments. Checked on the synthetic database.
_NO_LOB_ARGUMENT: tuple[type[exp.Expr], ...] = (
    exp.Min,
    exp.Max,
    exp.Count,
    exp.Sum,
    exp.Avg,
    exp.Median,
    exp.ApproxDistinct,
    exp.Stddev,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Variance,
    exp.VariancePop,
    exp.FirstValue,
    exp.LastValue,
    exp.Lag,
    exp.Lead,
    exp.NthValue,
    exp.Greatest,
    exp.Least,
    exp.Nullif,
)
_NO_LOB_ARGUMENT_NAMES = frozenset({"STATS_MODE", "VAR_SAMP"})

_COMPARISONS: tuple[type[exp.Expr], ...] = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Between,
    exp.In,
)
_COMPARED = "a comparison (=, <>, <, >, IN, BETWEEN, or a join's ON)"


@dataclass(frozen=True)
class _Lob:
    column: str  # the catalog column it comes from, as Oracle stores it
    type: str  # CLOB, NCLOB or BLOB
    made: bool = False  # made a LOB by the query itself (TO_CLOB, CAST AS CLOB)


def sql_name(name: str) -> str:
    """A column name as SQL must write it: in double quotes unless it's a
    plain upper-case Oracle name ("Bdate", "Black tea", QUESTIONTEXT)."""
    return name if _PLAIN_NAME.fullmatch(name) else f'"{name}"'


def lob_type(sql_type: str) -> str | None:
    """CLOB, NCLOB or BLOB for a catalog type, else None."""
    upper = sql_type.strip().upper()
    return next((t for t in _LOB_TYPES if upper == t or upper.startswith(t + "(")), None)


def lob_problem(
    tree: exp.Expr, columns: Mapping[str, Mapping[str, Mapping[str, str]]]
) -> str | None:
    """Why Oracle would refuse this qualified tree for a LOB, or None."""
    return _LobCheck(tree, columns).problem()


class _LobCheck:
    def __init__(self, tree: exp.Expr, columns: Mapping[str, Mapping[str, Mapping[str, str]]]):
        self._tree = tree
        self._columns = columns
        self._scopes = list(traverse_scope(tree))
        self._scope_of = {id(s.expression): s for s in self._scopes}

    def problem(self) -> str | None:
        for scope in self._scopes:
            if (found := self._in_scope(scope)) is not None:
                return found
        return self._in_set_operations()

    # Where a LOB can't go ---------------------------------------------------

    def _in_scope(self, scope: Scope) -> str | None:
        query = scope.expression
        if isinstance(query, exp.Select) and query.args.get("distinct") is not None:
            for projection in query.expressions:
                if (lob := self._lob(projection, scope)) is not None:
                    return _message(projection, lob, "SELECT DISTINCT")
        wanted = (
            exp.Group,
            exp.Order,
            exp.Window,
            exp.Case,
            exp.DecodeCase,
            exp.Anonymous,
            *_COMPARISONS,
            *_NO_LOB_ARGUMENT,
        )
        for node in find_all_in_scope(query, exp.Expr):
            if isinstance(node, wanted) and (found := self._node(node, scope)) is not None:
                return found
        return None

    def _node(self, node: exp.Expr, scope: Scope) -> str | None:
        if isinstance(node, exp.Group):
            return self._first(scope, "GROUP BY", _group_items(node))
        if isinstance(node, exp.Order):
            for ordered in node.expressions:
                target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                if (found := self._ordered(target, node, scope)) is not None:
                    return _message(found[0], found[1], "ORDER BY")
            return None
        if isinstance(node, exp.Window):
            return self._first(scope, "PARTITION BY", node.args.get("partition_by") or [])
        if isinstance(node, exp.Case):
            operand = node.args.get("this")
            return self._first(scope, _COMPARED, [operand] if operand is not None else [])
        if isinstance(node, exp.DecodeCase):
            args = node.expressions
            searched = [
                a for i, a in enumerate(args) if i == 0 or (i % 2 == 1 and i < len(args) - 1)
            ]
            return self._first(scope, _COMPARED, searched)
        if isinstance(node, _COMPARISONS):
            return self._comparison(node, scope)
        if isinstance(node, exp.Anonymous):
            if node.name.upper() not in _NO_LOB_ARGUMENT_NAMES:
                return None
            return self._first(scope, f"{node.name.upper()}()", node.expressions)
        return self._argument(node, scope)

    def _argument(self, node: exp.Expr, scope: Scope) -> str | None:
        name = (
            "APPROX_COUNT_DISTINCT"
            if isinstance(node, exp.ApproxDistinct)
            else node.sql(dialect="oracle").split("(")[0].upper()
        )
        if isinstance(node, (exp.Lag, exp.Lead, exp.NthValue)):
            args = [node.this]
        elif isinstance(node, (exp.Greatest, exp.Least)):
            args = [node.this, *node.expressions]
        elif isinstance(node, exp.Nullif):
            args = [node.this, node.expression]
        else:
            arg = node.this
            if isinstance(arg, exp.Distinct):
                args = list(arg.expressions)
            elif isinstance(arg, exp.Star) or arg is None:
                args = []
            else:
                args = [arg]
        counted = isinstance(node, exp.Count) and not isinstance(node.this, exp.Distinct)
        for arg in args:
            if arg is not None and (lob := self._lob(arg, scope)) is not None:
                return _message(arg, lob, f"{name}()", count=counted)
        return None

    def _comparison(self, node: exp.Expr, scope: Scope) -> str | None:
        operands: list[exp.Expr] = [node.this] if node.this is not None else []
        if isinstance(node, exp.Between):
            operands += [node.args["low"], node.args["high"]]
        elif isinstance(node, exp.In):
            operands += node.expressions
            query = node.args.get("query")
            if query is not None:
                inner = query.this if isinstance(query, exp.Subquery) else query
                if (found := self._projections(inner, _COMPARED)) is not None:
                    return found
        elif node.expression is not None:
            operands.append(node.expression)
        return self._first(scope, _COMPARED, operands)

    def _in_set_operations(self) -> str | None:
        for node in self._tree.find_all(exp.SetOperation):
            if isinstance(node, exp.Union) and not node.args.get("distinct"):
                continue  # UNION ALL
            for side in (node.this, node.expression):
                found = self._projections(side, "UNION, INTERSECT or MINUS (UNION ALL is fine)")
                if found is not None:
                    return found
        return None

    def _projections(self, query: exp.Expr, context: str) -> str | None:
        for select in _leaf_selects(query):
            scope = self._scope_of.get(id(select))
            if scope is None:
                continue
            if (found := self._first(scope, context, select.expressions)) is not None:
                return found
        return None

    def _first(self, scope: Scope, context: str, nodes: list[exp.Expr] | Iterator[exp.Expr]):
        for node in nodes:
            if (lob := self._lob(node, scope)) is not None:
                return _message(node, lob, context)
        return None

    def _ordered(
        self, target: exp.Expr, order: exp.Order, scope: Scope
    ) -> tuple[exp.Expr, _Lob] | None:
        """What an ORDER BY item sorts by, if it's a LOB: a position or an
        output name points at the query's own select list."""
        owner = order.parent
        position = target.meta.get(POSITION) if isinstance(target, exp.Null) else None
        by_output = isinstance(target, exp.Column) and not target.table
        if isinstance(owner, (exp.Select, exp.SetOperation)) and (position or by_output):
            for select in _leaf_selects(owner):
                select_scope = self._scope_of.get(id(select), scope)
                projection = (
                    _nth(select.expressions, position)
                    if position
                    else _named(select.expressions, target.name)
                )
                if projection is not None and (lob := self._lob(projection, select_scope)):
                    return (projection if position else target), lob
            return None
        lob = self._lob(target, scope)
        return (target, lob) if lob is not None else None

    # What is a LOB -----------------------------------------------------------

    def _lob(self, node: exp.Expr, scope: Scope, depth: int = 0) -> _Lob | None:
        if depth > 50:
            return None
        again = lambda n, s=scope: self._lob(n, s, depth + 1) if n is not None else None  # noqa: E731
        if isinstance(node, (exp.Alias, exp.Paren)):
            return again(node.this)
        if isinstance(node, exp.Column):
            return self._column(node, scope, depth)
        if isinstance(node, exp.Cast):
            to = node.args.get("to")
            written = to.sql(dialect="oracle").upper() if isinstance(to, exp.DataType) else ""
            kind = lob_type(written) or ("CLOB" if written == "TEXT" else None)
            return _Lob(_shown(node), kind, made=True) if kind else None
        if isinstance(node, _KEEPS_LOB_OF_FIRST):
            return again(node.this)
        if isinstance(node, exp.Anonymous):
            name = node.name.upper()
            if name in _MAKES_LOB_NAMES:
                return _Lob(_shown(node), _MAKES_LOB_NAMES[name], made=True)
            if name in _KEEPS_LOB_OF_FIRST_NAMES and node.expressions:
                return again(node.expressions[0])
            return None
        if isinstance(node, exp.Concat):
            return next((lob for e in node.expressions if (lob := again(e))), None)
        if isinstance(node, exp.DPipe):
            return again(node.this) or again(node.expression)
        if isinstance(node, exp.Coalesce):
            return again(node.this) or next(
                (lob for e in node.expressions if (lob := again(e))), None
            )
        if isinstance(node, exp.Nvl2):
            return again(node.args.get("true")) or again(node.args.get("false"))
        if isinstance(node, exp.Case):
            results = [i.args.get("true") for i in node.args.get("ifs") or []]
            results.append(node.args.get("default"))
            return next((lob for e in results if (lob := again(e))), None)
        if isinstance(node, exp.DecodeCase):
            args = node.expressions
            results = [a for i, a in enumerate(args) if i >= 2 and i % 2 == 0]
            if len(args) > 2 and len(args) % 2 == 0:
                results.append(args[-1])  # the default
            return next((lob for e in results if (lob := again(e))), None)
        return None

    def _column(self, column: exp.Column, scope: Scope, depth: int) -> _Lob | None:
        if not column.table:
            return None  # an output name: _ordered looks those up
        current: Scope | None = scope
        while current is not None and column.table not in current.sources:
            current = current.parent
        if current is None:
            return None
        source = current.sources[column.table]
        if isinstance(source, exp.Table):
            table = self._columns.get(source.db, {}).get(source.name, {})
            kind = lob_type(table.get(column.name, ""))
            return _Lob(column.name, kind) if kind else None
        if isinstance(source, Scope):
            for select in _leaf_selects(source.expression):
                projection = _named(select.expressions, column.name)
                select_scope = self._scope_of.get(id(select), source)
                if projection is not None and (
                    lob := self._lob(projection, select_scope, depth + 1)
                ):
                    return lob
        return None


def _message(node: exp.Expr, lob: _Lob, context: str, *, count: bool = False) -> str:
    refused = (
        f"Oracle can't use a {lob.type} in {context}: it would refuse the query "
        "(ORA-00932 or ORA-22848)."
    )
    if lob.made:
        return (
            f"{lob.column} is a {lob.type}, and {refused} Leave the conversion to "
            f"{lob.type} out there."
        )
    column = sql_name(lob.column)
    shown = _shown(node)
    what = (
        f"{column} is a {lob.type} column"
        if shown == column
        else f"{shown} is a {lob.type}, made from the {lob.type} column {column}"
    )
    if count:
        return (
            f"{what}, and {refused} To count the rows where it's filled in, use "
            f"COUNT(LENGTH({column})), or COUNT(*) with WHERE {column} IS NOT NULL."
        )
    if lob.type == "BLOB":
        return f"{what}, and {refused} Leave it out there; LENGTH({column}) and IS NULL work on it."
    return (
        f"{what}, and {refused} Use TO_CHAR(SUBSTR({column}, 1, 1000)) there instead: "
        f"ordinary text of up to 1,000 characters. SUBSTR, UPPER, TRIM or || alone still "
        f"give a {lob.type}, and DBMS_LOB.SUBSTR isn't available in DataLab. LIKE, IS NULL, "
        f"LENGTH and INSTR work on the {lob.type} as it is."
    )


def _shown(node: exp.Expr) -> str:
    """The expression as the query wrote it, without the qualifiers and
    aliases the column check added."""
    if isinstance(node, exp.Alias):
        node = node.this
    bare = node.copy()
    for column in list(bare.find_all(exp.Column)):
        column.set("table", None)
    if isinstance(bare, exp.Column):
        return sql_name(bare.name)
    text = bare.sql(dialect="oracle")
    return text if len(text) <= 120 else text[:117] + "..."


def _group_items(group: exp.Group) -> Iterator[exp.Expr]:
    containers = (exp.Tuple, exp.Cube, exp.Rollup, exp.GroupingSets)

    def items(node: exp.Expr) -> Iterator[exp.Expr]:
        if isinstance(node, containers):
            for inner in node.expressions:
                yield from items(inner)
        else:
            yield node

    for value in group.args.values():
        for node in value if isinstance(value, list) else [value]:
            if isinstance(node, exp.Expr):
                yield from items(node)


def _leaf_selects(query: exp.Expr) -> Iterator[exp.Select]:
    while isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, exp.SetOperation):
        yield from _leaf_selects(query.this)
        yield from _leaf_selects(query.expression)
    elif isinstance(query, exp.Select):
        yield query


def _named(projections: list[exp.Expr], name: str) -> exp.Expr | None:
    return next((p for p in projections if p.alias_or_name == name), None)


def _nth(projections: list[exp.Expr], position: int) -> exp.Expr | None:
    return projections[position - 1] if 0 < position <= len(projections) else None
