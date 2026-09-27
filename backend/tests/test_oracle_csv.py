"""How a query result is written as CSV: dates, times, RAW and missing values.

The format is what R and the lab's R code read (see `csv_value`), checked
against the prototype in the release parity check (scripts/parity/).
"""

from __future__ import annotations

import csv
import datetime as dt
import threading
from decimal import Decimal

import oracledb
import pytest

from datalab.config import PRACTICE_ORACLE, QueryLimits
from datalab.data.oracle import OracleDatabase, QueryCancelled, QueryTimedOut


class FakeCursor:
    """Rows in batches, described as Oracle would: DATE for datetimes unless `types` says."""

    def __init__(self, rows, columns, batch: int = 2, types=None) -> None:
        self._batches = [rows[i : i + batch] for i in range(0, len(rows), batch)]
        types = types or {}

        def kind(index, name):
            if name in types:
                return types[name]
            dated = any(isinstance(r[index], dt.datetime) for r in rows)
            return oracledb.DB_TYPE_DATE if dated else oracledb.DB_TYPE_VARCHAR

        self.description = [(name, kind(i, name)) for i, name in enumerate(columns)]

    def fetchmany(self) -> list[tuple]:
        return self._batches.pop(0) if self._batches else []


def write(tmp_path, columns, rows, *, preview_rows=10, types=None):
    database = OracleDatabase(PRACTICE_ORACLE, "not-a-password", QueryLimits())
    path = tmp_path / "result.csv"
    cursor = FakeCursor(rows, columns, types=types)
    result = database._write_rows(cursor, columns, path, 1_000, 10**9, preview_rows)
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


def test_a_timestamp_keeps_its_time_even_at_midnight(tmp_path):
    # A TIMESTAMP is a moment, not a day, and a zone-aware value never loses its time.
    rows = [
        (dt.datetime(2025, 5, 3), dt.datetime(2025, 5, 3, tzinfo=dt.UTC)),
        (dt.datetime(2025, 5, 4), dt.datetime(2025, 5, 4, tzinfo=dt.UTC)),
    ]
    types = {"TS": oracledb.DB_TYPE_TIMESTAMP, "TZ": oracledb.DB_TYPE_DATE}
    _, lines = write(tmp_path, ["TS", "TZ"], rows, types=types)
    assert lines[1:] == [
        ["2025-05-03 00:00:00", "2025-05-03 00:00:00+00:00"],
        ["2025-05-04 00:00:00", "2025-05-04 00:00:00+00:00"],
    ]


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


class FakeConnection:
    """Enough of a python-oracledb connection for `extract_to_csv`."""

    def __init__(self, rows: list[tuple], columns: list[str], on_fetched=None) -> None:
        self._cursor = FakeCursor(rows, columns, batch=len(rows) or 1)
        self._on_fetched = on_fetched

    def cursor(self):
        connection = self

        class Cursor:
            arraysize = 0
            prefetchrows = 0
            description = connection._cursor.description

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def execute(self, *_args):
                pass

            def fetchmany(self):
                batch = connection._cursor.fetchmany()
                if not batch and connection._on_fetched:
                    connection._on_fetched()
                return batch

        return Cursor()

    def cancel(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_stop_during_the_date_rewrite_ends_the_query_and_keeps_nothing(tmp_path, monkeypatch):
    database = OracleDatabase(PRACTICE_ORACLE, "not-a-password", QueryLimits())
    cancel = threading.Event()
    rows = [(dt.datetime(2025, 5, 1) + dt.timedelta(days=i % 300), i) for i in range(12_000)]
    # Stop is pressed once every row has come back, while the dates are rewritten.
    connection = FakeConnection(rows, ["D", "N"], on_fetched=cancel.set)
    monkeypatch.setattr(database, "connect", lambda **_: connection)
    checks = []
    real = database._stopped
    monkeypatch.setattr(database, "_stopped", lambda *a: checks.append(1) or real(*a))
    out = tmp_path / "result.csv"
    with pytest.raises(QueryCancelled):
        database.extract_to_csv(
            "SELECT 1 FROM DUAL", {}, out, max_rows=10**6, max_bytes=10**9,
            preview_rows=5, cancel=cancel,
        )  # fmt: skip
    assert checks == [1]  # the first check, 5,000 rows in, stopped it
    assert list(tmp_path.iterdir()) == []


def test_the_deadline_also_ends_the_date_rewrite(tmp_path):
    database = OracleDatabase(PRACTICE_ORACLE, "not-a-password", QueryLimits())
    timed_out = threading.Event()
    timed_out.set()
    path = tmp_path / "result.csv"
    with pytest.raises(QueryTimedOut):
        database._write_rows(
            FakeCursor([(dt.datetime(2025, 5, 3),)], ["D"]), ["D"], path, 10, 10**9, 5,
            stopped=lambda: database._stopped(threading.Event(), timed_out),
        )  # fmt: skip
    assert sorted(p.name for p in tmp_path.iterdir()) == ["result.csv"]  # no .dates left
