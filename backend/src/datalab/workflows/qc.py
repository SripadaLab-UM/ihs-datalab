"""Built-in QC: checks DataLab runs itself, on the host, over a step's CSV.

Each check returns `{id, status: pass|fail, observed, expected, message}`,
the same shape as a custom R check's. **Messages hold counts, shares and
column names only, never values or keys**: they go into the run record, the
Workflows tab and the delivery manifest.

The small-cell rule: a count from 1 to `min - 1` (10, with the usual 11)
mustn't be shown; 0 may be. A hidden count (empty, NA, or text such as
"<11") mustn't be recoverable either: when a total is shown, a single
hidden cell among the cells it adds up could be worked out by subtraction,
and so could several hidden cells whose combined count is itself small.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datalab.workflows.model import BuiltinQc, Scalar, SmallCells, param_value

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
class _Margin:
    """The cells one total adds up, for one count column."""

    total: float | None = None
    total_seen: bool = False
    shown: float = 0.0
    hidden: int = 0

    def add(self, value: float | None) -> None:
        if value is None:
            self.hidden += 1
        else:
            self.shown += value

    def recoverable(self, minimum: float) -> int:
        """How many hidden cells a shown total gives away."""
        if not self.total_seen or self.total is None or self.hidden == 0:
            return 0
        if self.hidden == 1:
            return 1
        rest = self.total - self.shown
        return self.hidden if 1 <= rest < minimum else 0


def builtin_qc(qc: BuiltinQc, path: Path, params: Mapping[str, Scalar]) -> list[dict]:
    """Run a QC step's built-in checks over one CSV file."""
    checks: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        index = {name: i for i, name in enumerate(header)}

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
        margins: dict[tuple[str, tuple[str, ...]], _Margin] = {}
        row_margins_recoverable = 0
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
                _add_to_margins(rule, row, cell, margins)
                row_margins_recoverable += _row_total_recoverable(rule, row, cell, minimum)

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
        checks += _small_cell_checks(
            rule, index, minimum, small_shown, margins, row_margins_recoverable
        )
    return checks


def _small_in_row(rule: SmallCells, row: list[str], cell, minimum: float) -> int:
    columns = [*rule.count_columns, *([rule.total_column] if rule.total_column else [])]
    small = 0
    for column in columns:
        value = _count(cell(row, column))
        if value is not None and 1 <= value < minimum:
            small += 1
    return small


def _add_to_margins(rule: SmallCells, row: list[str], cell, margins: dict) -> None:
    totals = rule.totals
    if totals is None:
        return
    within = tuple(cell(row, c) for c in totals.within)
    is_total = cell(row, totals.column) == totals.value
    for column in rule.count_columns:
        margin = margins.setdefault((column, within), _Margin())
        value = _count(cell(row, column))
        if is_total:
            margin.total_seen = True
            margin.total = value
        else:
            margin.add(value)


def _row_total_recoverable(rule: SmallCells, row: list[str], cell, minimum: float) -> int:
    if rule.total_column is None:
        return 0
    margin = _Margin(total=_count(cell(row, rule.total_column)), total_seen=True)
    for column in rule.count_columns:
        margin.add(_count(cell(row, column)))
    return margin.recoverable(minimum)


def _small_cell_checks(
    rule: SmallCells,
    index: Mapping[str, int],
    minimum: float,
    small_shown: int,
    margins: Mapping[tuple[str, tuple[str, ...]], _Margin],
    row_recoverable: int,
) -> list[dict]:
    wanted = [*rule.count_columns]
    if rule.total_column:
        wanted.append(rule.total_column)
    if rule.totals:
        wanted += [rule.totals.column, *rule.totals.within]
    gone = [c for c in wanted if c not in index]
    if gone:
        return [_check("small_cells", False, None, 0, f"not in the file: {', '.join(gone)}")]
    columns = ", ".join(rule.count_columns)
    shown_min = int(minimum) if minimum.is_integer() else minimum
    checks = [
        _check(
            "small_cells",
            small_shown == 0,
            small_shown,
            0,
            f"{small_shown} shown counts in {columns} below {shown_min} and above 0",
        )
    ]
    if rule.totals is not None or rule.total_column is not None:
        recoverable = row_recoverable + sum(m.recoverable(minimum) for m in margins.values())
        checks.append(
            _check(
                "small_cells_recoverable",
                recoverable == 0,
                recoverable,
                0,
                f"{recoverable} hidden counts in {columns} can be worked out from a total",
            )
        )
    return checks
