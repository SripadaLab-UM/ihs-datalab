"""The synthetic database's correctness cases (generate.py write_cases), without Oracle.

Three mistakes a finished analysis can still make need data that shows them:
text timestamps with several a day (person-days), device data from people
outside the cohort, and a participant twice in the cohort-defining summary.
The cases are extra rows only: every other row is exactly what it was.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from datalab.practice_db import generate
from datalab.practice_db.generate import (
    EDGE_DAY,
    ORPHANS,
    generate_cohort,
    load_spec,
    tstz,
)

COHORT = "IHS_2025"
RHR = "HEALTHKITSAMPLES_RESTINGHEARTRATE"


def _generate(cohort: str = COHORT) -> dict[str, list[dict]]:
    objects, config = load_spec()
    tables = {n for n, o in objects.items() if cohort in o["cohorts"] and o["create_as"] == "table"}
    return generate_cohort(config["seed"], cohort, config["cohorts"][cohort], config, tables)


@pytest.fixture(scope="module")
def rows() -> dict[str, list[dict]]:
    return _generate()


@pytest.fixture(scope="module")
def without_cases() -> dict[str, list[dict]]:
    saved = generate.CASES
    generate.CASES = set()
    try:
        return _generate()
    finally:
        generate.CASES = saved


def test_every_other_row_is_unchanged(rows, without_cases):
    assert set(rows) == set(without_cases)
    for table, before in without_cases.items():
        assert rows[table][: len(before)] == before, table
    grown = {t for t in rows if len(rows[t]) > len(without_cases[t])}
    assert "VW_IHS_PARTICIPANT_SUMMARY" in grown and RHR in grown
    assert not grown & {"STUDYPARTICIPANTS", "FITBITDAILYDATA", "GARMINDAILYSUMMARY"}


def test_the_cases_are_deterministic(rows):
    again = _generate()
    assert again["VW_IHS_PARTICIPANT_SUMMARY"] == rows["VW_IHS_PARTICIPANT_SUMMARY"]
    assert again[RHR][-10:] == rows[RHR][-10:]


def test_other_cohorts_have_no_cases():
    rows = _generate("IHS_2026")
    ids = [s["PARTICIPANTIDENTIFIER"] for s in rows["VW_IHS_PARTICIPANT_SUMMARY"]]
    assert len(ids) == len(set(ids))


def test_people_outside_the_cohort_have_device_data_only(rows):
    roster = {p["PARTICIPANTIDENTIFIER"] for p in rows["STUDYPARTICIPANTS"]}
    summary = {s["PARTICIPANTIDENTIFIER"] for s in rows["VW_IHS_PARTICIPANT_SUMMARY"]}
    with_data = {r["PARTICIPANTIDENTIFIER"] for r in rows[RHR]}
    outside = with_data - roster - summary
    assert outside == set(ORPHANS)
    steps = {r["PARTICIPANTIDENTIFIER"] for r in rows["HEALTHKITSTATISTICS_DAILYSTEPS"]}
    assert set(ORPHANS) <= steps
    assert all(pid.startswith("SYN25-") for pid in ORPHANS)


def test_one_participant_is_twice_in_the_summary(rows):
    summary = rows["VW_IHS_PARTICIPANT_SUMMARY"]
    ids = [s["PARTICIPANTIDENTIFIER"] for s in summary]
    twice = {pid for pid in ids if ids.count(pid) > 1}
    assert len(twice) == 1 and len(summary) == len(set(ids)) + 1
    (pid,) = twice
    first, second = (s for s in summary if s["PARTICIPANTIDENTIFIER"] == pid)
    # Enrolled, so a cohort of enrolled people has this duplicate in it.
    assert first["STUDY_PARTICIPANT_ID"] is not None
    assert {k for k in first if first[k] != second[k]} == {"PHONE"}


def test_two_timestamps_on_one_date_are_one_person_day(rows):
    # tstz renders a timestamp as the V* views do: 'YYYY-MM-DD HH24:MI:SS TZH:TZM'.
    pid = rows["VW_IHS_PARTICIPANT_SUMMARY"][-1]["PARTICIPANTIDENTIFIER"]  # the duplicated one
    edge = [r for r in rows[RHR] if r["PARTICIPANTIDENTIFIER"] == pid][-4:]
    ends = [tstz(r["RECORD_DATE"]) for r in edge]
    assert ends == [
        "2025-11-15 08:00:00 -05:00",
        "2025-11-15 21:15:00 -05:00",
        "2025-11-15 23:30:00 -05:00",
        "2025-11-16 00:20:00 -05:00",
    ]
    local_days = {e[:10] for e in ends}  # the date prefix: the local date
    assert len(set(ends)) == 4 and local_days == {"2025-11-15", "2025-11-16"}
    # In UTC, 23:30 -05:00 is already the next day ...
    utc_days = {r["RECORD_DATE"].astimezone(UTC).date().isoformat() for r in edge}
    assert utc_days == {"2025-11-15", "2025-11-16"}
    assert edge[2]["RECORD_DATE"].astimezone(UTC).date() > EDGE_DAY
    # ... and the last sample started on EDGE_DAY but ends after midnight.
    assert edge[3]["STARTDATE"].date() == EDGE_DAY < edge[3]["RECORD_DATE"].date()


def test_raw_timestamps_overcount_person_days(rows):
    samples = rows[RHR]
    raw = {(r["PARTICIPANTIDENTIFIER"], tstz(r["RECORD_DATE"])) for r in samples}
    local = {(r["PARTICIPANTIDENTIFIER"], tstz(r["RECORD_DATE"])[:10]) for r in samples}
    by_start = {(r["PARTICIPANTIDENTIFIER"], tstz(r["STARTDATE"])[:10]) for r in samples}
    assert len(raw) > 1.1 * max(len(local), len(by_start))
