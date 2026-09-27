"""Built-in QC: counts only in every message, and the small-cell rule."""

from __future__ import annotations

import csv
import json
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
    # Complementary suppression: two hidden cells adding up to 11 or more is fine.
    safe = write(
        tmp_path / "safe.csv",
        header,
        [
            ["W1", "fitbit", "40"],
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
