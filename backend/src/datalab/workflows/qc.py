"""Built-in QC: checks DataLab runs itself, on the host, over a step's CSV.

Each check returns `{id, status: pass|fail, observed, expected, message}`,
the same shape as a custom R check's. **Messages hold counts, shares and
column names only, never values or keys**: they go into the run record, the
Workflows tab and the delivery manifest.

The small-cell rule: a count from 1 to `min - 1` (10, with the usual 11)
mustn't be shown; 0 may be. `min` can't be set below SMALL_CELL_FLOOR (11),
in either profile, so a parameter can't turn the rule off. A hidden count
(empty, NA, or text such as "<11") mustn't be recoverable either: every row
total (`total_column`) and group total (`totals`) is an equation over the
hidden cells, and suppression.py works out, across all of them together,
which hidden cells they pin to one value. A declared percentage shown beside
a hidden count gives it away too. A `totals` value that matches no row
(compared ignoring case and spaces) fails, rather than checking nothing.

A header that names a column twice fails too: which of the two a check
read would be anyone's guess.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datalab.workflows.model import (
    SMALL_CELL_FLOOR,
    BuiltinQc,
    Scalar,
    SmallCells,
    param_value,
)
from datalab.workflows.suppression import Equation, audit

MISSING = frozenset({"", "NA", "NaN", "NULL"})


def _check(check_id: str, passed: bool, observed: Any, expected: Any, message: str) -> dict:
    return {
        "id": check_id,
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "message": message,
    }


def _count(value: str) -> float | None:
    """A shown count, or None if the cell is hidden (empty, NA, "<11", …)."""
    if value.strip() in MISSING:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


@dataclass
class _Row:
    """One row of a table the margin check reads: its group, whether it's a
    total row, and its count cells (None where hidden)."""

    group: tuple[str, ...]
    total: bool
    cells: dict[str, float | None]
    percents: dict[str, float | None]


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def builtin_qc(qc: BuiltinQc, path: Path, params: Mapping[str, Scalar]) -> list[dict]:
    """Run a QC step's built-in checks over one CSV file."""
    checks: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        index = {name: i for i, name in enumerate(header)}
        twice = sorted({name for name in header if header.count(name) > 1})
        if twice:
            checks.append(
                _check(
                    "unique_columns",
                    False,
                    len(twice),
                    0,
                    f"the header names these columns more than once: {', '.join(twice)}",
                )
            )

        def cell(row: list[str], column: str) -> str:
            i = index.get(column)
            return row[i] if i is not None and i < len(row) else ""

        missing_counts = {c: 0 for c in {*qc.no_missing, *qc.max_missing} if c in index}
        key_columns = [c for c in (qc.unique_by or ()) if c in index]
        keys: set[bytes] = set()
        duplicates = 0
        rule = qc.small_cells
        minimum = float(param_value(rule.min, params)) if rule else 0.0
        small_shown = 0
        table: list[_Row] = []
        keep_table = rule is not None and bool(
            rule.totals or rule.total_column or rule.percent_columns
        )
        rows = 0
        for row in reader:
            rows += 1
            for column in missing_counts:
                if cell(row, column).strip() in MISSING:
                    missing_counts[column] += 1
            if key_columns and len(key_columns) == len(qc.unique_by or ()):
                key = "\x1f".join(cell(row, c) for c in key_columns)
                digest = hashlib.blake2b(key.encode(), digest_size=12).digest()
                if digest in keys:
                    duplicates += 1
                keys.add(digest)
            if rule is not None:
                small_shown += _small_in_row(rule, row, cell, minimum)
                if keep_table:
                    table.append(_table_row(rule, row, cell))

    def absent(columns: Any) -> list[str]:
        return [c for c in columns if c not in index]

    if qc.min_rows is not None:
        want = int(param_value(qc.min_rows, params))
        checks.append(_check("min_rows", rows >= want, rows, f">= {want}", f"{rows} rows"))
    if qc.max_rows is not None:
        want = int(param_value(qc.max_rows, params))
        checks.append(_check("max_rows", rows <= want, rows, f"<= {want}", f"{rows} rows"))
    if qc.required_columns:
        missing = absent(qc.required_columns)
        checks.append(
            _check(
                "required_columns",
                not missing,
                missing,
                [],
                f"missing columns: {', '.join(missing)}" if missing else "all present",
            )
        )
    for column in qc.no_missing:
        if column not in index:
            checks.append(
                _check(f"no_missing:{column}", False, None, 0, f"{column} isn't in the file")
            )
            continue
        empty = missing_counts[column]
        checks.append(
            _check(f"no_missing:{column}", empty == 0, empty, 0, f"{empty} missing in {column}")
        )
    for column, limit in qc.max_missing.items():
        want = float(param_value(limit, params))
        if column not in index:
            checks.append(
                _check(
                    f"max_missing:{column}",
                    False,
                    None,
                    f"<= {want}",
                    f"{column} isn't in the file",
                )
            )
            continue
        share = missing_counts[column] / rows if rows else 0.0
        checks.append(
            _check(
                f"max_missing:{column}",
                share <= want,
                round(share, 4),
                f"<= {want}",
                f"{column} is {share:.1%} missing",
            )
        )
    if qc.unique_by is not None:
        gone = absent(qc.unique_by)
        if gone:
            checks.append(
                _check("unique_by", False, None, 0, f"not in the file: {', '.join(gone)}")
            )
        else:
            checks.append(
                _check(
                    "unique_by",
                    duplicates == 0,
                    duplicates,
                    0,
                    f"{duplicates} duplicate rows by {', '.join(qc.unique_by)}",
                )
            )
    if rule is not None:
        checks += _small_cell_checks(rule, index, minimum, small_shown, table)
    return checks


def _small_in_row(rule: SmallCells, row: list[str], cell, minimum: float) -> int:
    columns = [*rule.count_columns, *([rule.total_column] if rule.total_column else [])]
    small = 0
    for column in columns:
        value = _count(cell(row, column))
        if value is not None and 1 <= value < minimum:
            small += 1
    return small


def _table_row(rule: SmallCells, row: list[str], cell) -> _Row:
    totals = rule.totals
    columns = [*rule.count_columns, *([rule.total_column] if rule.total_column else [])]
    return _Row(
        group=tuple(cell(row, c) for c in totals.within) if totals else (),
        # Labels are compared as a person reads them: "Total " is "total".
        total=bool(totals) and _label(cell(row, totals.column)) == _label(totals.value),
        cells={c: _count(cell(row, c)) for c in columns},
        percents={p: _count(cell(row, p)) for p in rule.percent_columns},
    )


def _margin_equations(rule: SmallCells, table: list[_Row]) -> tuple[int, list[Equation]]:
    """Every shown or hidden total as an equation over the hidden cells."""
    names: dict[tuple[int, str], int] = {}

    def var(i: int, column: str) -> int:
        return names.setdefault((i, column), len(names))

    equations: list[Equation] = []

    def relation(members: list[tuple[int, str]], total: tuple[int, str]) -> None:
        terms: list[tuple[int, int]] = []
        shown = 0.0
        for i, column in members:
            value = table[i].cells[column]
            if value is None:
                terms.append((var(i, column), 1))
            else:
                shown += value
        value = table[total[0]].cells[total[1]]
        if value is None:
            terms.append((var(*total), -1))
            equations.append(Equation(terms, -shown))
        else:
            equations.append(Equation(terms, value - shown, shown_total=True))

    if rule.total_column:
        for i in range(len(table)):
            relation([(i, c) for c in rule.count_columns], (i, rule.total_column))
    if rule.totals:
        groups: dict[tuple[str, ...], tuple[list[int], list[int]]] = {}
        for i, row in enumerate(table):
            members, totals = groups.setdefault(row.group, ([], []))
            (totals if row.total else members).append(i)
        columns = [*rule.count_columns, *([rule.total_column] if rule.total_column else [])]
        for members, totals in groups.values():
            for column in columns:
                for t in totals:
                    relation([(i, column) for i in members], (t, column))
    return len(names), equations


def _small_cell_checks(
    rule: SmallCells,
    index: Mapping[str, int],
    minimum: float,
    small_shown: int,
    table: list[_Row],
) -> list[dict]:
    wanted = [*rule.count_columns]
    if rule.total_column:
        wanted.append(rule.total_column)
    if rule.totals:
        wanted += [rule.totals.column, *rule.totals.within]
    wanted += list(rule.percent_columns)
    gone = [c for c in wanted if c not in index]
    if gone:
        return [_check("small_cells", False, None, 0, f"not in the file: {', '.join(gone)}")]
    columns = ", ".join(rule.count_columns)
    shown_min = int(minimum) if minimum.is_integer() else minimum
    if minimum < SMALL_CELL_FLOOR:
        return [
            _check(
                "small_cells",
                False,
                shown_min,
                f">= {SMALL_CELL_FLOOR}",
                f"the small-cell minimum is {shown_min}; it can't be below {SMALL_CELL_FLOOR}",
            )
        ]
    checks = [
        _check(
            "small_cells",
            small_shown == 0,
            small_shown,
            0,
            f"{small_shown} shown counts in {columns} below {shown_min} and above 0",
        )
    ]
    if rule.totals is not None and not any(row.total for row in table):
        checks.append(
            _check(
                "small_cells_totals",
                False,
                0,
                ">= 1",
                f"no row has {rule.totals.value!r} in {rule.totals.column}, so the totals "
                "can't be checked",
            )
        )
    if rule.totals is not None or rule.total_column is not None:
        variables, equations = _margin_equations(rule, table)
        found = audit(variables, equations, minimum)
        recoverable = len(found.recoverable)
        if found.inconsistent:
            message = (
                "the totals don't add up with the counts shown, so hidden counts can't be checked"
            )
        elif found.unsettled:
            message = "the margin check didn't settle, so hidden counts can't be checked"
        else:
            message = f"{recoverable} hidden counts in {columns} can be worked out from the totals"
            if found.zeros:
                # A hidden 0 the totals show gives no small count away.
                zeros = len(found.zeros)
                message += f"; {zeros} hidden count{' is' if zeros == 1 else 's are'} 0"
        checks.append(
            _check(
                "small_cells_recoverable",
                recoverable == 0 and not found.inconsistent and not found.unsettled,
                recoverable,
                0,
                message,
            )
        )
    if rule.percent_columns:
        given_away = sum(
            1
            for row in table
            for pct, count in rule.percent_columns.items()
            if row.cells.get(count, 0) is None and row.percents.get(pct) is not None
        )
        checks.append(
            _check(
                "small_cells_percentages",
                given_away == 0,
                given_away,
                0,
                f"{given_away} hidden counts have their percentage shown beside them",
            )
        )
    return checks
