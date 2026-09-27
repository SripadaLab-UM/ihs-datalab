"""Workflow drafts from SQL: the Playground's Save as workflow, and a
conversation's Turn this into a workflow (docs/WORKFLOWS.md, Authoring).

DataLab writes the draft itself, from the SQL and binds alone: no model is
asked, and no data is read. The person names it, reviews the YAML, edits it
if they like, and saves it; nothing here saves anything.

For each query, the draft has:

- a SQL step (`extract`, or `extract_1`, `extract_2`, … for several), whose
  output is a CSV;
- `reads:` from the tables the SQL names, found by the same analysis the
  file check uses (`sql_tables`, as `check_reads` does), so the two agree;
- a parameter for each bind, lower case. Its type comes from the column
  it's compared with, in the catalog (a number, or a date, as is a bind
  inside TO_DATE); otherwise it stays text, as the Playground sent it. The
  value it ran with becomes the default only where that's plainly safe to
  write into a file others read: a date, or a number compared as a range
  (`STEPS > :min_steps`) or named as a limit, never text, and never for a
  bind whose name, or the column it's compared with, looks like it's about
  a person (`:dob`, `PARTICIPANTIDENTIFIER = :p`, `LASTNAME = :n`). A note
  says why each other one has no default;
- a built-in check: at least one row, the columns the query names (when it
  names them all, without `*`), and, when the query aggregates, a
  `small_cells` check over its count columns: `COUNT(...)`, and `SUM` of 1s
  (`SUM(CASE WHEN … THEN 1 ELSE 0 END)`). Any other aggregate (`SUM(x)`,
  `AVG`, `MAX`…) is listed too, with a note, since DataLab can't tell it
  isn't a count; when an aggregate has no name, or the counting happens in
  a subquery, the list is left empty, so the file check asks for it.

The draft's file also gets the Save & share check (pipelines/check.py) for
possible participant data, such as an id typed into the SQL; the person
confirms each finding before saving, wherever it's saved (api/workflows.py).

and, if the person picked a destination key, `deliver:` of every output.

A query the SQL check refuses doesn't become a draft (`DraftRefused`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from datalab.data.sqlcheck import ColumnIndex, SqlRejected, check_sql
from datalab.pipelines.check import looks_identifying
from datalab.sessions.titles import normalize_title, scrub_title
from datalab.workflows.model import SCHEMA_VERSION, SMALL_CELL_FLOOR, schemas_named, sql_tables

BindValue = str | int | float | bool | None
# Most queries a draft takes from a conversation.
MAX_QUERIES = 20

_INTEGER = re.compile(r"-?(?:0|[1-9]\d{0,17})")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d{0,17})\.\d{1,12}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class DraftQuery:
    sql: str
    binds: Mapping[str, BindValue]


@dataclass(frozen=True)
class Draft:
    text: str  # the workflow file
    notes: list[str]  # what the person should know before saving


class DraftRefused(ValueError):
    """A query the SQL check refuses: no draft is made."""


def draft_workflow(
    queries: Sequence[DraftQuery],
    *,
    name: str,
    description: str = "",
    destination: str | None = None,
    allowed_schemas: frozenset[str] | None,
    columns: ColumnIndex | None,
) -> Draft:
    """The workflow file for these queries, and notes on it. It isn't checked
    here: the caller checks it as any workflow file (runner.check_text).
    `allowed_schemas` are the cohorts this DataLab may read (None: any)."""
    if not queries:
        raise DraftRefused("There's no query to make a workflow from.")
    if len(queries) > MAX_QUERIES:
        raise DraftRefused(f"A draft takes at most {MAX_QUERIES} queries.")
    several = len(queries) > 1
    notes: list[str] = []
    parameters: dict[str, dict[str, Any]] = {}
    chosen: dict[str, Any] = {}  # each parameter's default, from the first query using it
    reads: set[str] = set()
    steps: list[dict[str, Any]] = []
    outputs: list[str] = []
    stem = name.strip() or "workflow"

    for number, query in enumerate(queries, start=1):
        label = f"Query {number}: " if several else ""
        try:
            # As the file check: any schema, when this DataLab names none.
            allowed = allowed_schemas or schemas_named(query.sql)
            checked = check_sql(query.sql, allowed_schemas=allowed, columns=columns)
        except SqlRejected as error:
            raise DraftRefused(f"{label}The SQL check refuses it: {error}") from None
        sql = checked.sql
        # The same analysis as the file check's `reads:` rule.
        reads |= sql_tables(sql)

        tree = sqlglot.parse_one(sql, read="oracle")
        uses = _bind_uses(tree)
        types = _column_types(columns, [str(t) for t in checked.tables])
        given = {key.lower(): value for key, value in query.binds.items()}
        for bind in checked.binds:
            param = bind.lower()
            value = given.get(param)
            declared, default, why = _parameter(param, value, uses.get(param, _Use()), types)
            if param in parameters:
                if default is not None and chosen.get(param) not in (None, default):
                    notes.append(
                        f":{bind} had different values in different queries; the draft's "
                        f"default is the first one's."
                    )
                continue
            if why:
                notes.append(why)
            parameters[param] = _FlowMap(declared)
            chosen[param] = default

        step_id = f"extract_{number}" if several else "extract"
        output = f"{stem}_{number}.csv" if several else f"{stem}.csv"
        steps.append({"id": step_id, "sql": _Block(sql + "\n"), "output": output})
        outputs.append(step_id)

        qc: dict[str, Any] = {"file": step_id, "min_rows": 1}
        named = _output_columns(tree)
        if named is not None and named:
            qc["required_columns"] = _Flow(named)
        if _aggregates(tree):
            qc["small_cells"] = {
                "count_columns": _Flow(_small_cells(tree, label, notes)),
                "min": SMALL_CELL_FLOOR,
            }
        steps.append({"id": f"check_{number}" if several else "check", "qc": qc})

    document: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "name": stem}
    if description.strip():
        document["description"] = normalize_title(description)
    if parameters:
        document["parameters"] = parameters
    document["reads"] = sorted(reads)
    document["steps"] = steps
    if destination:
        document["deliver"] = {
            "destination": destination,
            "folder": stem,
            "files": _Flow(outputs),
        }
    else:
        notes.append("With no destination, the workflow keeps its outputs in its run folder.")
    return Draft(text=yaml.dump(document, Dumper=_Dumper, sort_keys=False, width=100), notes=notes)


@dataclass
class _Use:
    """How the SQL uses one bind, over all the places it appears."""

    columns: set[str] = field(default_factory=set)  # compared with, upper case
    in_date_function: bool = False  # inside TO_DATE / TO_TIMESTAMP
    as_range: bool = False  # >, >=, <, <=, BETWEEN
    elsewhere: bool = False  # compared some other way (=, IN, LIKE…), or not at all


_COMPARISONS = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Between,
)  # fmt: skip
_RANGES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)
_DATE_FUNCTIONS = (exp.StrToDate, exp.StrToTime)
_STOP = (exp.Select, exp.Subquery, exp.Where, exp.Having, exp.Join)
_NUMERIC_TYPES = ("NUMBER", "INTEGER", "INT", "FLOAT", "DECIMAL", "SMALLINT", "BINARY_")
_DATE_TYPES = ("DATE", "TIMESTAMP")
# Parts of a name that say it's about a person (beyond pipelines/check.py's words).
_PERSONAL = (
    "identifier", "participant", "subject", "patient", "person", "email", "phone",
    "birth", "dob", "mrn", "ssn", "zip", "postal", "name", "address",
)  # fmt: skip
# A number named as a threshold or limit.
_LIMIT = re.compile(
    r"(?:^|_)(?:min|max|limit|threshold|cutoff|floor|ceiling|top|at_least|at_most)(?:_|$)"
)


def _bind_uses(tree: exp.Expr) -> dict[str, _Use]:
    uses: dict[str, _Use] = {}
    for placeholder in tree.find_all(exp.Placeholder):
        if not placeholder.name:
            continue
        use = uses.setdefault(placeholder.name.lower(), _Use())
        node: exp.Expr = placeholder
        compared: exp.Expr | None = None
        while node.parent is not None and not isinstance(node.parent, _STOP):
            parent = node.parent
            if isinstance(parent, _DATE_FUNCTIONS) and parent.this is node:
                use.in_date_function = True
            if isinstance(parent, _COMPARISONS):
                compared = parent
                break
            node = parent
        if compared is None:
            use.elsewhere = True
            continue
        if isinstance(compared, _RANGES):
            use.as_range = True
        else:
            use.elsewhere = True
        # The columns on the other side: all of them, but the bind's own side.
        for column in compared.find_all(exp.Column):
            if not any(p is node for p in [column, *_parents(column)]):
                use.columns.add(column.name.upper())
    return uses


def _column_types(columns: ColumnIndex | None, tables: list[str]) -> dict[str, set[str]]:
    """Each column name of the tables a query reads, with its catalog types."""
    types: dict[str, set[str]] = {}
    for table in tables:
        schema, _, name = table.partition(".")
        for column, kind in ((columns or {}).get(schema, {}).get(name, {}) or {}).items():
            types.setdefault(column.upper(), set()).add(kind.upper())
    return types


def _personal(name: str) -> bool:
    lowered = name.lower()
    squashed = re.sub(r"[_.\-\s]", "", lowered)
    return (
        looks_identifying(name)
        or any(part in squashed for part in _PERSONAL)
        or squashed.endswith("id")
    )


def _parameter(
    name: str, value: BindValue, use: _Use, types: Mapping[str, set[str]]
) -> tuple[dict[str, Any], Any, str | None]:
    """A bind's parameter declaration, its default, and a note, if any."""
    kinds = set().union(*(types.get(c, set()) for c in use.columns)) if use.columns else set()
    numeric = bool(kinds) and all(k.startswith(_NUMERIC_TYPES) for k in kinds)
    dated = use.in_date_function or (bool(kinds) and all(k.startswith(_DATE_TYPES) for k in kinds))
    text = "" if value is None else str(value).strip()

    if isinstance(value, bool):
        kind: str = "boolean"
    elif dated and _DATE.fullmatch(text) and _is_date(text):
        kind = "date"
    elif (numeric or (isinstance(value, int | float) and not kinds)) and _INTEGER.fullmatch(text):
        kind = "integer"
    elif (numeric or (isinstance(value, float) and not kinds)) and _NUMBER.fullmatch(text):
        kind = "number"
    else:
        # Text stays text: '12' against a VARCHAR2 column mustn't become a number.
        kind = "string"
    declared: dict[str, Any] = {"type": kind}

    if not text:
        return declared, None, f":{name} had no value, so it has no default."
    about = sorted(c for c in use.columns if _personal(c))
    if _personal(name) or about:
        why = f"it's compared with {', '.join(about)}" if about else "its name"
        return (
            declared,
            None,
            f":{name} may be about a person ({why}), so the value used isn't kept as its "
            "default: give it when you run the workflow.",
        )
    default: Any = None
    if kind == "date":
        default = text
    elif kind == "boolean":
        default = value
    elif (
        kind in ("integer", "number")
        and (use.as_range or _LIMIT.search(name))
        and not (use.elsewhere and not _LIMIT.search(name))
    ):
        default = int(text) if kind == "integer" else float(text)
    if default is None:
        return (
            declared,
            None,
            f":{name} has no default: only dates, and numbers used as a threshold or limit, "
            "keep the value they ran with. Give it when you run the workflow.",
        )
    if kind != "date" and scrub_title(str(default)) != normalize_title(str(default)):
        return (
            declared,
            None,
            f"The value used for :{name} looks like it could identify someone, so it isn't "
            "kept as the default.",
        )
    return {**declared, "default": default}, default, None


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _selects(tree: exp.Expr) -> list[exp.Expr]:
    """The output expressions: of the first SELECT, for a UNION (Oracle names
    the columns after it)."""
    while isinstance(tree, exp.SetOperation):
        tree = tree.this
    return list(tree.selects) if isinstance(tree, exp.Select) else []


def _column_name(expression: exp.Expr) -> str | None:
    """The column Oracle gives an output: an alias, or a column's own name,
    upper case unless quoted. None for anything else (Oracle names it after
    the expression's text)."""
    if isinstance(expression, exp.Alias):
        identifier = expression.args.get("alias")
    elif isinstance(expression, exp.Column) and not isinstance(expression.this, exp.Star):
        identifier = expression.this
    else:
        return None
    if not isinstance(identifier, exp.Identifier):
        return None
    return identifier.name if identifier.quoted else identifier.name.upper()


def _output_columns(tree: exp.Expr) -> list[str] | None:
    """Every output column's name, or None if any can't be named (such as `*`)."""
    names = []
    for expression in _selects(tree):
        name = _column_name(expression)
        if name is None:
            return None
        names.append(name)
    return names if len(set(names)) == len(names) else None


def _aggregates(tree: exp.Expr) -> bool:
    """Whether the output holds aggregates: GROUP BY, or an aggregate function,
    anywhere but a WHERE (`x > (SELECT AVG(x) …)` filters rows, it doesn't
    count them). When unsure, it says yes: the check is then asked for."""
    for node in tree.find_all(exp.AggFunc, exp.Group):
        if not any(isinstance(parent, exp.Where) for parent in _parents(node)):
            return True
    return False


def _parents(node: exp.Expr) -> list[exp.Expr]:
    found = []
    parent = node.parent
    while parent is not None:
        found.append(parent)
        parent = parent.parent
    return found


def _branches(tree: exp.Expr) -> list[exp.Select]:
    """The outermost SELECTs: one, or each side of a UNION."""
    if isinstance(tree, exp.SetOperation):
        return [*_branches(tree.this), *_branches(tree.expression)]
    return [tree] if isinstance(tree, exp.Select) else []


def _counts_ones(node: exp.Expr) -> bool:
    """Whether a SUM's argument is only 1s and 0s: SUM(1), SUM(CASE WHEN … THEN 1 ELSE 0 END)."""

    def one_or_zero(value: exp.Expr | None) -> bool:
        if value is None or isinstance(value, exp.Null):
            return True
        return isinstance(value, exp.Literal) and not value.is_string and value.name in ("0", "1")

    if isinstance(node, exp.Paren):
        return _counts_ones(node.this)
    if isinstance(node, exp.Case):
        values = [branch.args.get("true") for branch in node.args.get("ifs") or []]
        return bool(values) and all(one_or_zero(v) for v in [*values, node.args.get("default")])
    return isinstance(node, exp.Literal) and one_or_zero(node)


def _is_count(aggregate: exp.Expr) -> bool:
    if isinstance(aggregate, exp.Count):
        return True
    return isinstance(aggregate, exp.Sum) and _counts_ones(aggregate.this)


def _small_cells(tree: exp.Expr, label: str, notes: list[str]) -> list[str]:
    """The columns the small-cells check covers, failing closed: every
    aggregate output that isn't plainly a count is listed too, and if one
    can't be named, or the counting happens in a subquery, none is (so the
    file check asks the person)."""
    tops = [id(select) for select in _branches(tree)]
    inner = False
    for node in tree.find_all(exp.AggFunc, exp.Group):
        if any(isinstance(parent, exp.Where) for parent in _parents(node)):
            continue
        select = next((p for p in _parents(node) if isinstance(p, exp.Select)), None)
        if select is None or id(select) not in tops:
            inner = True
    counts: list[str] = []
    others: list[str] = []
    unnamed = False
    for expression in _selects(tree):
        aggregates = list(expression.find_all(exp.AggFunc))
        if not aggregates:
            continue
        name = _column_name(expression)
        if name is None:
            unnamed = True
            continue
        target = counts if all(_is_count(a) for a in aggregates) else others
        if name not in counts and name not in others:
            target.append(name)
    if inner:
        notes.append(
            f"{label}The query counts in a subquery, so DataLab can't tell which output "
            "columns are counts: name them under small_cells: count_columns."
        )
        return []
    if unnamed:
        notes.append(
            f"{label}Give each aggregate a name (COUNT(*) AS n) so the small-cells check can "
            "name its column; until then it names none."
        )
        return []
    if others:
        one = len(others) == 1
        notes.append(
            f"{label}{', '.join(others)} {'is an aggregate' if one else 'are aggregates'} "
            "DataLab can't tell from a count, so the small-cells check covers "
            f"{'it' if one else 'them'} too. Take one out only if it can't give a count "
            "away (a mean of a measure, say)."
        )
    if not counts and not others:
        notes.append(
            f"{label}The query aggregates, but DataLab couldn't tell which columns are "
            "counts: name them under small_cells: count_columns."
        )
    return [*counts, *others]


class _Block(str):
    """Written as a YAML literal block (`sql: |`)."""


class _Flow(list):
    """Written as a YAML flow sequence (`[a, b]`)."""


class _FlowMap(dict):
    """Written as a YAML flow mapping (`{ type: date, default: … }`)."""


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Lists indented under their key, as in the docs' examples.
        super().increase_indent(flow, False)


def _block(dumper: yaml.SafeDumper, value: _Block) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style="|")


def _flow(dumper: yaml.SafeDumper, value: _Flow) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(value), flow_style=True)


def _flow_map(dumper: yaml.SafeDumper, value: _FlowMap) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", dict(value), flow_style=True)


_Dumper.add_representer(_Block, _block)
_Dumper.add_representer(_FlowMap, _flow_map)
_Dumper.add_representer(_Flow, _flow)


def queries_from_log(records: Sequence[Any], ids: Sequence[str]) -> list[DraftQuery]:
    """The chosen queries of a conversation's Data accessed log, in the log's
    order: only those that succeeded (a refused one never ran)."""
    wanted = set(ids)
    chosen = [r for r in records if r.id in wanted]
    missing = wanted - {r.id for r in chosen}
    if missing:
        raise DraftRefused("Some of those queries aren't in this conversation's log.")
    failed = [r for r in chosen if r.status != "succeeded"]
    if failed:
        raise DraftRefused("Only queries that ran successfully can go into a workflow.")
    return [DraftQuery(r.sql_text, dict(r.binds)) for r in chosen]
