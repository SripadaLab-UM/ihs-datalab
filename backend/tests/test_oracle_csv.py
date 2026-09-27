"""How a query result is written as CSV: dates, times, RAW and missing values.

The format is what R and the lab's R code read (see `csv_value`), checked
against the prototype in the release parity check (scripts/parity/).
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal

from datalab.config import PRACTICE_ORACLE, QueryLimits
from datalab.data.oracle import OracleDatabase


class FakeCursor:
    def __init__(self, rows: list[tuple], batch: int = 2) -> None:
        self._batches = [rows[i : i + batch] for i in range(0, len(rows), batch)]

    def fetchmany(self) -> list[tuple]:
        return self._batches.pop(0) if self._batches else []


def write(tmp_path, columns, rows, *, preview_rows=10):
    database = OracleDatabase(PRACTICE_ORACLE, "not-a-password", QueryLimits())
    path = tmp_path / "result.csv"
    result = database._write_rows(FakeCursor(rows), columns, path, 1_000, 10**9, preview_rows)
    with path.open(newline="") as handle:
        return result, list(csv.reader(handle))


def test_a_column_of_midnights_is_written_as_plain_dates(tmp_path):
    rows = [
        (dt.datetime(2025, 5, 3), dt.datetime(2025, 5, 3, 7, 30), 1),
        (dt.datetime(2025, 5, 4), dt.datetime(2025, 5, 4), 2),
        (None, dt.datetime(2025, 5, 5, 0, 0, 1), 3),
    ]
    result, lines = write(tmp_path, ["CALENDARDATE", "STARTDATE", "N"], rows)
    assert lines == [
        ["CALENDARDATE", "STARTDATE", "N"],
        ["2025-05-03", "2025-05-03 07:30:00", "1"],
        # A column with any time of day keeps every time, midnight included.
        ["2025-05-04", "2025-05-04 00:00:00", "2"],
        ["", "2025-05-05 00:00:01", "3"],
    ]
    assert result.preview == lines[1:]
    assert result.bytes_written == (tmp_path / "result.csv").stat().st_size
    assert not list(tmp_path.glob("*.dates"))


def test_times_use_a_space_and_keep_fractions(tmp_path):
    rows = [(dt.datetime(2025, 5, 3, 7, 30, 0, 250000),), (dt.datetime(2025, 5, 3, 23, 59, 59),)]
    _, lines = write(tmp_path, ["T"], rows)
    assert lines[1:] == [["2025-05-03 07:30:00.250000"], ["2025-05-03 23:59:59"]]


def test_raw_is_upper_case_hex_like_rawtohex(tmp_path):
    key = bytes.fromhex("00ff10ab" * 4)
    _, lines = write(tmp_path, ["HEALTHKITSAMPLEKEY"], [(key,), (b"",)])
    assert lines[1:] == [["00FF10AB" * 4], [""]]


def test_numbers_text_and_missing_values_are_unchanged(tmp_path):
    rows = [(1, 2.5, "a,b", None, Decimal("3.10")), (0, -0.1, "", None, Decimal("0"))]
    _, lines = write(tmp_path, ["I", "F", "S", "X", "D"], rows)
    assert lines[1:] == [["1", "2.5", "a,b", "", "3.10"], ["0", "-0.1", "", "", "0"]]


def test_a_result_with_no_dates_is_written_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("datalab.data.oracle._dates_only", lambda *a: calls.append(a) or 0)
    write(tmp_path, ["N"], [(1,), (2,)])
    assert calls == []
