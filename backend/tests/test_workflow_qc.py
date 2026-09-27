"""Built-in QC: counts only in every message, and the small-cell rule."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from datalab.workflows.model import BuiltinQc
from datalab.workflows.qc import builtin_qc


def write(path: Path, header: list[str], rows: list[list]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def checks(path: Path, params: dict | None = None, **config) -> dict[str, dict]:
    qc = BuiltinQc.model_validate({"file": "x", **config})
    return {c["id"]: c for c in builtin_qc(qc, path, params or {})}


def test_rows_columns_missingness_and_duplicates(tmp_path):
    path = write(
        tmp_path / "x.csv",
        ["STUDY_PARTICIPANT_ID", "RECORD_DATE", "STEPS"],
        [
            ["SYN-0001", "2025-04-01", "8123"],
            ["SYN-0001", "2025-04-01", ""],
            ["SYN-0002", "2025-04-01", "NA"],
            ["SYN-0003", "2025-04-02", "4500"],
        ],
    )
    found = checks(
        path,
        {"least": 5},
        min_rows="$least",
        max_rows=10,
        required_columns=["STUDY_PARTICIPANT_ID", "HRV"],
        no_missing=["STEPS", "RECORD_DATE"],
        max_missing={"STEPS": 0.25},
        unique_by=["STUDY_PARTICIPANT_ID", "RECORD_DATE"],
    )
    assert found["min_rows"]["status"] == "fail" and found["min_rows"]["observed"] == 4
    assert found["max_rows"]["status"] == "pass"
    assert found["required_columns"]["observed"] == ["HRV"]
    assert found["no_missing:STEPS"]["observed"] == 2
    assert found["no_missing:RECORD_DATE"]["status"] == "pass"
    assert found["max_missing:STEPS"]["observed"] == 0.5
    assert found["unique_by"]["observed"] == 1
    # Messages hold counts, shares and column names, never values or keys.
    assert "SYN-" not in json.dumps(found) and "2025-04" not in json.dumps(found)


def test_small_counts_fail_and_zero_is_allowed(tmp_path):
    path = write(
        tmp_path / "x.csv",
        ["DEVICE", "n"],
        [["fitbit", "40"], ["garmin", "0"], ["apple", "10"], ["oura", "1"], ["other", "11"]],
    )
    small = checks(path, small_cells={"count_columns": ["n"], "min": 11})["small_cells"]
    assert small["status"] == "fail" and small["observed"] == 2
    ok = write(tmp_path / "ok.csv", ["DEVICE", "n"], [["fitbit", "40"], ["garmin", "0"]])
    assert checks(ok, small_cells={"count_column": "n"})["small_cells"]["status"] == "pass"


def test_a_hidden_cell_mustnt_be_recoverable_from_a_total(tmp_path):
    rule = {
        "count_columns": ["n"],
        "min": 11,
        "totals": {"column": "DEVICE", "value": "All", "within": ["week"]},
    }
    header = ["week", "DEVICE", "n"]
    # One hidden cell next to its total: 60 - 30 - 25 = 5 gives it away.
    one = write(
        tmp_path / "one.csv",
        header,
        [["W1", "fitbit", "30"], ["W1", "garmin", "25"], ["W1", "apple", ""], ["W1", "All", "60"]],
    )
    found = checks(one, small_cells=rule)
    assert found["small_cells"]["status"] == "pass"  # nothing small is shown
    assert found["small_cells_recoverable"]["status"] == "fail"
    assert found["small_cells_recoverable"]["observed"] == 1
    # Two hidden cells whose combined count is itself small give that away too.
    pair = write(
        tmp_path / "pair.csv",
        header,
        [
            ["W1", "fitbit", "50"],
            ["W1", "garmin", "<11"],
            ["W1", "apple", "<11"],
            ["W1", "All", "55"],
        ],
    )
    assert checks(pair, small_cells=rule)["small_cells_recoverable"]["observed"] == 2
    # Complementary suppression: two hidden cells adding up to 11 or more,
    # that could be split more than one way, is fine.
    safe = write(
        tmp_path / "safe.csv",
        header,
        [
            ["W1", "fitbit", "41"],
            ["W1", "garmin", ""],
            ["W1", "apple", ""],
            ["W1", "All", "60"],
            ["W2", "fitbit", "12"],
            ["W2", "garmin", "13"],
            ["W2", "All", "25"],
        ],
    )
    assert checks(safe, small_cells=rule)["small_cells_recoverable"]["status"] == "pass"
    # No total shown: nothing to subtract from.
    hidden = write(tmp_path / "hidden.csv", header, [["W1", "fitbit", "30"], ["W1", "apple", ""]])
    assert checks(hidden, small_cells=rule)["small_cells_recoverable"]["status"] == "pass"


def test_a_total_column_counts_too(tmp_path):
    rule = {"count_columns": ["male", "female"], "total_column": "total", "min": "$min"}
    path = write(
        tmp_path / "x.csv",
        ["group", "male", "female", "total"],
        [["a", "20", "", "24"], ["b", "20", "30", "50"]],
    )
    found = checks(path, {"min": 11}, small_cells=rule)
    assert found["small_cells_recoverable"]["observed"] == 1
    assert "20" not in found["small_cells_recoverable"]["message"]


def test_hidden_cells_the_upper_bound_pins_down_fail(tmp_path):
    """Hidden cells are each at most 10: two under a rest of 20 must both be 10."""
    rule = {"count_columns": ["n"], "totals": {"column": "DEVICE", "value": "All"}}
    header = ["DEVICE", "n"]

    def recoverable(total: int, hidden: int, shown: tuple[int, ...] = ()) -> int:
        rows = [[f"d{i}", str(v)] for i, v in enumerate(shown)]
        rows += [[f"h{i}", "<11"] for i in range(hidden)]
        path = write(tmp_path / f"m{total}-{hidden}.csv", header, [*rows, ["All", str(total)]])
        return checks(path, small_cells=rule)["small_cells_recoverable"]["observed"]

    assert recoverable(20, 2) == 2  # both 10
    assert recoverable(19, 2) == 0  # 9 and 10, either way round
    assert recoverable(30, 3) == 3  # all 10
    assert recoverable(29, 3) == 0
    assert recoverable(50, 3, (20,)) == 3  # 50 - 20 = 30: all 10
    assert recoverable(51, 3, (20,)) == 0  # more than three small cells can hold
    assert recoverable(12, 2, (1,)) == 0  # 11 left: 1 to 10 each, many ways
    assert recoverable(12, 2, (2,)) == 2  # 10 left: a small combined count
    assert recoverable(15, 3, (10,)) == 3  # 5 left: a small combined count


def test_the_small_cell_minimum_has_a_floor(tmp_path):
    path = write(tmp_path / "x.csv", ["DEVICE", "n"], [["fitbit", "40"], ["apple", "3"]])
    for low in (10, 1, 0, -5):
        small = checks(path, {"m": low}, small_cells={"count_columns": ["n"], "min": "$m"})
        assert small["small_cells"]["status"] == "fail", low
        assert "can't be below 11" in small["small_cells"]["message"]
    raised = checks(path, {"m": 20}, small_cells={"count_columns": ["n"], "min": "$m"})
    assert raised["small_cells"]["observed"] == 1


def test_a_column_named_twice_fails(tmp_path):
    """Checks read one column of a name; a small count in the other mustn't hide."""
    path = write(tmp_path / "x.csv", ["DEVICE", "n", "n"], [["apple", "3", "40"]])
    found = checks(path, small_cells={"count_columns": ["n"]})
    assert found["unique_columns"]["status"] == "fail"
    assert found["unique_columns"]["message"].endswith(": n")


BOTH = {
    "count_columns": ["c1", "c2", "c3"],
    "total_column": "total",
    "totals": {"column": "row", "value": "All"},
}
HEADER = ["row", "c1", "c2", "c3", "total"]


def test_row_and_column_totals_together_give_hidden_cells_away(tmp_path):
    """The re-review's pattern: each margin alone leaves every hidden cell two
    or more values, but together they pin all six. r3's hide 19, so each is 9
    or 10; c2 then leaves r1's second 9 or 10, so r1's first is 1 or 2; c1
    hides 12, so r2's first is 10, and the rest follow."""
    rows = [
        ["r1", "<11", "<11", "30", "41"],  # 2, 9
        ["r2", "<11", "25", "<11", "37"],  # 10, 2
        ["r3", "40", "<11", "<11", "59"],  # 10, 9
        ["All", "52", "44", "41", "137"],
    ]
    path = write(tmp_path / "x.csv", HEADER, rows)
    found = checks(path, small_cells=BOTH)
    assert found["small_cells_recoverable"]["status"] == "fail"
    assert found["small_cells_recoverable"]["observed"] == 6


def test_a_suppression_pattern_that_is_truly_safe_passes(tmp_path):
    # A 2 x 2 block of hidden cells, each row and column hiding 15: every
    # cell could be anything from 5 to 10.
    rows = [
        ["r1", "<11", "<11", "30", "45"],
        ["r2", "<11", "<11", "25", "40"],
        ["r3", "40", "35", "20", "95"],
        ["All", "55", "50", "75", "180"],
    ]
    found = checks(write(tmp_path / "x.csv", HEADER, rows), small_cells=BOTH)
    assert found["small_cells_recoverable"]["status"] == "pass", found


def test_margins_that_conflict_with_the_small_premise_or_each_other(tmp_path):
    rule = {"count_columns": ["n"], "totals": {"column": "DEVICE", "value": "All"}}
    header = ["DEVICE", "n"]
    # A large cell hidden to protect a small one: 43 can't be two small
    # counts, so the check widens its premise, and neither cell is pinned.
    large = write(tmp_path / "large.csv", header, [["a", "<11"], ["b", ""], ["All", "43"]])
    assert checks(large, small_cells=rule)["small_cells_recoverable"]["status"] == "pass"
    # Totals that can't add up with the cells shown: nothing can be said.
    wrong = write(tmp_path / "wrong.csv", header, [["a", "30"], ["b", "<11"], ["All", "20"]])
    found = checks(wrong, small_cells=rule)["small_cells_recoverable"]
    assert found["status"] == "fail" and "don't add up" in found["message"]


def test_total_rows_are_found_as_a_person_reads_them(tmp_path):
    rule = {"count_columns": ["n"], "totals": {"column": "DEVICE", "value": "All"}}
    header = ["DEVICE", "n"]
    spaced = write(tmp_path / "spaced.csv", header, [["a", "30"], ["b", "<11"], [" all ", "35"]])
    found = checks(spaced, small_cells=rule)
    assert found["small_cells_recoverable"]["observed"] == 1  # "all" was the total
    misspelt = write(tmp_path / "misspelt.csv", header, [["a", "30"], ["Totl", "35"]])
    missing = checks(misspelt, small_cells=rule)["small_cells_totals"]
    assert missing["status"] == "fail" and "no row has 'All'" in missing["message"]


def test_a_percentage_beside_a_hidden_count_gives_it_away(tmp_path):
    rule = {"count_columns": ["n"], "percent_columns": {"pct": "n"}}
    header = ["DEVICE", "n", "pct"]
    shown = write(tmp_path / "x.csv", header, [["a", "40", "80.0"], ["b", "<11", "20.0"]])
    found = checks(shown, small_cells=rule)["small_cells_percentages"]
    assert found["status"] == "fail" and found["observed"] == 1
    hidden = write(tmp_path / "y.csv", header, [["a", "40", "80.0"], ["b", "<11", ""]])
    assert checks(hidden, small_cells=rule)["small_cells_percentages"]["status"] == "pass"


GRID = {
    "count_columns": ["c0", "c1", "c2", "c3"],
    "total_column": "tot",
    "totals": {"column": "g", "value": "Total"},
}


def test_hidden_zeros_dont_hide_what_the_totals_pin(tmp_path):
    """ifelse(n < 11, NA, n) hides zeros too. Taking hidden cells to be 1 to 10
    made this table look impossible, and the widened check passed it; a
    reader who allows for zeros pins hidden 7s and 10s."""
    rows = [
        ["r0", "", "", "", "", "20"],
        ["r1", "", "", "", "", "37"],
        ["r2", "", "", "12", "", "29"],
        ["r3", "", "25", "12", "", "54"],
        ["Total", "37", "45", "44", "14", "140"],
    ]
    path = write(tmp_path / "x.csv", ["g", "c0", "c1", "c2", "c3", "tot"], rows)
    found = checks(path, small_cells=GRID)["small_cells_recoverable"]
    assert found["status"] == "fail" and found["observed"] >= 1


def test_a_hidden_cell_the_totals_show_is_zero_passes(tmp_path):
    rule = {"count_columns": ["n"], "totals": {"column": "DEVICE", "value": "All"}}
    path = write(tmp_path / "x.csv", ["DEVICE", "n"], [["a", "30"], ["b", "<11"], ["All", "30"]])
    found = checks(path, small_cells=rule)["small_cells_recoverable"]
    assert found["status"] == "pass" and "1 hidden count is 0" in found["message"]


def _possible_values(grid: list[list[int]], hidden: set[tuple[int, int]]) -> dict:
    """Every value each hidden cell can take, by brute force over whole
    numbers from 0 up, given the row, column and grand totals: what a reader
    who allows for zeros can work out."""
    rows, cols = len(grid), len(grid[0])
    row_left = [sum(grid[i][j] for j in range(cols) if (i, j) in hidden) for i in range(rows)]
    col_left = [sum(grid[i][j] for i in range(rows) if (i, j) in hidden) for j in range(cols)]
    cells = sorted(hidden)
    seen: dict = {c: set() for c in cells}
    chosen: dict = {}

    def place(k: int) -> None:
        if k == len(cells):
            if not any(row_left) and not any(col_left):
                for c in cells:
                    seen[c].add(chosen[c])
            return
        i, j = cells[k]
        top = min(row_left[i], col_left[j])
        values = range(top + 1)
        if all(ci != i for ci, _ in cells[k + 1 :]):
            values = [row_left[i]] if row_left[i] <= col_left[j] else []
        for value in values:
            chosen[(i, j)] = value
            row_left[i] -= value
            col_left[j] -= value
            place(k + 1)
            row_left[i] += value
            col_left[j] += value

    place(0)
    return seen


def test_a_table_that_passes_gives_no_small_count_away(tmp_path):
    """Random small tables, suppressed as R usually does (n < 11 hidden, zeros
    included) plus a few extra cells: any table QC passes must leave every
    hidden cell more than one possible value, or 0, or 11 and up."""
    rng = random.Random(20260927)
    passed = 0
    for number in range(400):
        rows, cols = rng.choice((2, 3)), rng.choice((2, 3, 4))
        grid = [
            [rng.choice((0, rng.randint(1, 10), rng.randint(11, 25))) for _ in range(cols)]
            for _ in range(rows)
        ]
        hidden = {(i, j) for i in range(rows) for j in range(cols) if grid[i][j] < 11}
        hidden |= {(rng.randrange(rows), rng.randrange(cols)) for _ in range(rng.randint(0, 2))}
        if not hidden:
            continue
        columns = [f"c{j}" for j in range(cols)]
        table = [
            [f"r{i}", *("" if (i, j) in hidden else grid[i][j] for j in range(cols)), sum(grid[i])]
            for i in range(rows)
        ]
        table.append(
            ["Total", *(sum(r[j] for r in grid) for j in range(cols)), sum(map(sum, grid))]
        )
        path = write(tmp_path / f"t{number}.csv", ["g", *columns, "tot"], table)
        rule = {
            "count_columns": columns,
            "total_column": "tot",
            "totals": {"column": "g", "value": "Total"},
        }
        found = checks(path, small_cells=rule)
        if any(c["status"] == "fail" for k, c in found.items() if k != "small_cells"):
            continue
        passed += 1
        for cell, values in _possible_values(grid, hidden).items():
            only = next(iter(values)) if len(values) == 1 else None
            assert only is None or not 1 <= only <= 10, (grid, sorted(hidden), cell, only)
    assert passed > 20  # the search does reach tables that pass
