"""Can hidden counts be worked out from the totals shown beside them?

A table's hidden (suppressed) counts are unknowns; every total it shows, of
a row (`total_column`) or of a group of rows (`totals`), is a linear
equation over them: the hidden cells it adds up, less a hidden total, equal
what's left of the shown numbers. Each hidden cell starts with the interval
[0, min - 1]: a small count, 0 included, since the usual R suppression
(`ifelse(n < 11, NA, n)`) hides zeros too.

Two things are then worked out, over every equation together:

1. **Exact determination.** Within each group of hidden cells linked by
   shared equations, Gaussian elimination (over exact fractions) finds every
   cell the equations fix on their own, whatever the bounds.
2. **Interval propagation.** Each equation tightens each of its cells'
   intervals from the others' (`x = c - sum(others)`), round after round
   across all row and column equations, until nothing changes.

A hidden cell is recoverable when either leaves it one value from 1 to
min - 1. One left only 0 gives no small count away, so it passes (and is
noted); one left a value of min or more isn't a small count either. If the
small premise can't hold (the equations need some hidden cell to be 11 or
more, as when a large cell is hidden to protect a small one), the
propagation is repeated with only the top widened, [0, infinity), which is
still sound. If even that fails, the shown totals don't add up, and nothing
can be said: the check fails.

Sound, not complete: a cell reported safe could still be narrowed by
reasoning that combines bounds and equations more cleverly (integer
programming), and cells in a linked group of more than MAX_EXACT cells skip
step 1. The check fails, rather than passes, when propagation doesn't settle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

MAX_EXACT = 400
MAX_SWEEPS = 5000


@dataclass
class Equation:
    """sum(coef * x[var]) == constant, over hidden cells."""

    terms: list[tuple[int, int]]  # (var, +1 or -1)
    constant: float
    # A shown total with several hidden cells: their sum is shown, in effect.
    shown_total: bool = False


@dataclass
class Audit:
    recoverable: set[int] = field(default_factory=set)  # pinned to a small count
    zeros: set[int] = field(default_factory=set)  # pinned to 0: no small count shown
    inconsistent: bool = False
    unsettled: bool = False


def audit(variables: int, equations: list[Equation], minimum: float) -> Audit:
    """Which hidden cells (0 … variables-1) the equations give away."""
    result = Audit()
    equations = [e for e in equations if e.terms]
    if not variables or not equations:
        return result
    high = math.ceil(minimum) - 1  # the largest small count

    def pinned(var: int, value: float) -> None:
        if value == 0:
            result.zeros.add(var)
        elif 0 < value <= high:
            result.recoverable.add(var)

    # A shown total whose hidden cells add up to a small count gives that away.
    for equation in equations:
        positive = [v for v, a in equation.terms if a > 0]
        whole = equation.shown_total and len(positive) == len(equation.terms) >= 2
        if whole and 1 <= equation.constant < minimum:
            result.recoverable.update(positive)

    for var, value in _determined(variables, equations).items():
        pinned(var, float(value))

    # Hidden cells are small counts, 0 included (ifelse(n < 11, NA, n) hides
    # zeros too); if that can't hold, some are large: widen only the top.
    for upper in (float(high), math.inf):
        intervals = _propagate(variables, equations, upper)
        if intervals is None:
            continue
        if isinstance(intervals, str):  # "unsettled"
            result.unsettled = True
            return result
        for var, (lo, hi) in enumerate(intervals):
            if lo == hi:
                pinned(var, lo)
        return result
    result.inconsistent = True
    return result


def _determined(variables: int, equations: list[Equation]) -> dict[int, Fraction]:
    """Cells the equations alone fix, and their values: e_j is in the row space."""
    parent = list(range(variables))

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for equation in equations:
        first = equation.terms[0][0]
        for var, _ in equation.terms[1:]:
            parent[find(var)] = find(first)
    groups: dict[int, list[Equation]] = {}
    for equation in equations:
        groups.setdefault(find(equation.terms[0][0]), []).append(equation)
    fixed: dict[int, Fraction] = {}
    for group in groups.values():
        names = sorted({v for e in group for v, _ in e.terms})
        if len(names) > MAX_EXACT:
            continue
        column = {v: i for i, v in enumerate(names)}
        rows = []
        for equation in group:
            row = [Fraction(0)] * (len(names) + 1)
            for var, coef in equation.terms:
                row[column[var]] += coef
            row[-1] = Fraction(equation.constant).limit_denominator(10**6)
            rows.append(row)
        for r, c in _reduce(rows, len(names)):
            if all(x == 0 for i, x in enumerate(rows[r][:-1]) if i != c):
                fixed[names[c]] = rows[r][-1]
    return fixed


def _reduce(rows: list[list[Fraction]], width: int) -> list[tuple[int, int]]:
    """Reduced row echelon form, in place, pivoting on the first `width`
    columns (the rest is the constants); returns the (row, column) pivots."""
    pivots = []
    r = 0
    for c in range(width):
        pick = next((i for i in range(r, len(rows)) if rows[i][c] != 0), None)
        if pick is None:
            continue
        rows[r], rows[pick] = rows[pick], rows[r]
        lead = rows[r][c]
        rows[r] = [x / lead for x in rows[r]]
        for i in range(len(rows)):
            if i != r and rows[i][c] != 0:
                factor = rows[i][c]
                rows[i] = [x - factor * y for x, y in zip(rows[i], rows[r], strict=True)]
        pivots.append((r, c))
        r += 1
        if r == len(rows):
            break
    return pivots


def _propagate(
    variables: int, equations: list[Equation], upper: float
) -> list[tuple[float, float]] | str | None:
    """Tighten every cell's interval until nothing changes.

    None if the intervals become empty (the premise can't hold), "unsettled"
    if they keep changing past MAX_SWEEPS.
    """
    lo = [0.0] * variables
    hi = [upper] * variables
    for _ in range(MAX_SWEEPS):
        changed = False
        for equation in equations:
            # The least and most each term can add, with infinities counted apart.
            low_parts = [(a * lo[v] if a > 0 else -hi[v]) for v, a in equation.terms]
            high_parts = [(a * hi[v] if a > 0 else -lo[v]) for v, a in equation.terms]
            low_sum, low_inf = _finite_sum(low_parts)
            high_sum, high_inf = _finite_sum(high_parts)
            for (var, coef), low_part, high_part in zip(
                equation.terms, low_parts, high_parts, strict=True
            ):
                others_low = (
                    -math.inf
                    if low_inf - (1 if math.isinf(low_part) else 0) > 0
                    else low_sum - (0 if math.isinf(low_part) else low_part)
                )
                others_high = (
                    math.inf
                    if high_inf - (1 if math.isinf(high_part) else 0) > 0
                    else high_sum - (0 if math.isinf(high_part) else high_part)
                )
                # coef * x = constant - others
                first, last = equation.constant - others_high, equation.constant - others_low
                if coef < 0:
                    first, last = -last, -first
                new_lo = math.ceil(first - 1e-9) if math.isfinite(first) else lo[var]
                new_hi = math.floor(last + 1e-9) if math.isfinite(last) else hi[var]
                if new_lo > lo[var]:
                    lo[var], changed = float(new_lo), True
                if new_hi < hi[var]:
                    hi[var], changed = float(new_hi), True
                if lo[var] > hi[var]:
                    return None
        if not changed:
            return list(zip(lo, hi, strict=True))
    return "unsettled"


def _finite_sum(parts: list[float]) -> tuple[float, int]:
    total, infinite = 0.0, 0
    for part in parts:
        if math.isinf(part):
            infinite += 1
        else:
            total += part
    return total, infinite
