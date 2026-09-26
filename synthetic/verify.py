"""Check the synthetic IHS database as DATALAB_RO: privileges, row counts, quirks.

    uv run --project synthetic python synthetic/verify.py

Exits non-zero if a check fails.
"""

import os
import sys

import oracledb

from guard import MARKER_SCHEMA, require_local_dsn

DSN = os.environ.get("SYNTH_ORACLE_DSN", "localhost:1522/FREEPDB1")
RO_PWD = os.environ.get("SYNTH_RO_PWD", "datalab_ro")
COHORTS = ("IHS_2024", "IHS_2025", "IHS_2026")
failures = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{': ' + str(detail) if detail != '' else ''}")
    if not ok:
        failures.append(label)


def one(cur, sql: str, **binds):
    cur.execute(sql, binds)
    return cur.fetchone()[0]


def can_write(cur) -> bool:
    try:
        cur.execute("UPDATE IHS_2026.VW_DAILY_MOOD SET MOOD_COMMENT = MOOD_COMMENT WHERE ROWNUM = 1")
        return True
    except oracledb.DatabaseError:
        return False
    finally:
        cur.connection.rollback()


# Exists but holds no rows, like a feed not loaded yet (generate.py EMPTY_TABLES).
MEANT_EMPTY = {"IHS_2026.GARMINHRVSUMMARY", "IHS_2026.VGARMINHRVSUMMARY"}

# Quirk probes: (description, SQL returning a count that should be > 0).
QUIRKS = [
    ("Garmin daily: participant-days with >1 row (later INSERTEDDATE wins)",
     "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.GARMINDAILYSUMMARY GROUP BY PARTICIPANTIDENTIFIER, CALENDARDATE HAVING COUNT(*) > 1)"),
    ("HealthKit daily steps: participant-days with >1 row",
     "SELECT COUNT(*) FROM (SELECT 1 FROM IHS_2025.HEALTHKITSTATISTICS_DAILYSTEPS GROUP BY PARTICIPANTIDENTIFIER, TRUNC(STARTDATE) HAVING COUNT(*) > 1)"),
    ("HealthKit daily steps: buckets not starting at local midnight",
     "SELECT COUNT(*) FROM IHS_2025.HEALTHKITSTATISTICS_DAILYSTEPS WHERE TO_CHAR(STARTDATE, 'HH24:MI') <> '00:00'"),
    ("Garmin sleep: non-FINAL VALIDATION rows",
     "SELECT COUNT(*) FROM IHS_2025.GARMINSLEEPSUMMARY WHERE VALIDATION NOT LIKE '%FINAL%'"),
    ("Garmin naps (no PARTICIPANTIDENTIFIER column)", "SELECT COUNT(*) FROM IHS_2025.GARMINSLEEPSUMMARY_NAPS"),
    ("Garmin HRV: CALENDARDATE with a time component",
     "SELECT COUNT(*) FROM IHS_2025.GARMINHRVSUMMARY WHERE CALENDARDATE <> TRUNC(CALENDARDATE)"),
    ("Fitbit sleep: logs spanning midnight",
     "SELECT COUNT(*) FROM IHS_2025.FITBITSLEEPLOGS WHERE TRUNC(STARTDATE) <> TRUNC(ENDDATE)"),
    ("HealthKit RHR: rows whose SOURCEPRODUCTTYPE lacks 'Watch' (or is NULL)",
     "SELECT COUNT(*) FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE WHERE SOURCEPRODUCTTYPE NOT LIKE '%Watch%' OR SOURCEPRODUCTTYPE IS NULL"),
    ("HealthKit: distinct UTC offsets in STARTDATE",
     "SELECT COUNT(DISTINCT TO_CHAR(STARTDATE, 'TZH:TZM')) FROM IHS_2025.HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL"),
    ("Participants never enrolled (NULL STUDY_PARTICIPANT_ID)",
     "SELECT COUNT(*) FROM IHS_2025.VW_IHS_PARTICIPANT_SUMMARY WHERE STUDY_PARTICIPANT_ID IS NULL"),
    ("Smoking routine join (STG_SURVEYDICTIONARY, text SURVEYVERSION)",
     "SELECT COUNT(*) FROM IHS_2025.SURVEYQUESTIONRESULTS qr JOIN IHS_2025.SURVEYRESULTS sr ON sr.SURVEYRESULTKEY = qr.SURVEYRESULTKEY "
     "JOIN IHS_2025.STG_SURVEYDICTIONARY d ON d.SURVEYNAME = sr.SURVEYNAME AND d.SURVEYVERSION = TO_CHAR(sr.SURVEYVERSION) "
     "AND d.RESULTIDENTIFIER = qr.RESULTIDENTIFIER WHERE LOWER(DBMS_LOB.SUBSTR(d.QUESTIONTEXT, 1000, 1)) = "
     "LOWER('Tobacco products (cigarettes, chewing tobacco, cigars, etc.)')"),
]


def main() -> None:
    require_local_dsn(DSN)
    with oracledb.connect(user="DATALAB_RO", password=RO_PWD, dsn=DSN) as conn:
        cur = conn.cursor()
        print("Synthetic marker (DataLab's practice profile requires it):")
        check("marker table is readable", one(cur, f"SELECT COUNT(*) FROM {MARKER_SCHEMA}.MARKER") == 1)
        print("Default roles (what a plain connection gets):")
        cur.execute("SELECT role FROM session_roles ORDER BY role")
        roles = [r[0] for r in cur]
        cur.execute("SELECT privilege FROM session_privs ORDER BY privilege")
        privs = [r[0] for r in cur]
        check("write role IHS_2026_ROLE is a default role", "IHS_2026_ROLE" in roles, roles)
        check("CREATE TABLE/VIEW/PROCEDURE held via the write role", {"CREATE TABLE", "CREATE VIEW", "CREATE PROCEDURE"} <= set(privs), privs)
        check("UPDATE on IHS_2026 succeeds (then rolled back)", can_write(cur))

        print("After SET ROLE IHS_2025_RO, IHS_2026_RO:")
        cur.execute("SET ROLE IHS_2025_RO, IHS_2026_RO")
        cur.execute("SELECT privilege FROM session_privs")
        privs = [r[0] for r in cur]
        check("session_privs is CREATE SESSION only", privs == ["CREATE SESSION"], privs)
        check("UPDATE on IHS_2026 is refused", not can_write(cur))

        print("Row counts (every object readable after SET ROLE):")
        cur.execute("SELECT owner, object_name FROM all_objects WHERE owner IN ('IHS_2024', 'IHS_2025', 'IHS_2026') "
                    "AND object_type IN ('TABLE', 'VIEW') ORDER BY object_name, owner")
        counts: dict[str, dict[str, int]] = {}
        for owner, name in cur.fetchall():
            counts.setdefault(name, {})[owner] = one(cur, f'SELECT COUNT(*) FROM {owner}."{name}"')
        print(f"  {'object':42}" + "".join(f"{c:>10}" for c in COHORTS))
        for name, by in counts.items():
            print(f"  {name:42}" + "".join(f"{by[c]:>10,}" if c in by else f"{'-':>10}" for c in COHORTS))
        empty = {f"{c}.{n}" for n, by in counts.items() for c, v in by.items() if v == 0}
        unexpected = sorted(empty - MEANT_EMPTY)
        check("every object has rows (but the deliberately empty one)", not unexpected, unexpected or "")
        check("the deliberately empty table is empty", MEANT_EMPTY <= empty, sorted(MEANT_EMPTY - empty) or "")
        check("drift: VW_DAILY_MOOD absent in IHS_2024", "IHS_2024" not in counts.get("VW_DAILY_MOOD", {}))
        check("drift: STG_SURVEYDICTIONARY replaces SURVEYDICTIONARY after 2024",
              set(counts["SURVEYDICTIONARY"]) == {"IHS_2024"} and "IHS_2024" not in counts["STG_SURVEYDICTIONARY"])

        print("Quirks (IHS_2025):")
        for label, sql in QUIRKS:
            n = one(cur, sql)
            check(label, n > 0, f"{n:,}")
        sample = one(cur, "SELECT STARTDATE FROM IHS_2025.VFITBITSLEEPLOGS WHERE ROWNUM = 1")
        check("V* views render timestamps as text", isinstance(sample, str), sample)
    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
    print("all checks passed")


if __name__ == "__main__":
    main()
