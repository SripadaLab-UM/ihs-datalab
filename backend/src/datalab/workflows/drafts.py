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
- a parameter for each bind, lower case, typed from the value it was run
  with (a date, a whole number, a number, or text). That value becomes the
  default, unless it looks like it could identify someone (the same scan as
  export folder names): then there's no default, and a note says why;
- a built-in check: at least one row, the columns the query names (when it
  names them all, without `*`), and, when the query aggregates, a
  `small_cells` check over its `COUNT` columns. If DataLab can't tell which
  columns are counts, the check names none, so the file check asks for them.

and, if the person picked a destination key, `deliver:` of every output.

A query the SQL check refuses doesn't become a draft (`DraftRefused`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from datalab.data.sqlcheck import ColumnIndex, SqlRejected, check_sql
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

        given = {key.lower(): value for key, value in query.binds.items()}
        for bind in checked.binds:
            param = bind.lower()
            value = given.get(param)
            declared, default, why = _parameter(param, value)
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

        tree = sqlglot.parse_one(sql, read="oracle")
        qc: dict[str, Any] = {"file": step_id, "min_rows": 1}
        named = _output_columns(tree)
        if named is not None and named:
            qc["required_columns"] = _Flow(named)
        if _aggregates(tree):
            counts, unnamed = _count_columns(tree)
            if unnamed:
                notes.append(
                    f"{label}Give each COUNT(...) a name (COUNT(*) AS n) so the small-cells "
                    "check can name its column."
                )
            if not counts:
                notes.append(
                    f"{label}The query aggregates, but DataLab couldn't tell which columns are "
                    "counts: name them under small_cells: count_columns."
                )
            qc["small_cells"] = {"count_columns": _Flow(counts), "min": SMALL_CELL_FLOOR}
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


def _parameter(name: str, value: BindValue) -> tuple[dict[str, Any], Any, str | None]:
    """A bind's parameter declaration, its default, and a note, if any."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return {"type": "string"}, None, f":{name} had no value, so it has no default."
    if isinstance(value, bool):
        return {"type": "boolean", "default": value}, value, None
    if isinstance(value, int):
        kind, default = "integer", value
    elif isinstance(value, float):
        kind, default = "number", value
    else:
        text = value.strip()
        if _DATE.fullmatch(text) and _is_date(text):
            # A date range is what a workflow's parameters usually are.
            return {"type": "date", "default": text}, text, None
        if _INTEGER.fullmatch(text):
            kind, default = "integer", int(text)
        elif _NUMBER.fullmatch(text):
            kind, default = "number", float(text)
        else:
            kind, default = "string", value
    if scrub_title(str(value)) != normalize_title(str(value)):
        # It goes into a file others may read (and, shared, into git history).
        return (
            {"type": kind},
            None,
            f"The value used for :{name} looks like it could identify someone, so it isn't "
            "kept as the default: give it when you run the workflow.",
        )
    return {"type": kind, "default": default}, default, None


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


def _count_columns(tree: exp.Expr) -> tuple[list[str], bool]:
    """The outputs that are counts, and whether a count had no name to give."""
    counts: list[str] = []
    unnamed = False
    for expression in _selects(tree):
        if expression.find(exp.Count) is None:
            continue
        name = _column_name(expression)
        if name is None:
            unnamed = True
        elif name not in counts:
            counts.append(name)
    return counts, unnamed


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
