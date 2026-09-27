"""Compare one output file from the prototype with the same file from v1.

Everything this module returns is structural or aggregate: column names,
row counts, per-column value *shapes* (such as `YYYY-MM-DD HH:MM:SS`),
missing and distinct counts, and for numeric columns the count, mean and
sum. It never returns a row, a cell, a minimum or a maximum, even for
synthetic data, so a report built from it can be pasted anywhere.

Verdicts, strictest first:

- `identical`: the same bytes.
- `same_rows`: the same header and the same rows in the same order, after
  reading both as CSV (so only quoting or line endings differ).
- `same_rows_any_order`: the same rows, in another order. Used only where
  the order isn't defined: an extract whose SQL has no ORDER BY, and R
  steps that keep the extract's order. The report says which verdict each
  output got, so a reordering is never hidden.
- `equal_within_tolerance`: the same after numbers are compared with a
  relative tolerance of 1e-9 (FLOAT_RTOL) and no other change. R writes
  15 significant digits, and two R versions, or a sum taken in another
  order, can differ in the last one; nothing a scientist reads is at 1e-9.
- `differs`: anything else, with what differs per column.

Where the values differ only in how a date or time is written (`2025-05-03`
against `2025-05-03 00:00:00`, or a `T` for the space), the column is
marked `datetime_format_only`, since that is a different cause from a
different value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FLOAT_RTOL = 1e-9
FLOAT_ATOL = 1e-12
MISSING = {"", "NA", "NaN", "NULL"}

_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:([ T])(\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?"
)


@dataclass
class Table:
    header: list[str]
    rows: list[tuple[str, ...]]


def read_table(path: Path) -> Table:
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return Table([], [])
    width = len(header)
    rows = [tuple(r) + ("",) * (width - len(r)) if len(r) < width else tuple(r) for r in reader]
    return Table(header, rows)


def shape(value: str) -> str:
    """The value's shape with no content: `int`, `num`, `YYYY-MM-DD HH:MM:SS`, `text`…"""
    if value in MISSING:
        return "missing"
    if _NUMBER.fullmatch(value):
        return "int" if re.fullmatch(r"[+-]?\d+", value) else "num"
    match = _DATETIME.fullmatch(value)
    if match:
        sep, hh, ss, frac, zone = match.group(4, 5, 7, 8, 9)
        if hh is None:
            return "YYYY-MM-DD"
        out = f"YYYY-MM-DD{sep}HH:MM"
        if ss is not None:
            out += ":SS"
        if frac:
            out += ".f"
        if zone:
            out += "Z"
        return out
    if value.lower() in {"true", "false"}:
        return "bool"
    return "text"


def canonical_datetime(value: str) -> str | None:
    """A date or time written one way, so format-only differences can be told apart."""
    match = _DATETIME.fullmatch(value)
    if not match:
        return None
    y, m, d, _sep, hh, mm, ss, frac, zone = match.groups()
    if zone:
        return None
    hh, mm, ss = hh or "00", mm or "00", ss or "00"
    return f"{y}-{m}-{d} {hh}:{mm}:{ss}{frac or ''}"


def _as_float(value: str) -> float | None:
    if value in MISSING or not _NUMBER.fullmatch(value):
        return None
    return float(value)


def numbers_close(a: str, b: str) -> bool:
    x, y = _as_float(a), _as_float(b)
    if x is None or y is None:
        return False
    return math.isclose(x, y, rel_tol=FLOAT_RTOL, abs_tol=FLOAT_ATOL)


def column_summary(values: list[str]) -> dict[str, Any]:
    shapes = Counter(shape(v) for v in values)
    out: dict[str, Any] = {
        "missing": shapes.pop("missing", 0),
        "distinct": len(set(values)),
        "shapes": dict(sorted(shapes.items())),
    }
    numbers = [f for v in values if (f := _as_float(v)) is not None]
    if numbers and len(numbers) == len(values) - out["missing"]:
        total = math.fsum(numbers)
        out["numeric"] = {"count": len(numbers), "sum": total, "mean": total / len(numbers)}
    return out


def _normal(value: str) -> str:
    return canonical_datetime(value) or value


def _cell_diff(a: str, b: str) -> str:
    if a == b:
        return "same"
    if (a in MISSING) and (b in MISSING):
        return "missing_spelling"
    if numbers_close(a, b):
        return "float_tolerance"
    ca, cb = canonical_datetime(a), canonical_datetime(b)
    if ca is not None and ca == cb:
        return "datetime_format"
    return "value"


@dataclass
class Comparison:
    name: str
    verdict: str
    notes: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "verdict": self.verdict, "notes": self.notes, **self.detail}


def _digest(row: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(row).encode()).hexdigest()


def compare_csv(name: str, prototype: Path, v1: Path, *, order_defined: bool = True) -> Comparison:
    """Compare two CSV files, structurally and cell by cell (without showing cells).

    `order_defined`: the producing step sorts its rows, so a reordering is a
    difference. When it doesn't, rows in another order are `same_rows_any_order`.
    """
    a_bytes, b_bytes = prototype.read_bytes(), v1.read_bytes()
    a, b = read_table(prototype), read_table(v1)
    detail: dict[str, Any] = {
        "bytes": {"prototype": len(a_bytes), "v1": len(b_bytes)},
        "rows": {"prototype": len(a.rows), "v1": len(b.rows)},
        "columns": {"prototype": len(a.header), "v1": len(b.header)},
    }
    notes: list[str] = []
    if a_bytes == b_bytes:
        detail["sha256"] = hashlib.sha256(a_bytes).hexdigest()
        return Comparison(name, "identical", notes, detail)

    only_a = [c for c in a.header if c not in b.header]
    only_b = [c for c in b.header if c not in a.header]
    common = [c for c in a.header if c in b.header]
    if only_a or only_b:
        detail["only_in_prototype"] = only_a
        detail["only_in_v1"] = only_b
        notes.append(f"{len(only_a)} columns only in the prototype, {len(only_b)} only in v1.")
    if common != [c for c in b.header if c in a.header]:
        notes.append("The shared columns are in a different order.")
    ia = [a.header.index(c) for c in common]
    ib = [b.header.index(c) for c in common]
    ra = [tuple(r[i] for i in ia) for r in a.rows]
    rb = [tuple(r[i] for i in ib) for r in b.rows]

    same_header = a.header == b.header
    if same_header and a.rows == b.rows:
        return Comparison(name, "same_rows", ["Same rows; only the CSV quoting differs."], detail)
    if same_header and Counter(map(_digest, ra)) == Counter(map(_digest, rb)):
        verdict = "differs" if order_defined else "same_rows_any_order"
        notes.append("The same rows in another order.")
        return Comparison(name, verdict, notes, detail)

    # Align the rows: sort both on the columns whose values agree as a whole
    # (a key, usually), then compare cell by cell. Dates and times are
    # compared as instants here, so a column written another way still counts.
    na = [tuple(_normal(v) for v in r) for r in ra]
    nb = [tuple(_normal(v) for v in r) for r in rb]
    stable = [
        k for k in range(len(common)) if Counter(r[k] for r in na) == Counter(r[k] for r in nb)
    ]
    order_a = sorted(range(len(ra)), key=lambda i: (tuple(na[i][k] for k in stable), na[i]))
    order_b = sorted(range(len(rb)), key=lambda i: (tuple(nb[i][k] for k in stable), nb[i]))
    positional = order_defined or not stable
    sa = ra if positional else [ra[i] for i in order_a]
    sb = rb if positional else [rb[i] for i in order_b]
    if stable:
        keys = [tuple(_normal(r[k]) for k in stable) for r in sa]
        detail["alignment"] = {
            "by": "row order" if positional else "shared columns",
            "unique": len(set(keys)) == len(keys),
            "key_columns": len(stable),
        }
    per_column: dict[str, dict[str, Any]] = {}
    kinds_seen: Counter[str] = Counter()
    for k, column in enumerate(common):
        diffs = Counter(_cell_diff(x[k], y[k]) for x, y in zip(sa, sb, strict=False))
        diffs.pop("same", None)
        values_a, values_b = [r[k] for r in ra], [r[k] for r in rb]
        sum_a, sum_b = column_summary(values_a), column_summary(values_b)
        if not diffs and sum_a == sum_b:
            continue
        kinds_seen.update(diffs.keys())
        entry: dict[str, Any] = {"cells": dict(sorted(diffs.items()))}
        if diffs and set(diffs) <= {"datetime_format"}:
            entry["cause"] = "datetime_format_only"
        entry["prototype"], entry["v1"] = sum_a, sum_b
        per_column[column] = entry
    detail["columns_differing"] = per_column
    if len(a.rows) != len(b.rows):
        notes.append("Different row counts, so cell comparisons past the shorter file are skipped.")
        return Comparison(name, "differs", notes, detail)
    if same_header and kinds_seen and kinds_seen.keys() <= {"float_tolerance", "missing_spelling"}:
        notes.append(f"Numbers agree to a relative {FLOAT_RTOL:g}.")
        return Comparison(name, "equal_within_tolerance", notes, detail)
    return Comparison(name, "differs", notes, detail)


def compare_json_qc(name: str, prototype: dict[str, Any], v1: dict[str, Any]) -> Comparison:
    """Compare two QC outcomes: passed or not, and the row count checked."""
    same = prototype == v1
    return Comparison(
        name,
        "identical" if same else "differs",
        [] if same else ["The QC outcomes differ."],
        {"prototype": prototype, "v1": v1},
    )


def compare_file(name: str, prototype: Path | None, v1: Path | None, **kw: Any) -> Comparison:
    if prototype is None or v1 is None:
        missing = [s for s, p in (("prototype", prototype), ("v1", v1)) if p is None]
        return Comparison(name, "missing", [f"No file from {' or '.join(missing)}."])
    if prototype.suffix.lower() == ".csv":
        return compare_csv(name, prototype, v1, **kw)
    same = prototype.read_bytes() == v1.read_bytes()
    return Comparison(name, "identical" if same else "differs")
