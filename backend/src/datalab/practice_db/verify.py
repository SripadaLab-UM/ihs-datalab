"""Check the synthetic IHS database as DATALAB_RO: privileges, row counts, quirks.

    python -m datalab.practice_db verify    (synthetic/db.sh verify)

`verify` returns the checks that failed (none: all passed).
"""

from __future__ import annotations

from collections.abc import Callable

import oracledb

from datalab.practice_db.guard import MARKER_SCHEMA, RO_PWD, require_local_dsn

COHORTS = ("IHS_2024", "IHS_2025", "IHS_2026")


class _Checks:
    def __init__(self, say: Callable[[str], None]) -> None:
        self.say = say
        self.failures: list[str] = []

    def __call__(self, label: str, ok: bool, detail: object = "") -> None:
        self.say(
            f"  [{'ok' if ok else 'FAIL'}] {label}{': ' + str(detail) if detail != '' else ''}"
        )
        if not ok:
            self.failures.append(label)


def one(cur, sql: str, **binds):
    cur.execute(sql, binds)
    return cur.fetchone()[0]


def can_write(cur) -> bool:
    try:
        cur.execute(
            "UPDATE IHS_2026.VW_DAILY_MOOD SET MOOD_COMMENT = MOOD_COMMENT WHERE ROWNUM = 1"
        )
        return True
    except oracledb.DatabaseError:
        return False
    finally:
        cur.connection.rollback()


# Exists but holds no rows, like a feed not loaded yet (generate.py EMPTY_TABLES).
MEANT_EMPTY = {"IHS_2026.GARMINHRVSUMMARY", "IHS_2026.VGARMINHRVSUMMARY"}

# Quirk probes: (description, SQL returning a count that should be > 0).
QUIRKS = [
    (
        "Garmin daily: participant-days with >1 row (later INSERTEDDATE wins)",
        "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.GARMINDAILYSUMMARY GROUP BY "
        "PARTICIPANTIDENTIFIER, CALENDARDATE HAVING COUNT(*) > 1)",
    ),
    (
        "HealthKit daily steps: participant-days with >1 row",
        "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.HEALTHKITSTATISTICS_DAILYSTEPS GROUP BY "
        "PARTICIPANTIDENTIFIER, TRUNC(STARTDATE) HAVING COUNT(*) > 1)",
    ),
    (
        "HealthKit daily steps: buckets not starting at local midnight",
        "SELECT COUNT(*) FROM IHS_2025.HEALTHKITSTATISTICS_DAILYSTEPS WHERE TO_CHAR(STARTDATE, "
        "'HH24:MI') <> '00:00'",
    ),
    (
        "Garmin sleep: non-FINAL VALIDATION rows",
        "SELECT COUNT(*) FROM IHS_2025.GARMINSLEEPSUMMARY WHERE VALIDATION NOT LIKE '%FINAL%'",
    ),
    (
        "Garmin naps (no PARTICIPANTIDENTIFIER column)",
        "SELECT COUNT(*) FROM IHS_2025.GARMINSLEEPSUMMARY_NAPS",
    ),
    (
        "Garmin HRV: CALENDARDATE with a time component",
        "SELECT COUNT(*) FROM IHS_2025.GARMINHRVSUMMARY WHERE CALENDARDATE <> TRUNC(CALENDARDATE)",
    ),
    (
        "Fitbit sleep: logs spanning midnight",
        "SELECT COUNT(*) FROM IHS_2025.FITBITSLEEPLOGS WHERE TRUNC(STARTDATE) <> TRUNC(ENDDATE)",
    ),
    (
        "HealthKit RHR: rows whose SOURCEPRODUCTTYPE lacks 'Watch' (or is NULL)",
        "SELECT COUNT(*) FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE WHERE SOURCEPRODUCTTYPE "
        "NOT LIKE '%Watch%' OR SOURCEPRODUCTTYPE IS NULL",
    ),
    (
        "HealthKit: distinct UTC offsets in STARTDATE",
        "SELECT COUNT(DISTINCT TO_CHAR(STARTDATE, 'TZH:TZM')) FROM "
        "IHS_2025.HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL",
    ),
    (
        "Participants never enrolled (NULL STUDY_PARTICIPANT_ID)",
        "SELECT COUNT(*) FROM IHS_2025.VW_IHS_PARTICIPANT_SUMMARY WHERE STUDY_PARTICIPANT_ID IS "
        "NULL",
    ),
    # Correctness cases (generate.py write_cases).
    (
        "HealthKit RHR: participants in neither STUDYPARTICIPANTS nor the summary (out of cohort)",
        "SELECT COUNT(DISTINCT h.PARTICIPANTIDENTIFIER) FROM "
        "IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE h WHERE NOT EXISTS (SELECT 1 FROM "
        "IHS_2025.STUDYPARTICIPANTS p WHERE p.PARTICIPANTIDENTIFIER = h.PARTICIPANTIDENTIFIER) "
        "AND NOT EXISTS (SELECT 1 FROM IHS_2025.VW_IHS_PARTICIPANT_SUMMARY s "
        "WHERE s.PARTICIPANTIDENTIFIER = h.PARTICIPANTIDENTIFIER)",
    ),
    (
        "Participant summary: a participant with more than one row",
        "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.VW_IHS_PARTICIPANT_SUMMARY "
        "GROUP BY PARTICIPANTIDENTIFIER HAVING COUNT(*) > 1)",
    ),
    (
        "HealthKit RHR text RECORD_DATE: person-days with more than one timestamp",
        "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE "
        "GROUP BY PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10) "
        "HAVING COUNT(DISTINCT RECORD_DATE) > 1)",
    ),
    (
        "HealthKit RHR text RECORD_DATE: local date differs from the UTC date",
        "SELECT COUNT(*) FROM IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE WHERE "
        "TRUNC(CAST(RECORD_DATE AS DATE)) <> TRUNC(CAST(SYS_EXTRACT_UTC(RECORD_DATE) AS DATE))",
    ),
    (
        "Smoking routine join (STG_SURVEYDICTIONARY, text SURVEYVERSION)",
        "SELECT COUNT(*) FROM IHS_2025.SURVEYQUESTIONRESULTS qr JOIN IHS_2025.SURVEYRESULTS sr ON "
        "sr.SURVEYRESULTKEY = qr.SURVEYRESULTKEY "
        "JOIN IHS_2025.STG_SURVEYDICTIONARY d ON d.SURVEYNAME = sr.SURVEYNAME AND d.SURVEYVERSION "
        "= TO_CHAR(sr.SURVEYVERSION) "
        "AND d.RESULTIDENTIFIER = qr.RESULTIDENTIFIER WHERE LOWER(DBMS_LOB.SUBSTR(d.QUESTIONTEXT, "
        "1000, 1)) = "
        "LOWER('Tobacco products (cigarettes, chewing tobacco, cigars, etc.)')",
    ),
]


def verify(dsn: str, *, ro_pwd: str = RO_PWD, say: Callable[[str], None] = print) -> list[str]:
    require_local_dsn(dsn)
    check = _Checks(say)
    with oracledb.connect(user="DATALAB_RO", password=ro_pwd, dsn=dsn) as conn:
        cur = conn.cursor()
        say("Synthetic marker (DataLab's practice profile requires it):")
        check(
            "marker table is readable",
            one(cur, f"SELECT COUNT(*) FROM {MARKER_SCHEMA}.MARKER") == 1,
        )
        say("Default roles (what a plain connection gets):")
        cur.execute("SELECT role FROM session_roles ORDER BY role")
        roles = [r[0] for r in cur]
        cur.execute("SELECT privilege FROM session_privs ORDER BY privilege")
        privs = [r[0] for r in cur]
        check("write role IHS_2026_ROLE is a default role", "IHS_2026_ROLE" in roles, roles)
        check(
            "CREATE TABLE/VIEW/PROCEDURE held via the write role",
            {"CREATE TABLE", "CREATE VIEW", "CREATE PROCEDURE"} <= set(privs),
            privs,
        )
        check("UPDATE on IHS_2026 succeeds (then rolled back)", can_write(cur))

        say("After SET ROLE IHS_2025_RO, IHS_2026_RO:")
        cur.execute("SET ROLE IHS_2025_RO, IHS_2026_RO")
        cur.execute("SELECT privilege FROM session_privs")
        privs = [r[0] for r in cur]
        check("session_privs is CREATE SESSION only", privs == ["CREATE SESSION"], privs)
        check("UPDATE on IHS_2026 is refused", not can_write(cur))

        say("Row counts (every object readable after SET ROLE):")
        cur.execute(
            "SELECT owner, object_name FROM all_objects WHERE owner IN ('IHS_2024', 'IHS_2025', "
            "'IHS_2026') "
            "AND object_type IN ('TABLE', 'VIEW') ORDER BY object_name, owner"
        )
        counts: dict[str, dict[str, int]] = {}
        for owner, name in cur.fetchall():
            counts.setdefault(name, {})[owner] = one(cur, f'SELECT COUNT(*) FROM {owner}."{name}"')
        say(f"  {'object':42}" + "".join(f"{c:>10}" for c in COHORTS))
        for name, by in counts.items():
            say(
                f"  {name:42}"
                + "".join(f"{by[c]:>10,}" if c in by else f"{'-':>10}" for c in COHORTS)
            )
        empty = {f"{c}.{n}" for n, by in counts.items() for c, v in by.items() if v == 0}
        unexpected = sorted(empty - MEANT_EMPTY)
        check(
            "every object has rows (but the deliberately empty one)",
            not unexpected,
            unexpected or "",
        )
        check(
            "the deliberately empty table is empty",
            empty >= MEANT_EMPTY,
            sorted(MEANT_EMPTY - empty) or "",
        )
        check(
            "drift: VW_DAILY_MOOD absent in IHS_2024",
            "IHS_2024" not in counts.get("VW_DAILY_MOOD", {}),
        )
        check(
            "drift: STG_SURVEYDICTIONARY replaces SURVEYDICTIONARY after 2024",
            set(counts["SURVEYDICTIONARY"]) == {"IHS_2024"}
            and "IHS_2024" not in counts["STG_SURVEYDICTIONARY"],
        )

        say("Quirks (IHS_2025):")
        for label, sql in QUIRKS:
            n = one(cur, sql)
            check(label, n > 0, f"{n:,}")
        sample = one(cur, "SELECT STARTDATE FROM IHS_2025.VFITBITSLEEPLOGS WHERE ROWNUM = 1")
        check("V* views render timestamps as text", isinstance(sample, str), sample)
    say(f"{len(check.failures)} check(s) failed" if check.failures else "all checks passed")
    return check.failures
