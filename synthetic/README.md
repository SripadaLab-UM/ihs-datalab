# Synthetic IHS database

**Everything in this database is fake.** Participants, device readings, sleep,
mood and survey answers are produced by a seeded random generator. No real
participant data was read or copied to build it. Only the *shape* comes from
the real Intern Health Study (IHS) Oracle database: object names, column names,
Oracle types and the differences between cohorts. Those were taken from a
metadata-only catalog export (names and types, no rows), plus the legacy R
scripts, the `ihsDataR` prototype and its synthetic fixtures, and the public
IHS data dictionaries.

DataLab uses it for development, CI, automated safety tests, evals, and the
practice profile. It runs on **Oracle Database Free**, so SQL written against
it uses the same dialect as the real database.

## Quick start

**Practice DataLab needs none of this.** It sets up and starts this database
itself, in Python, on Mac and Windows alike: the installer runs
`datalab --profile practice practice-db setup`, and practice DataLab starts it
again whenever it opens (see "In practice DataLab" below).

For development and CI, `db.sh` runs the same code (`python -m
datalab.practice_db`, in `backend/`) for the development container. It needs
Docker and [uv](https://docs.astral.sh/uv/).

```sh
synthetic/db.sh start      # create/start container datalab-synthetic-oracle, wait until ready
synthetic/db.sh generate   # drop and recreate the schemas, then load the data (~20 s)
synthetic/db.sh verify     # check privileges, row counts and quirks as DATALAB_RO
synthetic/db.sh stop       # stop (data is kept); `reset` deletes the container and its volume
```

`db.sh` looks after a container without DataLab's label only if it's
called `datalab-synthetic-oracle`; for another name
(`DATALAB_PRACTICE_DB_CONTAINER`) pass `--force`.

The first `start` takes about a minute. The image is Oracle's
`container-registry.oracle.com/database/free:latest-lite`, pinned by digest in
`backend/src/datalab/practice_db/__init__.py` (`IMAGE`). Or run it yourself:
`cd backend && uv run python -m datalab.practice_db generate`.

## In practice DataLab

- **Set up once.** The container `datalab-practice-oracle` keeps its data in
  the Docker volume `datalab-practice-oracle-data`, both labelled
  `datalab.practice-db`. Reinstalling or updating DataLab finds them again,
  and the data is loaded only when the database has none (no marker table:
  the generator creates it last, so a load that stopped part way is done
  again).
- **Started when needed.** Practice DataLab starts the container in the
  background when it opens (Settings → Connections says how it's going), then
  builds its catalog.
- **Reset only when asked:** `datalab --profile practice practice-db reset`,
  or Reset practice data in Settings → Connections (confirmed). Both delete
  the container and its volume and set them up again from scratch.
- **Left alone:** a container or volume of that name without DataLab's
  label, and a synthetic database already answering on port 1522 that
  DataLab didn't set up (this development container, say): practice DataLab
  uses that one as it is, and never loads data into it or resets it. It
  does so only if Docker says an Oracle Database Free container publishes
  the port, and then only `DATALAB_RO` checks the marker, once: DataLab
  never logs in as SYSTEM to a database it didn't make, and doesn't log in
  at all to anything else on the port (an SSH tunnel, say).
- **Upgrading from this development container:** if it's stopped when
  practice DataLab is set up or opens, practice DataLab starts it and uses
  it (a terminal asks first), rather than making a second database on its
  port.
- **One at a time.** Loading and resetting hold a lock in the practice data
  folder (`practice-db/.lock`), and `practice-db setup` leaves the database
  to a practice DataLab that's running.
- **Oracle's image** is downloaded for a first setup only; an update or a
  reinstall with a practice database already there downloads nothing. A
  busy registry never stops an install or an update.
- **Uninstalling** asks whether to delete the practice database too.
- `datalab --profile practice practice-db status | setup | start | stop | reset`.
  The real profile refuses these.

Tests and a second copy beside the first can move it with
`DATALAB_PRACTICE_DB_CONTAINER`, `DATALAB_PRACTICE_DB_VOLUME` and
`DATALAB_PRACTICE_DB_PORT` (always on 127.0.0.1).

## Connecting

| | |
|---|---|
| DSN | `localhost:1522/FREEPDB1` (host port 1522, so it never clashes with a real 1521) |
| App user | `DATALAB_RO` / `datalab_ro` |
| Admin | `SYSTEM` / `SynthDev2026` (also SYS and PDBADMIN) |

The port is published on **127.0.0.1 only**, so no other computer can reach
it. These passwords are **fixed, public, dev-only** values. That is acceptable
only because the container holds nothing but synthetic data. Never reuse them.
`db.sh` takes another port from `SYNTH_ORACLE_PORT`.

```python
import oracledb
conn = oracledb.connect(user="DATALAB_RO", password="datalab_ro", dsn="localhost:1522/FREEPDB1")
```

## Privileges: mirroring the real account

`DATALAB_RO` has the same privilege shape as the real shared service account
(see `docs/SAFETY.md`), so DataLab's role handling can be tested:

- `CREATE SESSION` is granted directly, along with **direct SELECT** on every
  IHS_2024 object.
- Role `IHS_2025_RO` has SELECT on IHS_2025. Role `IHS_2026_RO` has SELECT on
  IHS_2026.
- Role **`IHS_2026_ROLE`** is over-privileged on purpose. It has SELECT,
  UPDATE, DELETE and ALTER on every IHS_2026 table, plus CREATE TABLE, CREATE
  VIEW and CREATE PROCEDURE.
- All three roles are **default roles**, so a plain connection can write to
  IHS_2026.

`SET ROLE IHS_2025_RO, IHS_2026_RO` drops the write role. After that,
`session_privs` contains only `CREATE SESSION`, and every cohort stays
readable. `verify` checks both states. Unlike the real account,
`DATALAB_RO` has no direct grants on IHS_2025 objects or IHS_2026 views. That
makes the test stricter, because reading those objects after `SET ROLE` proves
the read-only roles really are enabled.

The cohort schemas are schema-only users (`NO AUTHENTICATION`), so nobody can
log in as them.

## Seed and determinism

The seed is in `spec/cohorts.yaml` (`seed: 20260926`). Override it with
`synthetic/db.sh generate --seed N`. The same seed and spec always produce identical rows.
The cohort windows and data end dates are fixed dates, never "today". Each
cohort gets its own random stream, so changing one cohort does not change the
others. Re-running always drops and recreates everything: the cohort users,
`DATALAB_RO`, the roles, and any session they hold.

## Cohorts and objects

| Cohort | Participants | Window | Data until |
|---|---|---|---|
| IHS_2024 | 150 (`SYN24-0001`…) | 2024-04-01, internship 2024-07-01 | 2025-07-01 |
| IHS_2025 | 150 (`SYN25-0001`…) | 2025-04-01, internship 2025-07-01 | 2026-07-01 |
| IHS_2026 | 40 (`SYN26-0001`…) | 2026-04-01, internship 2026-07-01 | 2026-09-20 (in progress) |

Participant IDs always start with `SYN`. STUDY_PARTICIPANT_ID values look like
`9250001`. The PII columns hold obviously fake values: email
`syn25-0001@example.invalid`, first name "Synthetic", phone and postal code
NULL.

Participants are about 40% Fitbit, 35% Apple Watch, 20% Garmin and 5% Oura.
IHS_2026 has more Oura, and IHS_2024 has none. The objects are listed in
`spec/objects.yaml`:

- **Participants:** STUDYPARTICIPANTS, VW_IHS_PARTICIPANT_SUMMARY
- **Fitbit:** FITBITDAILYDATA, FITBITSLEEPLOGS (+ V views)
- **Garmin:** GARMINDAILYSUMMARY, GARMINSLEEPSUMMARY, GARMINHRVSUMMARY (+ V views), GARMINSLEEPSUMMARY_NAPS
- **HealthKit:** HEALTHKITSTATISTICS_DAILYSTEPS, HEALTHKITSAMPLES_RESTINGHEARTRATE,
  HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL, HEALTHKITSAMPLES_HEARTRATEVARIABILITY,
  HEALTHKITACTIVITYSUMMARIES (+ V views)
- **Oura:** OURADAILYACTIVITY (+ VOURADAILYACTIVITY in 2026)
- **Mood:** VW_DAILY_MOOD. Daily 1–10 score, stored as text.
- **Surveys:** SURVEYRESULTS, SURVEYQUESTIONRESULTS, SURVEYDICTIONARY /
  STG_SURVEYDICTIONARY, and the wide views VW_BASELINE_SURVEY, VW_SEP_SURVEY,
  VW_DEC_SURVEY, VW_MAR_SURVEY and VW_JUN_SURVEY. Each wave has the PHQ-9
  items plus tobacco and alcohol items.

These cover every object used by the 2025 daily-metrics pipeline (`ihsDataR`
data sources) and the default routines. All of their queries run unchanged.

**Views.** The `V*` objects are real Oracle views over their base tables. As
in the real database, they render `TIMESTAMP WITH TIME ZONE` columns as
`VARCHAR2(26)` text, for example `2025-05-02 23:08:37 -04:00`. The `VW_*`
objects are views in reality but plain tables here, filled by the generator.

**Cohort drift** (all taken from the metadata export):

- IHS_2024 has no Oura tables, no VW_DAILY_MOOD, and no WITHDRAWDATE column.
  Its FITBITSLEEPLOGS has an extra STARTDATE_UTC column.
- IHS_2024 has SURVEYDICTIONARY (SURVEYKEY RAW, SURVEYVERSION NUMBER). Later
  cohorts only have STG_SURVEYDICTIONARY, where both columns are VARCHAR2.
  This is why the smoking routine joins on `TO_CHAR(sr.SURVEYVERSION)`.
- IHS_2024's SURVEYRESULTS lacks the SCHEDULE*, USERTYPE, USER_EMAIL and LOCALE
  columns. IHS_2026 widens SCHEDULENAME.
- IHS_2026 has no DEC/MAR/JUN survey views, and its participant summary has
  only Q1_SURVEY_COMPLETION. Only IHS_2026 has VOURADAILYACTIVITY.
- The baseline view names the tobacco item `tobacco0`. The quarterly views
  use `substance_tobacco1..4`. The wide survey views use quoted mixed-case
  identifiers, such as `"interest0"` and `"Black tea"`.

## Quirks included (on purpose)

These are the real-world problems the legacy R pipelines handle. `verify`
counts each one.

| Quirk | Where |
|---|---|
| Duplicate day rows. An earlier partial row is superseded by one with a later INSERTEDDATE. | GARMINDAILYSUMMARY (plus ~1% exact duplicates), HEALTHKITSTATISTICS_DAILYSTEPS, HEALTHKITACTIVITYSUMMARIES, OURADAILYACTIVITY (tiny early step count), GARMINHRVSUMMARY |
| Garmin VALIDATION values: ENHANCED_FINAL, AUTO_FINAL, AUTO_TENTATIVE, MANUAL, DEVICE. ENHANCED_TENTATIVE rows are later replaced by FINAL, and some FINAL nights are re-sent. | GARMINSLEEPSUMMARY |
| Naps with NAPVALIDATION AUTO/DEVICE/MANUAL, no PARTICIPANTIDENTIFIER (join via PARTICIPANTID), and SUMMARYID sometimes NULL | GARMINSLEEPSUMMARY_NAPS. Fitbit naps have ISMAINSLEEP 'false'. |
| Sleep that spans midnight, and bedtimes after midnight | all sleep tables |
| Garmin epoch seconds with STARTTIMEOFFSETINSECONDS. Sleep DURATION excludes awake time. | Garmin tables |
| Timezone offsets on every local timestamp. DST is handled by the zone rules. ~35% of participants move to a new time zone before internship. | all TIMESTAMP WITH TIME ZONE columns. INSERTEDDATE is UTC. |
| HealthKit daily buckets that don't start at local midnight after a move | HEALTHKITSTATISTICS_DAILYSTEPS, HEALTHKITACTIVITYSUMMARIES |
| SOURCEPRODUCTTYPE with and without "Watch": iPhone rows written by Garmin Connect, Oura or Withings, plus rare NULLs | HEALTHKITSAMPLES_* |
| Older watches that record plain "Asleep" with no stages (2024). Overlapping stage records. Whole nights duplicated verbatim. | HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL |
| More than one resting-HR sample per day, some ending after midnight | HEALTHKITSAMPLES_RESTINGHEARTRATE |
| Garmin HRV CALENDARDATE with a time component (UTC midnight shifted to local) | GARMINHRVSUMMARY |
| Garmin sleep CALENDARDATE occasionally the bed date instead of the wake date | GARMINSLEEPSUMMARY |
| Fitbit HR-zone minutes NULL on some days (pipelines fall back to tracker minutes). 'classic' vs 'stages' sleep logs. NULL RHR/HRV. | FITBITDAILYDATA, FITBITSLEEPLOGS |
| Missing days, multi-day sync gaps, late enrollment, dropout (~15%), WITHDRAWDATE | all device tables, STUDYPARTICIPANTS |
| Screened-but-not-enrolled participants: NULL STUDY_PARTICIPANT_ID and SECONDARYIDENTIFIER, but they still have device data. Column comments say so. | participant tables |
| A table that exists but is empty (a feed not loaded yet), though the cohort has Garmin users | IHS_2026.GARMINHRVSUMMARY |
| In the intern year, people whose mood runs low answer the daily mood question less often, so a pooled average understates the within-person drop | VW_DAILY_MOOD |
| Numbers stored as text | HealthKit VALUE, MOOD_SCORE, ANSWERS, STG SURVEYVERSION |
| Phone-counted HealthKit steps for some non-Apple-Watch iPhone users, so one participant-day can come from two devices | HEALTHKITSTATISTICS_DAILYSTEPS |
| Survey version 2 for about half of Q1 respondents (2025+) | SURVEYRESULTS, dictionary |

The data also carry a mild intern-year effect: from `internship_start`,
participants sleep less, take fewer steps, report lower mood, and have
slightly higher PHQ-9 items.

## Files

In `backend/src/datalab/practice_db/`, so the installed DataLab carries them:

- `spec/objects.yaml`: objects, columns, Oracle types and per-cohort drift,
  copied from the metadata export. Large tables keep all their columns. The
  ~200-column survey views keep a subset.
- `spec/cohorts.yaml`: seed, cohort windows, device mix, survey waves and items.
- `generate.py`: creates users, roles, tables and views, then generates and
  bulk-loads the data, and creates the marker last.
- `verify.py`: privilege, row-count and quirk checks as DATALAB_RO.
- `guard.py`: the checks below, and the marker.
- `__init__.py`: the container's lifecycle, through the `docker` command.
- `__main__.py`: the commands `db.sh` runs.

Here, `db.sh` only.

## Invented details

The metadata export gives names and types, not values. These value formats are
therefore plausible guesses, not copies of reality:

- survey names, and the SCHEDULE* values;
- the HealthKit SOURCENAME and SOURCEIDENTIFIER values and the UNITS on sleep
  rows;
- the Garmin SUMMARYID format;
- the Fitbit LOGTYPE values;
- the Oura IDs;
- the MOOD_COMMENT texts;
- the dictionary ANSWERCHOICES format.

If a pipeline depends on an exact value format, check it against the real data
dictionary rather than against this database.

## Safeguards

- **Local only.** The port is published on `127.0.0.1`, never on the
  network. Practice DataLab stops, and won't use, a container of its own
  that's published anywhere else.
- **The code refuses real databases.** The generator and `verify` check
  that the address is this computer and that the server is Oracle Database
  Free (`FREEPDB1`) before doing anything (see `guard.py`).
- **Marker table.** The generator creates `DATALAB_SYNTHETIC.MARKER`.
  DataLab's practice profile refuses to query any database that lacks it.

