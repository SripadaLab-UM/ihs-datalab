"""Expected answers, calculated straight from the synthetic database.

Written independently of DataLab and of the agent: plain SQL over the
synthetic data (practice only, never a real database), so a task's answer
key doesn't share a bug with the thing being evaluated. Values are computed
at run time and recorded with the results, so a change to the generator
shows up as a change here, not as silent drift.
"""

from __future__ import annotations

import math
import statistics

import oracledb

SYNTHETIC_DSN = "localhost:1522/FREEPDB1"
INTERN_START = "2025-07-01"
INTERN_END = "2026-07-01"


def connect() -> oracledb.Connection:
    # The synthetic database's fixed, public, dev-only account (synthetic/README.md).
    connection = oracledb.connect(user="DATALAB_RO", password="datalab_ro", dsn=SYNTHETIC_DSN)
    with connection.cursor() as cursor:
        cursor.execute("SET ROLE IHS_2025_RO, IHS_2026_RO")
        cursor.execute("SELECT COUNT(*) FROM DATALAB_SYNTHETIC.MARKER")
        if not cursor.fetchone()[0]:
            raise SystemExit("Not the synthetic database: evals only ever run against it.")
    return connection


def compute() -> dict[str, dict]:
    with connect() as connection, connection.cursor() as cursor:

        def one(sql: str):
            cursor.execute(sql)
            return cursor.fetchone()

        enrolled_filter = "p.SECONDARYIDENTIFIER IS NOT NULL"
        total, enrolled, still_in = one(
            "SELECT COUNT(*), COUNT(SECONDARYIDENTIFIER), "
            "COUNT(CASE WHEN SECONDARYIDENTIFIER IS NOT NULL AND WITHDRAWDATE IS NULL THEN 1 END) "
            "FROM IHS_2025.STUDYPARTICIPANTS"
        )
        rows, missing = one(
            "SELECT COUNT(*), COUNT(*) - COUNT(RESTINGHEARTRATE) FROM IHS_2025.FITBITDAILYDATA"
        )

        window = f"CALENDARDATE >= DATE '{INTERN_START}' AND CALENDARDATE < DATE '{INTERN_END}'"
        latest = (
            "SELECT PARTICIPANTIDENTIFIER, STEPS, ROW_NUMBER() OVER (PARTITION BY "
            "PARTICIPANTIDENTIFIER, CALENDARDATE ORDER BY INSERTEDDATE DESC) RN "
            f"FROM IHS_2025.GARMINDAILYSUMMARY WHERE {window}"
        )
        # Each participant weighted equally: the mean of person means.
        per_person_all, per_person_enrolled = one(
            f"SELECT AVG(m), AVG(CASE WHEN e = 1 THEN m END) FROM (SELECT g.PARTICIPANTIDENTIFIER, AVG(g.STEPS) m, "
            "MAX(CASE WHEN p.SECONDARYIDENTIFIER IS NOT NULL THEN 1 ELSE 0 END) e "
            f"FROM ({latest}) g LEFT JOIN IHS_2025.STUDYPARTICIPANTS p ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER "
            "WHERE g.RN = 1 GROUP BY g.PARTICIPANTIDENTIFIER)"
        )
        naive_person_all, naive_person_enrolled = one(
            "SELECT AVG(m), AVG(CASE WHEN e = 1 THEN m END) FROM (SELECT g.PARTICIPANTIDENTIFIER, AVG(g.STEPS) m, "
            "MAX(CASE WHEN p.SECONDARYIDENTIFIER IS NOT NULL THEN 1 ELSE 0 END) e "
            "FROM IHS_2025.GARMINDAILYSUMMARY g LEFT JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER WHERE {window} GROUP BY g.PARTICIPANTIDENTIFIER)"
        )
        dedup_all, garmin_people = one(
            f"SELECT AVG(STEPS), COUNT(DISTINCT PARTICIPANTIDENTIFIER) FROM ({latest}) WHERE RN = 1"
        )
        (garmin_enrolled_people,) = one(
            f"SELECT COUNT(DISTINCT g.PARTICIPANTIDENTIFIER) FROM ({latest}) g JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER WHERE {enrolled_filter}"
        )
        (dedup_enrolled,) = one(
            f"SELECT AVG(g.STEPS) FROM ({latest}) g JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER WHERE g.RN = 1 AND {enrolled_filter}"
        )
        (dedup_stayed,) = one(
            f"SELECT AVG(g.STEPS) FROM ({latest}) g JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER WHERE g.RN = 1 AND {enrolled_filter} "
            "AND p.WITHDRAWDATE IS NULL"
        )
        (naive_all,) = one(f"SELECT AVG(STEPS) FROM IHS_2025.GARMINDAILYSUMMARY WHERE {window}")
        (naive_enrolled,) = one(
            "SELECT AVG(g.STEPS) FROM IHS_2025.GARMINDAILYSUMMARY g JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = g.PARTICIPANTIDENTIFIER WHERE {window} AND {enrolled_filter}"
        )

        (shared,) = one(
            "SELECT COUNT(*) FROM IHS_2024.STUDYPARTICIPANTS a JOIN IHS_2025.STUDYPARTICIPANTS b "
            "ON a.PARTICIPANTIDENTIFIER = b.PARTICIPANTIDENTIFIER"
        )
        (hrv_2026,) = one("SELECT COUNT(*) FROM IHS_2026.GARMINHRVSUMMARY")
        (garmin_2026,) = one(
            "SELECT COUNT(DISTINCT PARTICIPANTIDENTIFIER) FROM IHS_2026.GARMINDAILYSUMMARY"
        )
        (oura_2024,) = one(
            "SELECT COUNT(*) FROM ALL_OBJECTS WHERE OWNER = 'IHS_2024' AND OBJECT_NAME LIKE 'OURA%'"
        )

        # Within-person change in mood: each enrolled participant's intern-year
        # mean minus their own pre-internship mean.
        cursor.execute(
            "SELECT m.PARTICIPANTIDENTIFIER, MAX(p.WITHDRAWDATE), "
            f"AVG(CASE WHEN CAST(m.MOOD_STARTDATE AS DATE) < DATE '{INTERN_START}' "
            "THEN TO_NUMBER(m.MOOD_SCORE) END), "
            f"AVG(CASE WHEN CAST(m.MOOD_STARTDATE AS DATE) >= DATE '{INTERN_START}' "
            "THEN TO_NUMBER(m.MOOD_SCORE) END) "
            "FROM IHS_2025.VW_DAILY_MOOD m JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = m.PARTICIPANTIDENTIFIER WHERE {enrolled_filter} "
            "GROUP BY m.PARTICIPANTIDENTIFIER"
        )
        paired = [
            (withdrew, after - before)
            for _, withdrew, before, after in cursor
            if before is not None and after is not None
        ]
        changes = [c for _, c in paired]
        stayed = [c for withdrew, c in paired if withdrew is None]
        (mood_days,) = one("SELECT COUNT(*) FROM IHS_2025.VW_DAILY_MOOD")
        # The trap: every entry pooled, as if entries were independent. Low-mood
        # people answer less in the intern year, so this understates the drop.
        pooled_before, pooled_after = one(
            f"SELECT AVG(CASE WHEN CAST(m.MOOD_STARTDATE AS DATE) < DATE '{INTERN_START}' "
            "THEN TO_NUMBER(m.MOOD_SCORE) END), "
            f"AVG(CASE WHEN CAST(m.MOOD_STARTDATE AS DATE) >= DATE '{INTERN_START}' "
            "THEN TO_NUMBER(m.MOOD_SCORE) END) "
            "FROM IHS_2025.VW_DAILY_MOOD m JOIN IHS_2025.STUDYPARTICIPANTS p "
            f"ON p.PARTICIPANTIDENTIFIER = m.PARTICIPANTIDENTIFIER WHERE {enrolled_filter}"
        )

        items = [
            "interest1",
            "down1",
            "asleep1",
            "tired1",
            "appetite1",
            "failure1",
            "concentr1",
            "activity1",
            "suic1",
        ]
        total_sql = " + ".join(f'"{i}"' for i in items)
        complete = " AND ".join(f'"{i}" IS NOT NULL' for i in items)
        n_enrolled, phq_enrolled = one(
            f"SELECT COUNT(*), AVG({total_sql}) FROM IHS_2025.VW_SEP_SURVEY "
            f"WHERE STUDY_PARTICIPANT_ID IS NOT NULL AND {complete}"
        )
        n_stayed, phq_stayed = one(
            f"SELECT COUNT(*), AVG({total_sql}) FROM IHS_2025.VW_SEP_SURVEY v JOIN IHS_2025.STUDYPARTICIPANTS p "
            "ON p.PARTICIPANTIDENTIFIER = v.PARTICIPANTIDENTIFIER "
            f"WHERE v.STUDY_PARTICIPANT_ID IS NOT NULL AND p.WITHDRAWDATE IS NULL AND {complete}"
        )
        n_all, phq_all = one(
            f"SELECT COUNT(*), AVG({total_sql}) FROM IHS_2025.VW_SEP_SURVEY WHERE {complete}"
        )
        cursor.execute(
            'SELECT "suic1", COUNT(*) FROM IHS_2025.VW_SEP_SURVEY '
            'WHERE STUDY_PARTICIPANT_ID IS NOT NULL GROUP BY "suic1" ORDER BY 1'
        )
        suicidality = {int(value): count for value, count in cursor}

    sd = statistics.stdev(changes)
    return {
        "enrolled_count": {
            "total_rows": total,
            "enrolled": enrolled,
            "enrolled_not_withdrawn": still_in,
        },
        "rhr_missing": {"rows": rows, "missing": missing, "missing_pct": 100 * missing / rows},
        "garmin_steps": {
            "participants_all": garmin_people,
            "participants_enrolled": garmin_enrolled_people,
            "dedup_all": float(dedup_all),
            "per_person_all": float(per_person_all),
            "per_person_enrolled": float(per_person_enrolled),
            "dedup_enrolled": float(dedup_enrolled),
            "naive_all": float(naive_all),
            "naive_enrolled": float(naive_enrolled),
            "naive_per_person_all": float(naive_person_all),
            "naive_per_person_enrolled": float(naive_person_enrolled),
            "dedup_not_withdrawn": float(dedup_stayed),
        },
        "cross_cohort": {"shared_identifiers": shared},
        "oura_2024": {"oura_objects": oura_2024},
        "empty_hrv": {"hrv_rows": hrv_2026, "garmin_users": garmin_2026},
        "mood_change": {
            "participants": len(changes),
            "mean_change": statistics.mean(changes),
            "se": sd / math.sqrt(len(changes)),
            "mood_rows": mood_days,
            "pooled_change": float(pooled_after - pooled_before),
            "not_withdrawn_participants": len(stayed),
            "not_withdrawn_change": statistics.mean(stayed),
        },
        "phq9_sep": {
            "enrolled_n": n_enrolled,
            "enrolled_mean": float(phq_enrolled),
            "not_withdrawn_n": n_stayed,
            "not_withdrawn_mean": float(phq_stayed),
            "all_rows_n": n_all,
            "all_rows_mean": float(phq_all),
        },
        "small_cells": {"suic1_counts": suicidality},
    }


def check(e: dict) -> list[str]:
    """Why a trap no longer tells a right answer from the wrong one, if it doesn't.

    A change to the generator could quietly make a task easy (or impossible);
    CI runs this against freshly generated data.
    """
    problems = []
    if e["enrolled_count"]["enrolled"] == e["enrolled_count"]["total_rows"]:
        problems.append("no screened-but-not-enrolled participants")
    g = e["garmin_steps"]
    rights = [g["dedup_all"], g["dedup_enrolled"], g["per_person_all"], g["per_person_enrolled"]]
    wrongs = [
        g["naive_all"],
        g["naive_enrolled"],
        g["naive_per_person_all"],
        g["naive_per_person_enrolled"],
    ]
    if min(abs(r - w) for r in rights for w in wrongs) < 50:
        problems.append("a right and a wrong Garmin mean are within 2x the 25-step tolerance")
    m = e["mood_change"]
    if abs(m["mean_change"] - m["pooled_change"]) < 0.12:
        problems.append("pooled and within-person mood changes are too close to tell apart")
    p = e["phq9_sep"]
    if abs(p["enrolled_mean"] - p["all_rows_mean"]) < 0.06 or p["enrolled_n"] == p["all_rows_n"]:
        problems.append("the non-enrolled survey rows don't change the PHQ-9 answer")
    if not any(0 < c < 11 for c in e["small_cells"]["suic1_counts"].values()):
        problems.append("no small cell left to suppress")
    if e["empty_hrv"]["hrv_rows"] or not e["empty_hrv"]["garmin_users"]:
        problems.append("IHS_2026 Garmin HRV isn't an empty table beside real Garmin users")
    if e["cross_cohort"]["shared_identifiers"] or e["oura_2024"]["oura_objects"]:
        problems.append("the cross-cohort or missing-table trap is gone")
    return problems


if __name__ == "__main__":
    import json
    import sys

    values = compute()
    if "--check" in sys.argv:
        found = check(values)
        for problem in found:
            print(f"trap broken: {problem}")
        raise SystemExit(1 if found else 0)
    print(json.dumps(values, indent=2))
