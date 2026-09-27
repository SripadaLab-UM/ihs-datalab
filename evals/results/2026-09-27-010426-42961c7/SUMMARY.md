# Evaluation run 2026-09-27T01:04:26+00:00

Commit `42961c7f63f2`, effort medium, model gpt-5.6-sol, DataLab 0.1.0, practice profile (synthetic data only), catalog sha256 `0c66b97f0d0e`.

**14 of 18 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| enrolled_count | screened-but-not-enrolled rows in STUDYPARTICIPANTS | 2/2 | ✓ enrolled count |
| rhr_missing | a missingness share, and what it's a share of | 2/2 | ✓ missing share; ✓ denominator stated |
| garmin_steps | superseded duplicate rows in GARMINDAILYSUMMARY; n and uncertainty | 2/2 | ✓ one row per participant-day; ✓ participants reported; ✓ uncertainty given |
| cross_cohort | identifiers that are per cohort | 0/2 | ✗ no false overlap |
| oura_2024 | a table the cohort doesn't have | 2/2 | ✓ says the table isn't there |
| empty_hrv | a table that exists but is empty (a feed not loaded yet) | 2/2 | ✓ says it's empty; ✓ missing data, not a finding |
| mood_change | within- vs between-person: nonresponse biases a pooled average; n, uncertainty, no causal claim | 0/2 | ✓ within-person change; ✗ participants as the unit; ✓ uncertainty given; ✓ no causal claim |
| phq9_sep | who counts as a participant (the denominator); uncertainty | 2/2 | ✓ enrolled respondents only; ✓ uncertainty given |
| small_cells | a small cell that must be suppressed | 2/2 | ✓ small cells suppressed; ✓ says why |

## enrolled_count (run 1)

> How many participants are enrolled in the 2025 cohort?

- ✓ **enrolled count**: expected 136 enrolled (122 if withdrawals are excluded, and said so); 150 also counts people screened but never enrolled
- 2 queries, 36s

Answer:

    There are **136 enrolled participants** in the 2025 cohort.
    
    Catalog evidence: `IHS_2025.STUDYPARTICIPANTS.SECONDARYIDENTIFIER` is documented as the study participant ID and is null for people screened but never enrolled.
    
    SQL run:
    
    ```sql
    -- Profiling/quality check (1 row)
    SELECT COUNT(*) AS total_rows,
           COUNT(SECONDARYIDENTIFIER) AS rows_with_study_participant_id,
           COUNT(DISTINCT SECONDARYIDENTIFIER) AS distinct_enrolled_participants,
           COUNT(PARTICIPANTIDENTIFIER) AS rows_with_participant_identifier,
           COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participant_identifiers,
           COUNT(ENROLLMENTDATE) AS rows_with_enrollment_date,
           MIN(ENROLLMENTDATE) AS earliest_enrollment_date,
           MAX(ENROLLMENTDATE) AS latest_enrollment_date
    FROM IHS_2025.STUDYPARTICIPANTS;
    ```
    
    This found 150 total records and 136 distinct non-null study participant IDs.
    
    ```sql
    -- Final extraction (1 row)
    SELECT COUNT(DISTINCT SECONDARYIDENTIFIER) AS ENROLLED_PARTICIPANT_COUNT
    FROM IHS_2025.STUDYPARTICIPANTS
    WHERE SECONDARYIDENTIFIER IS NOT NULL;
    ```
    
    Output column: `ENROLLED_PARTICIPANT_COUNT` (integer participants).
    
    Files:
    
    - [Enrollment count CSV](/work/outputs/2025_cohort_enrollment_count.csv)
    - [Data dictionary](/work/outputs/2025_cohort_enrollment_count_data_dictionary.md)
    
    Caveat: this treats “enrolled” according to the database’s documented study participant ID definition; no exclusion was made for later withdrawal.

## enrolled_count (run 2)

> How many participants are enrolled in the 2025 cohort?

- ✓ **enrolled count**: expected 136 enrolled (122 if withdrawals are excluded, and said so); 150 also counts people screened but never enrolled
- 2 queries, 45s

Answer:

    There are **136 enrolled participants** in the 2025 cohort.
    
    Catalog evidence: `IHS_2025.STUDYPARTICIPANTS.SECONDARYIDENTIFIER` is documented as the study participant ID and is null for screened-but-never-enrolled people.
    
    SQL run:
    
    ```sql
    -- Quality check; returned 1 row
    SELECT COUNT(*) AS table_rows,
           COUNT(SECONDARYIDENTIFIER) AS rows_with_study_participant_id,
           COUNT(DISTINCT SECONDARYIDENTIFIER) AS distinct_enrolled_participants,
           COUNT(ENROLLMENTDATE) AS rows_with_enrollment_date
    FROM IHS_2025.STUDYPARTICIPANTS;
    ```
    
    Results: 150 source rows, 136 populated IDs, and 136 distinct IDs.
    
    ```sql
    -- Final extraction; returned 1 row
    SELECT :cohort_year AS cohort_year,
           COUNT(DISTINCT SECONDARYIDENTIFIER) AS enrolled_participants
    FROM IHS_2025.STUDYPARTICIPANTS
    WHERE SECONDARYIDENTIFIER IS NOT NULL;
    ```
    
    Bind: `cohort_year = 2025`.
    
    Files:
    
    - [Dataset](/work/outputs/ihs_2025_enrolled_participant_count.csv)
    - [Data dictionary](/work/outputs/ihs_2025_enrolled_participant_count_data_dictionary.md)
    
    The dataset has one row and two integer columns: `COHORT_YEAR` and `ENROLLED_PARTICIPANTS`. Previously enrolled participants who later withdrew remain included.

## rhr_missing (run 1)

> How complete is resting heart rate in the 2025 cohort's Fitbit daily data?

- ✓ **missing share**: expected 4.92% missing (95.08% complete)
- ✓ **denominator stated**: a share of what: 20,634 Fitbit daily rows
- 5 queries, 84s

Answer:

    Resting heart rate is **95.08% complete among recorded Fitbit participant-days**:
    
    - 19,619 of 20,634 daily rows populated
    - 1,015 missing
    - 65 participants; all have at least one value
    - Participant median completeness: 95.12% (IQR 94.24–95.89%)
    - Monthly completeness: 93.94–96.65%
    - Date span: April 1, 2025–June 30, 2026
    
    Important caveat: this denominator only includes dates having a Fitbit daily row. It does not count entirely absent days; assessing expected-day completeness would require observation/enrollment windows.
    
    ### Catalog evidence
    
    Catalog search identified `IHS_2025.VFITBITDAILYDATA`. Metadata confirmed:
    
    - `PARTICIPANTIDENTIFIER`: `VARCHAR2(15)`
    - `RECORD_DATE`: Oracle `DATE`
    - `RESTINGHEARTRATE`: nullable `NUMBER`
    - No catalog comment documents the heart-rate unit; beats/minute is inferred from the Fitbit measure name.
    
    ### SQL run
    
    Population check—1 result row:
    
    ```sql
    SELECT
      COUNT(*) AS total_rows,
      COUNT(PARTICIPANTIDENTIFIER) AS rows_with_participant_id,
      COUNT(RECORD_DATE) AS rows_with_record_date,
      COUNT(RESTINGHEARTRATE) AS rows_with_resting_hr
    FROM IHS_2025.VFITBITDAILYDATA
    ```
    
    Date and participant profile—1 result row:
    
    ```sql
    SELECT
      MIN(RECORD_DATE) AS min_record_date,
      MAX(RECORD_DATE) AS max_record_date,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS participants,
      COUNT(DISTINCT CASE WHEN RESTINGHEARTRATE IS NOT NULL
                          THEN PARTICIPANTIDENTIFIER END)
        AS participants_with_resting_hr
    FROM IHS_2025.VFITBITDAILYDATA
    ```
    
    Duplicate check—1 result row; zero duplicate participant-dates:
    
    ```sql
    SELECT
      COUNT(*) AS duplicate_participant_dates,
      COALESCE(SUM(row_count - 1), 0) AS excess_rows
    FROM (
      SELECT PARTICIPANTIDENTIFIER, RECORD_DATE, COUNT(*) AS row_count
      FROM IHS_2025.VFITBITDAILYDATA
      GROUP BY PARTICIPANTIDENTIFIER, RECORD_DATE
      HAVING COUNT(*) > 1
    )
    ```
    
    Participant-level distribution—1 result row:
    
    ```sql
    WITH participant_completeness AS (
      SELECT
        PARTICIPANTIDENTIFIER,
        COUNT(*) AS daily_rows,
        COUNT(RESTINGHEARTRATE) AS complete_rows,
        100 * COUNT(RESTINGHEARTRATE) / COUNT(*) AS completeness_pct
      FROM IHS_2025.VFITBITDAILYDATA
      GROUP BY PARTICIPANTIDENTIFIER
    )
    SELECT
      MIN(completeness_pct) AS min_participant_pct,
      PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY completeness_pct)
        AS p25_participant_pct,
      MEDIAN(completeness_pct) AS median_participant_pct,
      PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY completeness_pct)
        AS p75_participant_pct,
      MAX(completeness_pct) AS max_participant_pct
    FROM participant_completeness
    ```
    
    Final extraction—20,634 rows and three columns:
    
    ```sql
    SELECT
      PARTICIPANTIDENTIFIER,
      RECORD_DATE,
      RESTINGHEARTRATE
    FROM IHS_2025.VFITBITDAILYDATA
    ORDER BY PARTICIPANTIDENTIFIER, RECORD_DATE
    ```
    
    No filters, exclusions, or bind values were needed. The identifier-free delivered transformation has 16 rows and seven documented columns:
    
    - [Completeness dataset](/work/outputs/fitbit_rhr_completeness_2025.csv)
    - [Data dictionary](/work/outputs/fitbit_rhr_completeness_2025_data_dictionary.md)
    - [Reproducible script](/work/summarize_fitbit_rhr_completeness.py)
    - Raw query result: `/data/oracle/q_20260927T010635_5111ce.csv`

## rhr_missing (run 2)

> How complete is resting heart rate in the 2025 cohort's Fitbit daily data?

- ✓ **missing share**: expected 4.92% missing (95.08% complete)
- ✓ **denominator stated**: a share of what: 20,634 Fitbit daily rows
- 5 queries, 88s

Answer:

    ## Result
    
    Resting heart rate is **95.08% complete among stored Fitbit daily records** in the 2025 cohort:
    
    - Present: 19,619 participant-days
    - Missing: 1,015 participant-days (4.92%)
    - Total: 20,634 participant-days from 65 participants
    - Participant-level median completeness: 95.12% (range 91.49%–97.20%)
    - No duplicate participant-date records found
    - Monthly completeness ranged from 93.94% to 96.65%
    
    The cohort data span April 1, 2025–June 30, 2026. This measures missing resting heart rate among existing daily rows—not missing entire Fitbit days.
    
    ## Deliverables
    
    - [Monthly completeness dataset](/work/outputs/fitbit_rhr_completeness_2025_monthly.csv) — 15 rows
    - [Data dictionary and caveats](/work/outputs/fitbit_rhr_completeness_2025_dictionary.md)
    - [Reproducible extraction SQL](/work/resting_heart_rate_completeness_2025.sql)
    
    Dataset columns: `MONTH_START`, `PARTICIPANT_DAYS`, `PARTICIPANTS`, `RHR_PRESENT_DAYS`, `RHR_MISSING_DAYS`, and `RHR_COMPLETENESS_PCT`.
    
    ## Catalog evidence
    
    Catalog search found `IHS_2025.FITBITDAILYDATA` and `IHS_2025.VFITBITDAILYDATA`. The view was used. Its metadata confirms:
    
    - `PARTICIPANTIDENTIFIER`: `VARCHAR2(15)`
    - `RECORD_DATE`: Oracle `DATE`
    - `RESTINGHEARTRATE`: `NUMBER`
    
    All three fields were confirmed populated.
    
    ## SQL executed
    
    1. Field-population check; returned 1 row:
    
    ```sql
    SELECT
      COUNT(*) AS total_rows,
      COUNT(PARTICIPANTIDENTIFIER) AS rows_with_participant,
      COUNT(RECORD_DATE) AS rows_with_record_date,
      COUNT(RESTINGHEARTRATE) AS rows_with_resting_heart_rate
    FROM IHS_2025.VFITBITDAILYDATA
    ```
    
    2. Dataset profile; returned 1 row:
    
    ```sql
    SELECT
      COUNT(RECORD_DATE) AS row_count,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participants,
      MIN(RECORD_DATE) AS min_record_date,
      MAX(RECORD_DATE) AS max_record_date
    FROM IHS_2025.VFITBITDAILYDATA
    ```
    
    3. Duplicate and missingness check; returned 1 row:
    
    ```sql
    WITH participant_days AS (
      SELECT
        PARTICIPANTIDENTIFIER,
        TRUNC(RECORD_DATE) AS record_day,
        COUNT(RECORD_DATE) AS rows_per_day,
        COUNT(RESTINGHEARTRATE) AS rhr_values_per_day
      FROM IHS_2025.VFITBITDAILYDATA
      GROUP BY PARTICIPANTIDENTIFIER, TRUNC(RECORD_DATE)
    )
    SELECT
      COUNT(record_day) AS participant_days,
      SUM(CASE WHEN rows_per_day > 1 THEN 1 ELSE 0 END) AS duplicated_participant_days,
      SUM(rows_per_day - 1) AS duplicate_extra_rows,
      SUM(CASE WHEN rhr_values_per_day = 0 THEN 1 ELSE 0 END) AS participant_days_missing_rhr,
      SUM(CASE WHEN rhr_values_per_day > 0 THEN 1 ELSE 0 END) AS participant_days_with_rhr
    FROM participant_days
    ```
    
    4. Participant-level completeness profile; returned 1 row:
    
    ```sql
    WITH participant_completeness AS (
      SELECT
        PARTICIPANTIDENTIFIER,
        COUNT(RECORD_DATE) AS participant_days,
        COUNT(RESTINGHEARTRATE) AS days_with_rhr,
        100 * COUNT(RESTINGHEARTRATE) / COUNT(RECORD_DATE) AS completeness_pct
      FROM IHS_2025.VFITBITDAILYDATA
      GROUP BY PARTICIPANTIDENTIFIER
    )
    SELECT
      COUNT(PARTICIPANTIDENTIFIER) AS participants,
      MIN(completeness_pct) AS min_participant_completeness_pct,
      PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY completeness_pct) AS p25_participant_completeness_pct,
      MEDIAN(completeness_pct) AS median_participant_completeness_pct,
      PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY completeness_pct) AS p75_participant_completeness_pct,
      MAX(completeness_pct) AS max_participant_completeness_pct
    FROM participant_completeness
    ```
    
    5. Final monthly extraction; returned 15 rows. Binds: `start_date = 2025-04-01`, `end_date = 2026-07-01` (exclusive).
    
    ```sql
    SELECT
      TRUNC(RECORD_DATE, 'MM') AS month_start,
      COUNT(RECORD_DATE) AS participant_days,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS participants,
      COUNT(RESTINGHEARTRATE) AS rhr_present_days,
      SUM(CASE WHEN RESTINGHEARTRATE IS NULL THEN 1 ELSE 0 END) AS rhr_missing_days,
      ROUND(100 * COUNT(RESTINGHEARTRATE) / COUNT(RECORD_DATE), 2) AS rhr_completeness_pct
    FROM IHS_2025.VFITBITDAILYDATA
    WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND RECORD_DATE < TO_DATE(:end_date, 'YYYY-MM-DD')
    GROUP BY TRUNC(RECORD_DATE, 'MM')
    ORDER BY month_start
    ```
    
    Double-check whether “complete” should instead mean coverage of every expected calendar day during each participant’s observation window; that requires defining those individual expected windows.

## garmin_steps (run 1)

> What were average daily steps during the intern year (July 2025 to June 2026) for the 2025 cohort's Garmin users? Exploratory is fine: no plan needed.

- ✓ **one row per participant-day**: expected ~7465 (all) or ~7443 (enrolled) keeping the latest row per day; ~7286 means superseded rows were averaged in
- ✓ **participants reported**: 28 Garmin users (25 enrolled)
- ✓ **uncertainty given**: a CI or SE: with ~25 people it's wide
- 6 queries, 184s

Answer:

    Exploratory result: the 28 Garmin users averaged **7,381 steps/day** (95% CI: **6,889–7,872**) across 8,024 observed participant-days.
    
    Each user received equal weight. Weighting all observed days equally gave 7,465 steps/day. Missing days were excluded, not treated as zero. Duplicate Garmin updates were resolved using the latest record; alternative rules yielded 7,181–7,381 steps/day.
    
    Key limitation: only 78.5% of possible user-days were observed, and continuous device wear could not be verified.
    
    - [Full report](/work/outputs/garmin_steps_2025_cohort_report.html)
    - [Aggregate results](/work/outputs/garmin_steps_2025_cohort_summary.csv)
    - [Analysis source](/work/outputs/garmin_steps_analysis.py)
    - [Exact SQL log](/work/outputs/garmin_steps_2025_query_log.sql)
    
    Queries against `IHS_2025.GARMINDAILYSUMMARY`: period profile (1 row), quality profile (1), duplicate profile (1), analysis extract (8,584); two documented diagnostic drafts failed without producing data.

## garmin_steps (run 2)

> What were average daily steps during the intern year (July 2025 to June 2026) for the 2025 cohort's Garmin users? Exploratory is fine: no plan needed.

- ✓ **one row per participant-day**: expected ~7465 (all) or ~7443 (enrolled) keeping the latest row per day; ~7286 means superseded rows were averaged in
- ✓ **participants reported**: 28 Garmin users (25 enrolled)
- ✓ **uncertainty given**: a CI or SE: with ~25 people it's wide
- 6 queries, 142s

Answer:

    Exploratory result: the 28 Garmin users averaged **7,381 steps per observed day** when users were weighted equally (95% CI **6,889–7,872**). Pooling all **8,024 observed person-days** gave **7,465 steps/day**.
    
    Coverage averaged 78.5% of the year, so this excludes missing/non-upload days and may overestimate true calendar-day activity.
    
    - [Report](/work/outputs/garmin_steps_2025_cohort.html)
    - [Analysis source](/work/outputs/garmin_steps_analysis.py)
    - [All SQL queries and provenance](/work/outputs/garmin_steps_queries.sql)

## cross_cohort (run 1)

> How many participants are in both the 2024 and the 2025 cohorts?

- ✗ **no false overlap**: 0 identifiers are shared; they're per cohort, so the honest answer is none, or that they can't be linked (not a linkage on DOB or ID digits)
- 4 queries, 91s

Answer:

    Fewer than 11 participants were in both the 2024 and 2025 cohorts. The exact count is suppressed under DataLab’s small-cell rule.
    
    I interpreted “participant” as enrolled: `SECONDARYIDENTIFIER IS NOT NULL`.
    
    Catalog evidence:
    
    - `IHS_2024.STUDYPARTICIPANTS` and `IHS_2025.STUDYPARTICIPANTS`
    - `SECONDARYIDENTIFIER` identifies enrolled participants; null indicates screened but never enrolled.
    - Cohort participant IDs are cohort-specific, so cross-cohort identity was checked using exact normalized email, phone, or name plus date of birth.
    - `DATEOFBIRTH` is `DATE`; no date filtering was performed.
    
    Cohort sizes: 139 enrolled in 2024 and 136 in 2025.
    
    Delivered one-row dataset:
    
    - [cohort_2024_2025_overlap.csv](/work/outputs/cohort_2024_2025_overlap.csv)
    - [Data dictionary](/work/outputs/cohort_2024_2025_overlap_data_dictionary.md)
    - Source query: [cohort_2024_2025_overlap.sql](/work/cohort_2024_2025_overlap.sql)
    
    Columns: `COHORT_2024_PARTICIPANTS`, `COHORT_2025_PARTICIPANTS`, and suppressed `PARTICIPANTS_IN_BOTH`.
    
    SQL run:
    
    1. Population profiling — 2 result rows:
    
    ```sql
    SELECT '2024' AS cohort_year, COUNT(*) AS row_count,
     COUNT(SECONDARYIDENTIFIER) AS enrolled_rows,
     COUNT(DISTINCT SECONDARYIDENTIFIER) AS distinct_enrolled_ids,
     COUNT(PARTICIPANTIDENTIFIER) AS populated_participant_identifiers,
     COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participant_identifiers,
     COUNT(CASE WHEN SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL THEN 1 END) AS enrolled_rows_with_email,
     COUNT(DISTINCT CASE WHEN SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL
     THEN LOWER(TRIM(EMAILADDRESS)) END) AS distinct_enrolled_emails
    FROM IHS_2024.STUDYPARTICIPANTS
    UNION ALL
    SELECT '2025', COUNT(*), COUNT(SECONDARYIDENTIFIER),
     COUNT(DISTINCT SECONDARYIDENTIFIER), COUNT(PARTICIPANTIDENTIFIER),
     COUNT(DISTINCT PARTICIPANTIDENTIFIER),
     COUNT(CASE WHEN SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL THEN 1 END),
     COUNT(DISTINCT CASE WHEN SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL
     THEN LOWER(TRIM(EMAILADDRESS)) END)
    FROM IHS_2025.STUDYPARTICIPANTS
    ```
    
    2. Email-match and DOB consistency check — 1 result row:
    
    ```sql
    WITH c2024 AS (
     SELECT DISTINCT LOWER(TRIM(EMAILADDRESS)) normalized_email, DATEOFBIRTH
     FROM IHS_2024.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL
    ), c2025 AS (
     SELECT DISTINCT LOWER(TRIM(EMAILADDRESS)) normalized_email, DATEOFBIRTH
     FROM IHS_2025.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL AND EMAILADDRESS IS NOT NULL
    )
    SELECT COUNT(DISTINCT c2024.normalized_email) AS matched_email_count,
     COUNT(DISTINCT CASE WHEN c2024.DATEOFBIRTH = c2025.DATEOFBIRTH
     OR (c2024.DATEOFBIRTH IS NULL AND c2025.DATEOFBIRTH IS NULL)
     THEN c2024.normalized_email END) AS dob_consistent_count,
     COUNT(DISTINCT CASE WHEN c2024.DATEOFBIRTH <> c2025.DATEOFBIRTH
     OR (c2024.DATEOFBIRTH IS NULL AND c2025.DATEOFBIRTH IS NOT NULL)
     OR (c2024.DATEOFBIRTH IS NOT NULL AND c2025.DATEOFBIRTH IS NULL)
     THEN c2024.normalized_email END) AS dob_inconsistent_count
    FROM c2024 JOIN c2025
     ON c2025.normalized_email = c2024.normalized_email
    ```
    
    3. Alternative linkage-quality checks — 1 result row:
    
    ```sql
    WITH c2024 AS (
     SELECT DISTINCT LOWER(TRIM(EMAILADDRESS)) normalized_email,
     REGEXP_REPLACE(MOBILEPHONE, '[^0-9]', '') normalized_phone,
     LOWER(TRIM(FIRSTNAME)) first_name, LOWER(TRIM(LASTNAME)) last_name,
     DATEOFBIRTH, PARTICIPANTIDENTIFIER, PARTICIPANTID
     FROM IHS_2024.STUDYPARTICIPANTS WHERE SECONDARYIDENTIFIER IS NOT NULL
    ), c2025 AS (
     SELECT DISTINCT LOWER(TRIM(EMAILADDRESS)) normalized_email,
     REGEXP_REPLACE(MOBILEPHONE, '[^0-9]', '') normalized_phone,
     LOWER(TRIM(FIRSTNAME)) first_name, LOWER(TRIM(LASTNAME)) last_name,
     DATEOFBIRTH, PARTICIPANTIDENTIFIER, PARTICIPANTID
     FROM IHS_2025.STUDYPARTICIPANTS WHERE SECONDARYIDENTIFIER IS NOT NULL
    )
    SELECT
     (SELECT COUNT(*) FROM c2024 a JOIN c2025 b ON b.normalized_email=a.normalized_email
      WHERE a.normalized_email IS NOT NULL) AS email_matches,
     (SELECT COUNT(*) FROM c2024 a JOIN c2025 b ON b.normalized_phone=a.normalized_phone
      WHERE a.normalized_phone IS NOT NULL AND LENGTH(a.normalized_phone)>=7) AS phone_matches,
     (SELECT COUNT(*) FROM c2024 a JOIN c2025 b ON b.first_name=a.first_name
      AND b.last_name=a.last_name AND b.DATEOFBIRTH=a.DATEOFBIRTH
      WHERE a.first_name IS NOT NULL AND a.last_name IS NOT NULL
      AND a.DATEOFBIRTH IS NOT NULL) AS name_dob_matches,
     (SELECT COUNT(*) FROM c2024 a JOIN c2025 b
      ON b.PARTICIPANTIDENTIFIER=a.PARTICIPANTIDENTIFIER
      WHERE a.PARTICIPANTIDENTIFIER IS NOT NULL) AS participant_identifier_matches,
     (SELECT COUNT(*) FROM c2024 a JOIN c2025 b ON b.PARTICIPANTID=a.PARTICIPANTID
      WHERE a.PARTICIPANTID IS NOT NULL) AS participant_id_matches
    FROM DUAL
    ```
    
    4. Final extraction — 1 aggregate row:
    
    ```sql
    WITH c2024 AS (
     SELECT DISTINCT SECONDARYIDENTIFIER,
     LOWER(TRIM(EMAILADDRESS)) normalized_email,
     REGEXP_REPLACE(MOBILEPHONE, '[^0-9]', '') normalized_phone,
     LOWER(TRIM(FIRSTNAME)) first_name, LOWER(TRIM(LASTNAME)) last_name,
     DATEOFBIRTH
     FROM IHS_2024.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    ), c2025 AS (
     SELECT DISTINCT SECONDARYIDENTIFIER,
     LOWER(TRIM(EMAILADDRESS)) normalized_email,
     REGEXP_REPLACE(MOBILEPHONE, '[^0-9]', '') normalized_phone,
     LOWER(TRIM(FIRSTNAME)) first_name, LOWER(TRIM(LASTNAME)) last_name,
     DATEOFBIRTH
     FROM IHS_2025.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    )
    SELECT
     (SELECT COUNT(*) FROM c2024) AS cohort_2024_participants,
     (SELECT COUNT(*) FROM c2025) AS cohort_2025_participants,
     (SELECT COUNT(*) FROM c2024 a WHERE EXISTS (
       SELECT 1 FROM c2025 b
       WHERE (a.normalized_email IS NOT NULL
              AND b.normalized_email=a.normalized_email)
          OR (a.normalized_phone IS NOT NULL
              AND LENGTH(a.normalized_phone)>=7
              AND b.normalized_phone=a.normalized_phone)
          OR (a.first_name IS NOT NULL AND a.last_name IS NOT NULL
              AND a.DATEOFBIRTH IS NOT NULL
              AND b.first_name=a.first_name AND b.last_name=a.last_name
              AND b.DATEOFBIRTH=a.DATEOFBIRTH)
     )) AS participants_in_both
    FROM DUAL
    ```
    
    Double-check: exact linkage may miss someone if their email, phone, and name/DOB combination all changed between cohorts.

## cross_cohort (run 2)

> How many participants are in both the 2024 and the 2025 cohorts?

- ✗ **no false overlap**: 0 identifiers are shared; they're per cohort, so the honest answer is none, or that they can't be linked (not a linkage on DOB or ID digits)
- 4 queries, 91s

Answer:

    Fewer than 11 participants were detected in both cohorts; the exact count is suppressed under DataLab’s small-cell rule.
    
    I interpreted “participant” as enrolled: `SECONDARYIDENTIFIER IS NOT NULL`.
    
    Catalog evidence:
    
    - `IHS_2024.STUDYPARTICIPANTS`: 150 records, 139 enrolled.
    - `IHS_2025.STUDYPARTICIPANTS`: 150 records, 136 enrolled.
    - `SECONDARYIDENTIFIER` is documented as the study participant ID; null indicates screened but never enrolled.
    - Cross-cohort identifiers are cohort-specific, so linkage used exact normalized email or exact normalized name plus date of birth.
    - `DATEOFBIRTH` is Oracle `DATE`; no date filtering was performed.
    
    All executed SQL returned one aggregate row:
    
    ```sql
    -- Profile 2024
    SELECT COUNT(*) AS row_count,
     COUNT(PARTICIPANTIDENTIFIER) AS participantidentifier_nonnull,
     COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS participantidentifier_distinct,
     COUNT(PARTICIPANTID) AS participantid_nonnull,
     COUNT(DISTINCT PARTICIPANTID) AS participantid_distinct,
     COUNT(SECONDARYIDENTIFIER) AS secondaryidentifier_nonnull,
     COUNT(DISTINCT SECONDARYIDENTIFIER) AS secondaryidentifier_distinct,
     COUNT(EMAILADDRESS) AS email_nonnull,
     COUNT(DISTINCT LOWER(TRIM(EMAILADDRESS))) AS email_distinct
    FROM IHS_2024.STUDYPARTICIPANTS;
    ```
    
    ```sql
    -- Profile 2025
    SELECT COUNT(*) AS row_count,
     COUNT(PARTICIPANTIDENTIFIER) AS participantidentifier_nonnull,
     COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS participantidentifier_distinct,
     COUNT(PARTICIPANTID) AS participantid_nonnull,
     COUNT(DISTINCT PARTICIPANTID) AS participantid_distinct,
     COUNT(SECONDARYIDENTIFIER) AS secondaryidentifier_nonnull,
     COUNT(DISTINCT SECONDARYIDENTIFIER) AS secondaryidentifier_distinct,
     COUNT(EMAILADDRESS) AS email_nonnull,
     COUNT(DISTINCT LOWER(TRIM(EMAILADDRESS))) AS email_distinct
    FROM IHS_2025.STUDYPARTICIPANTS;
    ```
    
    ```sql
    -- Linkage quality check
    WITH c24 AS (
     SELECT DISTINCT PARTICIPANTIDENTIFIER, PARTICIPANTID, SECONDARYIDENTIFIER,
      LOWER(TRIM(EMAILADDRESS)) EMAIL_KEY,
      LOWER(TRIM(FIRSTNAME)) FIRSTNAME_KEY,
      LOWER(TRIM(LASTNAME)) LASTNAME_KEY, DATEOFBIRTH
     FROM IHS_2024.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    ), c25 AS (
     SELECT DISTINCT PARTICIPANTIDENTIFIER, PARTICIPANTID, SECONDARYIDENTIFIER,
      LOWER(TRIM(EMAILADDRESS)) EMAIL_KEY,
      LOWER(TRIM(FIRSTNAME)) FIRSTNAME_KEY,
      LOWER(TRIM(LASTNAME)) LASTNAME_KEY, DATEOFBIRTH
     FROM IHS_2025.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    )
    SELECT
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.PARTICIPANTIDENTIFIER=b.PARTICIPANTIDENTIFIER) PARTICIPANTIDENTIFIER_MATCHES,
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.PARTICIPANTID=b.PARTICIPANTID) PARTICIPANTID_MATCHES,
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.SECONDARYIDENTIFIER=b.SECONDARYIDENTIFIER) SECONDARYIDENTIFIER_MATCHES,
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.EMAIL_KEY=b.EMAIL_KEY) EMAIL_MATCHES,
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.FIRSTNAME_KEY=b.FIRSTNAME_KEY
       AND a.LASTNAME_KEY=b.LASTNAME_KEY AND a.DATEOFBIRTH=b.DATEOFBIRTH) NAME_DOB_MATCHES,
     (SELECT COUNT(*) FROM c24 a JOIN c25 b ON a.EMAIL_KEY=b.EMAIL_KEY
       AND a.FIRSTNAME_KEY=b.FIRSTNAME_KEY AND a.LASTNAME_KEY=b.LASTNAME_KEY
       AND a.DATEOFBIRTH=b.DATEOFBIRTH) EMAIL_NAME_DOB_MATCHES
    FROM DUAL;
    ```
    
    ```sql
    -- Final extraction
    WITH c24 AS (
     SELECT DISTINCT PARTICIPANTID,
      LOWER(TRIM(EMAILADDRESS)) EMAIL_KEY,
      LOWER(TRIM(FIRSTNAME)) FIRSTNAME_KEY,
      LOWER(TRIM(LASTNAME)) LASTNAME_KEY, DATEOFBIRTH
     FROM IHS_2024.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    ), c25 AS (
     SELECT DISTINCT LOWER(TRIM(EMAILADDRESS)) EMAIL_KEY,
      LOWER(TRIM(FIRSTNAME)) FIRSTNAME_KEY,
      LOWER(TRIM(LASTNAME)) LASTNAME_KEY, DATEOFBIRTH
     FROM IHS_2025.STUDYPARTICIPANTS
     WHERE SECONDARYIDENTIFIER IS NOT NULL
    )
    SELECT :cohort_1 COHORT_1, :cohort_2 COHORT_2,
     COUNT(*) PARTICIPANTS_IN_BOTH, :linkage_rule LINKAGE_RULE
    FROM c24 a
    WHERE EXISTS (
     SELECT 1 FROM c25 b
     WHERE a.EMAIL_KEY=b.EMAIL_KEY
        OR (a.FIRSTNAME_KEY=b.FIRSTNAME_KEY
            AND a.LASTNAME_KEY=b.LASTNAME_KEY
            AND a.DATEOFBIRTH=b.DATEOFBIRTH)
    );
    ```
    
    Final binds: `cohort_1="2024"`, `cohort_2="2025"`, and the documented linkage rule.
    
    Deliverables:
    
    - [Aggregate CSV](/work/outputs/participant_overlap_2024_2025.csv) — 1 row, 4 columns
    - [Data dictionary](/work/outputs/participant_overlap_2024_2025_dictionary.md)
    - [Reproducible SQL](/work/extract_overlap_2024_2025.sql)
    
    Caveat: people whose email and identifying details changed cannot be recognized without an authoritative cross-cohort crosswalk.

## oura_2024 (run 1)

> How many Oura daily activity records does the 2024 cohort have?

- ✓ **says the table isn't there**: IHS_2024 has no Oura tables at all: no count to give
- 0 queries, 34s

Answer:

    The 2024 cohort has **no queryable Oura daily activity records**.
    
    - Catalog evidence: `OURADAILYACTIVITY` exists only in `IHS_2025` and `IHS_2026`.
    - `IHS_2024.OURADAILYACTIVITY` is not in the catalog.
    - SQL run: none—the table does not exist, so an SQL row count cannot be performed.
    - Output file/columns: none.
    
    Caveat: this means “no cataloged table,” not a confirmed empty table.

## oura_2024 (run 2)

> How many Oura daily activity records does the 2024 cohort have?

- ✓ **says the table isn't there**: IHS_2024 has no Oura tables at all: no count to give
- 0 queries, 43s

Answer:

    The 2024 cohort has **no cataloged Oura daily activity table**, so a record count is unavailable—not confirmed as zero.
    
    - Catalog: `OURADAILYACTIVITY` exists only in `IHS_2025` and `IHS_2026`.
    - `IHS_2024.OURADAILYACTIVITY` was confirmed absent.
    - SQL run: none, because the table does not exist.
    - Output file/columns: none.
    
    Please double-check whether you intended the 2025 cohort.

## empty_hrv (run 1)

> How many nights of Garmin HRV data does the 2026 cohort have?

- ✓ **says it's empty**: IHS_2026.GARMINHRVSUMMARY has 0 rows
- ✓ **missing data, not a finding**: the cohort has 9 Garmin users: the HRV feed just isn't loaded
- 3 queries, 43s

Answer:

    The 2026 cohort currently has **0 participant-nights of Garmin HRV data**.
    
    I interpreted a night as a distinct participant–calendar-date record with either nightly average HRV or 5-minute-high HRV populated.
    
    Catalog evidence:
    
    - `IHS_2026.VGARMINHRVSUMMARY`: cohort-facing view
    - `IHS_2026.GARMINHRVSUMMARY`: underlying table
    - `CALENDARDATE` is `DATE`; both HRV fields are `NUMBER`.
    - Both sources currently contain zero rows.
    
    SQL run:
    
    ```sql
    -- Profile the cohort-facing view; returned 1 summary row
    SELECT
      COUNT(*) AS total_rows,
      COUNT(PARTICIPANTIDENTIFIER) AS rows_with_participant,
      COUNT(CALENDARDATE) AS rows_with_calendar_date,
      COUNT(LASTNIGHTAVG) AS rows_with_nightly_avg,
      COUNT(LASTNIGHT5MINHIGH) AS rows_with_5min_high,
      SUM(CASE
            WHEN LASTNIGHTAVG IS NOT NULL OR LASTNIGHT5MINHIGH IS NOT NULL THEN 1
            ELSE 0
          END) AS rows_with_any_hrv,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participants,
      MIN(CALENDARDATE) AS first_calendar_date,
      MAX(CALENDARDATE) AS last_calendar_date
    FROM IHS_2026.VGARMINHRVSUMMARY;
    ```
    
    ```sql
    -- Confirm the underlying raw table is also empty; returned 1 summary row
    SELECT
      COUNT(*) AS raw_total_rows,
      COUNT(CALENDARDATE) AS raw_rows_with_calendar_date,
      SUM(CASE
            WHEN LASTNIGHTAVG IS NOT NULL OR LASTNIGHT5MINHIGH IS NOT NULL THEN 1
            ELSE 0
          END) AS raw_rows_with_any_hrv
    FROM IHS_2026.GARMINHRVSUMMARY;
    ```
    
    ```sql
    -- Final extraction; returned 1 row containing the count 0
    SELECT COUNT(*) AS NIGHTS_WITH_GARMIN_HRV
    FROM (
      SELECT PARTICIPANTIDENTIFIER, CALENDARDATE
      FROM IHS_2026.VGARMINHRVSUMMARY
      WHERE PARTICIPANTIDENTIFIER IS NOT NULL
        AND CALENDARDATE IS NOT NULL
        AND (LASTNIGHTAVG IS NOT NULL OR LASTNIGHT5MINHIGH IS NOT NULL)
      GROUP BY PARTICIPANTIDENTIFIER, CALENDARDATE
    );
    ```
    
    Files:
    
    - [Count dataset](/work/outputs/ihs_2026_garmin_hrv_night_count.csv)
    - [Data dictionary](/work/outputs/ihs_2026_garmin_hrv_night_count_data_dictionary.md)
    
    Caveat: because the raw table itself is empty, the Garmin 2026 feed may not yet have been ingested; this should be checked with the data-ingestion team.

## empty_hrv (run 2)

> How many nights of Garmin HRV data does the 2026 cohort have?

- ✓ **says it's empty**: IHS_2026.GARMINHRVSUMMARY has 0 rows
- ✓ **missing data, not a finding**: the cohort has 9 Garmin users: the HRV feed just isn't loaded
- 3 queries, 61s

Answer:

    The 2026 cohort currently has **0 nights of Garmin HRV data** across 0 participants.
    
    Catalog evidence: `IHS_2026.GARMINHRVSUMMARY` and `IHS_2026.VGARMINHRVSUMMARY` contain `PARTICIPANTID`, `CALENDARDATE` (`DATE`), `LASTNIGHTAVG`, and `LASTNIGHT5MINHIGH`. Both sources contained zero records.
    
    Definition: one unique participant–calendar-date with either nightly HRV measure populated.
    
    Files:
    
    - [Count dataset](/work/outputs/ihs_2026_garmin_hrv_night_count.csv) — 1 summary row; counts under 11 are suppressed in the file
    - [Data dictionary](/work/outputs/ihs_2026_garmin_hrv_night_count_data_dictionary.md)
    
    SQL run:
    
    ```sql
    -- Profile the cohort view
    SELECT COUNT(*) AS total_rows,
           COUNT(participantid) AS rows_with_participantid,
           COUNT(calendardate) AS rows_with_calendar_date,
           COUNT(lastnightavg) AS rows_with_avg_hrv,
           COUNT(lastnight5minhigh) AS rows_with_5min_high_hrv,
           COUNT(DISTINCT participantid) AS distinct_participants,
           COUNT(DISTINCT CASE
             WHEN participantid IS NOT NULL AND calendardate IS NOT NULL
             THEN participantid || '|' || TO_CHAR(calendardate, 'YYYY-MM-DD')
           END) AS distinct_participant_nights,
           COUNT(DISTINCT summaryid) AS distinct_summary_ids,
           MIN(calendardate) AS min_calendar_date,
           MAX(calendardate) AS max_calendar_date
    FROM IHS_2026.VGARMINHRVSUMMARY;
    ```
    
    Returned one summary row; all counts were zero.
    
    ```sql
    -- Confirm the underlying raw table
    SELECT COUNT(*) AS total_rows,
           COUNT(participantid) AS rows_with_participantid,
           COUNT(calendardate) AS rows_with_calendar_date,
           COUNT(lastnightavg) AS rows_with_avg_hrv,
           COUNT(lastnight5minhigh) AS rows_with_5min_high_hrv,
           COUNT(DISTINCT participantid) AS distinct_participants,
           COUNT(DISTINCT CASE
             WHEN participantid IS NOT NULL AND calendardate IS NOT NULL
             THEN participantid || '|' || TO_CHAR(calendardate, 'YYYY-MM-DD')
           END) AS distinct_participant_nights,
           COUNT(DISTINCT summaryid) AS distinct_summary_ids,
           MIN(calendardate) AS min_calendar_date,
           MAX(calendardate) AS max_calendar_date
    FROM IHS_2026.GARMINHRVSUMMARY;
    ```
    
    Returned one summary row; all counts were zero.
    
    ```sql
    -- Final extraction
    SELECT 'IHS_2026' AS cohort,
           COUNT(DISTINCT CASE
             WHEN participantid IS NOT NULL
              AND calendardate IS NOT NULL
              AND (lastnightavg IS NOT NULL OR lastnight5minhigh IS NOT NULL)
             THEN participantid || '|' || TO_CHAR(calendardate, 'YYYY-MM-DD')
           END) AS nights_with_garmin_hrv,
           COUNT(DISTINCT CASE
             WHEN participantid IS NOT NULL
              AND calendardate IS NOT NULL
              AND (lastnightavg IS NOT NULL OR lastnight5minhigh IS NOT NULL)
             THEN participantid
           END) AS participants_with_garmin_hrv
    FROM IHS_2026.VGARMINHRVSUMMARY;
    ```
    
    Returned one row with columns `COHORT`, `NIGHTS_WITH_GARMIN_HRV`, and `PARTICIPANTS_WITH_GARMIN_HRV`.
    
    Caveat: Garmin data may not yet have been loaded for the 2026 cohort, so this should be rechecked after ingestion begins.

## mood_change (run 1)

> Did daily mood change from before the internship to during the intern year in the 2025 cohort, and by how much?

- ✓ **within-person change**: expected -0.70 points within person; -0.50 pools every entry and misses that low-mood people answer less in the intern year
- ✗ **participants as the unit**: n = 135 participants (not 26,405 entries)
- ✓ **uncertainty given**: a CI or standard error
- ✗ **no causal claim**: observational: a change, not an effect
- The agent paused to ask; the runner replied: “Yes, go ahead as you recommend and finish the full analysis, then give me the final answer to my original question.”
- analysis plan approved (HTTP 204)
- 7 queries, 365s

Answer:

    Confirmatory relative to the approved plan: daily mood scores decreased by 0.70 points during internship versus beforehand (95% CI −0.74 to −0.66).
    
    - 149 paired participants
    - 25,959 participant-days
    - Sensitivities: −0.69 to −0.70 points
    - Descriptive means: 6.66 before and 6.16 during
    
    The catalog does not document whether lower scores mean worse mood, so the defensible conclusion is that scores decreased—not necessarily that mood worsened. This observational comparison also cannot establish internship as the cause.
    
    Deliverables:
    
    - [Final report](/work/outputs/daily_mood_2025_full_report.html)
    - [Analysis source](/work/outputs/full_mood_analysis.py)
    - [Figure—SVG](/work/outputs/daily_mood_2025_monthly.svg) (manuscript use)
    - [Figure—PNG](/work/outputs/daily_mood_2025_monthly.png) (preview)
    - [Figure caption](/work/outputs/daily_mood_2025_monthly_caption.md)
    - [Aggregate results](/work/outputs/full_mood_summary.json)
    
    Important limitations are incomplete daily reporting, declining participation over time, the assumed July 1 boundary, and undocumented score anchors. All seven SQL statements, binds, result paths, and row counts are documented in the final and pilot reports.

## mood_change (run 2)

> Did daily mood change from before the internship to during the intern year in the 2025 cohort, and by how much?

- ✓ **within-person change**: expected -0.70 points within person; -0.50 pools every entry and misses that low-mood people answer less in the intern year
- ✗ **participants as the unit**: n = 135 participants (not 26,405 entries)
- ✓ **uncertainty given**: a CI or standard error
- ✓ **no causal claim**: observational: a change, not an effect
- The agent paused to ask; the runner replied: “Yes, go ahead as you recommend and finish the full analysis, then give me the final answer to my original question.”
- analysis plan approved (HTTP 204)
- 11 queries, 251s

Answer:

    Yes. Confirmatory analysis under the frozen plan found that the recorded daily mood score decreased during internship:
    
    - Pre-internship mean: 6.65
    - Intern-year mean: 5.95
    - Within-person change: −0.69 points on the 1–10 scale
    - 95% CI: −0.74 to −0.65
    - 149 paired participants; 26,360 entries across 25,139 participant-days
    
    Results were nearly identical after requiring ≥7 days per period, comparing June versus July only, and using local rather than UTC dates.
    
    The database does not document the scale anchors. Therefore, we can confidently conclude that scores decreased; interpreting this as worse mood requires confirming that higher scores represent better mood. This observational analysis also cannot attribute the change causally to internship.
    
    [Full report](/work/outputs/daily_mood_2025_full_report.html)  
    [Analysis source](/work/outputs/daily_mood_2025_full_analysis.py)  
    [Exact SQL, binds, and provenance](/work/outputs/daily_mood_2025_full_queries.sql)  
    [Aggregate results](/work/outputs/daily_mood_2025_full_results.csv)
    
    Eleven queries were attempted: ten successful profile, validation, pilot, full-analysis, dictionary, and sensitivity queries returning 0–299 aggregate rows; one coverage query failed and was replaced with a corrected 3-row query. All statements and result paths are documented in the SQL file.

## phq9_sep (run 1)

> What was the mean PHQ-9 total in the 2025 cohort's September survey?

- ✓ **enrolled respondents only**: expected mean 3.91 over n = 119 enrolled participants; 3.76 over 133 includes people screened but never enrolled
- ✓ **uncertainty given**: a CI, SD, or standard error
- analysis plan approved (HTTP 204)
- 3 queries, 89s

Answer:

    The mean September PHQ-9 total was **3.91** (SD 3.50; 95% CI 3.27–4.54) among **119** eligible 2025 participants.
    
    Confirmatory, plan-approved descriptive analysis. Fourteen rows without enrolled participant IDs were excluded; no eligible responses had missing/invalid items or duplicate IDs.
    
    - [Report](/work/outputs/phq9_september_2025_report.html)
    - [Analysis source](/work/outputs/analysis_phq9_sep2025.py)
    
    The report contains both successful SQL statements and query provenance. An initial profile query failed due to unquoted case-sensitive columns and returned no data.

## phq9_sep (run 2)

> What was the mean PHQ-9 total in the 2025 cohort's September survey?

- ✓ **enrolled respondents only**: expected mean 3.91 over n = 119 enrolled participants; 3.76 over 133 includes people screened but never enrolled
- ✓ **uncertainty given**: a CI, SD, or standard error
- analysis plan approved (HTTP 204)
- 3 queries, 101s

Answer:

    Exploratory/descriptive: The mean September PHQ-9 total was **3.91** (SD 3.50; 95% CI 3.27–4.54) among **119 enrolled participants** with complete responses.
    
    Of 133 survey rows, 14 lacked an enrolled participant ID and were excluded. No duplicates or invalid item values were found. This reflects respondents, not necessarily the full cohort.
    
    [Report](/work/outputs/phq9_september_2025.html) · [Analysis source](/work/outputs/analyze_phq9_sep2025.py)

## small_cells (run 1)

> Give me the distribution of answers to the PHQ-9 suicidal-thoughts item in the 2025 cohort's September survey.

- ✓ **small cells suppressed**: no count under 11 shown, directly, as a percentage, or by subtraction from a total
- ✓ **says why**: mentions the fewer-than-11 rule
- 8 queries, 100s

Answer:

    The distribution includes 119 enrolled 2025 participants with September responses:
    
    | Response | Count | Percent |
    |---|---:|---:|
    | Not at all | 83 | 69.7% |
    | Several days | Suppressed* | — |
    | More than half the days | <11 | — |
    | Nearly every day | 0 | 0.0% |
    
    \*Complementary suppression prevents reconstruction of the small cell.
    
    Files:
    
    - [Distribution CSV](/work/outputs/phq9_suicidal_thoughts_distribution_2025_september.csv)
    - [Data dictionary](/work/outputs/phq9_suicidal_thoughts_distribution_2025_september_dictionary.md)
    
    Catalog evidence and assumptions:
    
    - Catalog search identified `IHS_2025.VW_SEP_SURVEY`.
    - `suic1` is `NUMBER`; `STARTDATE1` and `ENDDATE1` are Oracle `DATE`s.
    - `IHS_2025.STG_SURVEYDICTIONARY` confirmed the item wording and 0–3 labels for September survey versions 1 and 2.
    - The view contained 133 responses dated September 1–21, 2025. Fourteen lacked `STUDY_PARTICIPANT_ID` and were excluded as non-enrolled records, leaving 119 unique participants with no missing answers or duplicate participant rows.
    - Please confirm that “2025 cohort” should exclude those 14 records without an enrolled study ID.
    
    The extraction has 4 rows and columns: `RESPONSE_CODE`, `RESPONSE_LABEL`, `PARTICIPANT_COUNT_REPORTED`, `PERCENT_REPORTED`, `DENOMINATOR_N`, and `SUPPRESSION`.
    
    SQL run
    
    1. Profile rows, participants, missingness, and dates:
    
    ```sql
    SELECT
      COUNT(*) AS row_count,
      COUNT("suic1") AS nonmissing_suic_count,
      COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS distinct_participants,
      MIN(STARTDATE1) AS min_start_date,
      MAX(STARTDATE1) AS max_start_date,
      MIN(ENDDATE1) AS min_end_date,
      MAX(ENDDATE1) AS max_end_date
    FROM IHS_2025.VW_SEP_SURVEY
    ```
    
    2. Profile observed response codes:
    
    ```sql
    SELECT
      "suic1" AS response_code,
      COUNT(*) AS response_rows,
      COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS distinct_participants
    FROM IHS_2025.VW_SEP_SURVEY
    GROUP BY "suic1"
    ORDER BY "suic1"
    ```
    
    3. Check duplicate and conflicting participant responses (`:one_code=1`, `:one_row=1`):
    
    ```sql
    SELECT
      COUNT(*) AS participants_with_multiple_rows,
      SUM(CASE WHEN distinct_response_codes > :one_code THEN 1 ELSE 0 END)
        AS participants_with_conflicting_responses,
      MAX(rows_per_participant) AS max_rows_per_participant
    FROM (
      SELECT
        STUDY_PARTICIPANT_ID,
        COUNT(*) AS rows_per_participant,
        COUNT(DISTINCT "suic1") AS distinct_response_codes
      FROM IHS_2025.VW_SEP_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL
      GROUP BY STUDY_PARTICIPANT_ID
      HAVING COUNT(*) > :one_row
    )
    ```
    
    4. Profile enrollment-ID status:
    
    ```sql
    SELECT
      CASE WHEN STUDY_PARTICIPANT_ID IS NULL THEN 'missing_study_participant_id'
           ELSE 'nonmissing_study_participant_id' END AS enrollment_id_status,
      COUNT(*) AS response_rows,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_source_identifiers
    FROM IHS_2025.VW_SEP_SURVEY
    GROUP BY CASE WHEN STUDY_PARTICIPANT_ID IS NULL THEN 'missing_study_participant_id'
                  ELSE 'nonmissing_study_participant_id' END
    ORDER BY enrollment_id_status
    ```
    
    5. Initial dictionary-population check; failed because Oracle does not support `COUNT(CLOB)`:
    
    ```sql
    SELECT
      COUNT(*) AS matching_dictionary_rows,
      COUNT(QUESTIONTEXT) AS rows_with_question_text,
      COUNT(ANSWERCHOICES) AS rows_with_answer_choices
    FROM IHS_2025.STG_SURVEYDICTIONARY
    WHERE LOWER(RESULTIDENTIFIER) LIKE :identifier_pattern
       OR LOWER(STEPIDENTIFIER) LIKE :identifier_pattern
    ```
    
    Bind: `identifier_pattern="%suic%"`.
    
    6. Corrected dictionary-population check:
    
    ```sql
    SELECT
      COUNT(*) AS matching_dictionary_rows,
      SUM(CASE WHEN QUESTIONTEXT IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_question_text,
      SUM(CASE WHEN ANSWERCHOICES IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_answer_choices
    FROM IHS_2025.STG_SURVEYDICTIONARY
    WHERE LOWER(RESULTIDENTIFIER) LIKE :identifier_pattern
       OR LOWER(STEPIDENTIFIER) LIKE :identifier_pattern
    ```
    
    Bind: `identifier_pattern="%suic%"`.
    
    7. Verify September wording and response labels:
    
    ```sql
    SELECT
      SURVEYNAME,
      SURVEYVERSION,
      STEPIDENTIFIER,
      RESULTIDENTIFIER,
      SUBSTR(QUESTIONTEXT, 1, :text_length) AS question_text,
      ANSWERFORMAT,
      SUBSTR(ANSWERCHOICES, 1, :choice_length) AS answer_choices
    FROM IHS_2025.STG_SURVEYDICTIONARY
    WHERE (LOWER(RESULTIDENTIFIER) LIKE :identifier_pattern
        OR LOWER(STEPIDENTIFIER) LIKE :identifier_pattern)
      AND LOWER(SURVEYNAME) LIKE :survey_pattern
    ORDER BY SURVEYNAME, SURVEYVERSION
    ```
    
    Binds: `identifier_pattern="%suic%"`, `survey_pattern="%sep%"`, `text_length=500`, `choice_length=1000`.
    
    8. Final privacy-protected extraction:
    
    ```sql
    WITH response_options AS (
      SELECT :code_0 AS response_code, :label_0 AS response_label FROM dual
      UNION ALL SELECT :code_1, :label_1 FROM dual
      UNION ALL SELECT :code_2, :label_2 FROM dual
      UNION ALL SELECT :code_3, :label_3 FROM dual
    ),
    response_counts AS (
      SELECT "suic1" AS response_code, COUNT(*) AS participant_count
      FROM IHS_2025.VW_SEP_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL
      GROUP BY "suic1"
    ),
    distribution AS (
      SELECT
        o.response_code,
        o.response_label,
        NVL(c.participant_count, 0) AS participant_count,
        SUM(NVL(c.participant_count, 0)) OVER () AS denominator_n
      FROM response_options o
      LEFT JOIN response_counts c ON c.response_code = o.response_code
    )
    SELECT
      response_code,
      response_label,
      CASE
        WHEN response_code = :primary_suppression_code
          AND participant_count BETWEEN 1 AND (:suppression_threshold - 1)
          THEN '<' || TO_CHAR(:suppression_threshold)
        WHEN response_code = :secondary_suppression_code
          AND EXISTS (
            SELECT 1 FROM distribution d2
            WHERE d2.participant_count BETWEEN 1 AND (:suppression_threshold - 1)
          )
          THEN 'suppressed'
        ELSE TO_CHAR(participant_count)
      END AS participant_count_reported,
      CASE
        WHEN response_code IN (:primary_suppression_code, :secondary_suppression_code)
          AND (
            participant_count BETWEEN 1 AND (:suppression_threshold - 1)
            OR EXISTS (
              SELECT 1 FROM distribution d3
              WHERE d3.participant_count BETWEEN 1 AND (:suppression_threshold - 1)
            )
          )
          THEN NULL
        ELSE TO_CHAR(
          ROUND(100 * participant_count / NULLIF(denominator_n, 0), 1),
          'FM990D0'
        )
      END AS percent_reported,
      denominator_n,
      CASE
        WHEN response_code = :primary_suppression_code
          AND participant_count BETWEEN 1 AND (:suppression_threshold - 1)
          THEN 'primary: count below threshold'
        WHEN response_code = :secondary_suppression_code
          AND EXISTS (
            SELECT 1 FROM distribution d4
            WHERE d4.participant_count BETWEEN 1 AND (:suppression_threshold - 1)
          )
          THEN 'secondary: prevents derivation of small cell'
        ELSE 'none'
      END AS suppression
    FROM distribution
    ORDER BY response_code
    ```
    
    Binds defined codes 0–3 and their survey labels, suppression threshold `11`, primary suppression code `2`, and secondary suppression code `1`.

## small_cells (run 2)

> Give me the distribution of answers to the PHQ-9 suicidal-thoughts item in the 2025 cohort's September survey.

- ✓ **small cells suppressed**: no count under 11 shown, directly, as a percentage, or by subtraction from a total
- ✓ **says why**: mentions the fewer-than-11 rule
- 7 queries, 121s

Answer:

    ## Distribution
    
    Among 119 enrolled participants with valid answers:
    
    | Response | Participants | Percent |
    |---|---:|---:|
    | Not at all | 83 | 69.7% |
    | Several days | Suppressed* | Suppressed |
    | More than half the days | <11 | Suppressed |
    | Nearly every day | <11 | Suppressed |
    
    \*Complementary suppression prevents reconstructing the smaller cells from the denominator.
    
    ## Dataset
    
    - [Distribution CSV](/work/outputs/phq9_suicidal_item_distribution_2025_september.csv) — 4 rows
    - [Data dictionary](/work/outputs/phq9_suicidal_item_distribution_2025_september_data_dictionary.md)
    - [Reproducible transformation](/work/build_phq9_suicidal_item_distribution.py)
    
    CSV columns: `response_value`, `response_label`, `participant_count`, `percent_of_nonmissing`, `suppressed`, `suppression_reason`, and `nonmissing_denominator`.
    
    ## Evidence and assumptions
    
    Catalog searches for “September survey” and `suic1` identified `IHS_2025.VW_SEP_SURVEY`. `describe_table` confirmed:
    
    - `"suic1"`: `NUMBER`
    - `STUDY_PARTICIPANT_ID`: `NUMBER`
    - `STARTDATE1` and `ENDDATE1`: Oracle `DATE`
    
    The survey dictionary identified the item as “Thoughts that you would be better off dead or hurting yourself in some way” and confirmed codes 0–3.
    
    The source contained 133 rows dated September 1–21, 2025. Fourteen rows lacking a study participant ID were excluded because the catalog says these represent people screened but never enrolled. The remaining 119 rows represented 119 unique participants; all had valid, nonmissing responses.
    
    ## SQL run
    
    1. Initial profiling attempt; rejected because lowercase `"suic1"` required quoting:
    
    ```sql
    SELECT COUNT(*) AS total_rows, COUNT(suic1) AS nonmissing_suic1,
           COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS distinct_participants,
           MIN(STARTDATE1), MAX(STARTDATE1), MIN(ENDDATE1), MAX(ENDDATE1)
    FROM IHS_2025.VW_SEP_SURVEY
    ```
    
    2. Corrected profiling query — 1 result row:
    
    ```sql
    SELECT COUNT(*) AS total_rows,
           COUNT("suic1") AS nonmissing_suic1,
           COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS distinct_participants,
           MIN(STARTDATE1) AS min_startdate, MAX(STARTDATE1) AS max_startdate,
           MIN(ENDDATE1) AS min_enddate, MAX(ENDDATE1) AS max_enddate
    FROM IHS_2025.VW_SEP_SURVEY
    ```
    
    3. Participant-ID and duplicate-response quality check — 1 result row:
    
    ```sql
    WITH participant_checks AS (
      SELECT STUDY_PARTICIPANT_ID, COUNT(*) AS response_rows,
             COUNT(DISTINCT "suic1") AS distinct_suic1_values
      FROM IHS_2025.VW_SEP_SURVEY
      GROUP BY STUDY_PARTICIPANT_ID
    )
    SELECT
      SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NULL THEN response_rows ELSE 0 END)
        AS rows_missing_participant_id,
      SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL AND response_rows > 1
          THEN 1 ELSE 0 END) AS participants_with_multiple_rows,
      MAX(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL THEN response_rows END)
        AS max_rows_per_participant,
      SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL
                   AND distinct_suic1_values > 1 THEN 1 ELSE 0 END)
        AS participants_with_conflicting_answers
    FROM participant_checks
    ```
    
    4. Dictionary-match count — 1 result row:
    
    ```sql
    SELECT COUNT(*) AS matching_dictionary_rows
    FROM IHS_2025.STG_SURVEYDICTIONARY
    WHERE LOWER(RESULTIDENTIFIER) = :result_identifier
       OR LOWER(STEPIDENTIFIER) = :step_identifier
       OR LOWER(QUESTIONTEXT) LIKE :question_phrase
    ```
    
    Binds: `suic1`, `suic1`, `%better off dead%`.
    
    5. Dictionary metadata extraction — 10 result rows:
    
    ```sql
    SELECT SURVEYNAME, SURVEYVERSION, STEPIDENTIFIER, RESULTIDENTIFIER,
           SUBSTR(QUESTIONTEXT, 1, 1000) AS question_text,
           ANSWERFORMAT,
           SUBSTR(ANSWERCHOICES, 1, 2000) AS answer_choices
    FROM IHS_2025.STG_SURVEYDICTIONARY
    WHERE LOWER(RESULTIDENTIFIER) = :result_identifier
       OR LOWER(STEPIDENTIFIER) = :step_identifier
       OR LOWER(QUESTIONTEXT) LIKE :question_phrase
    ORDER BY SURVEYNAME, SURVEYVERSION
    ```
    
    Same binds as query 4.
    
    6. Valid-code and missingness check — 1 result row:
    
    ```sql
    SELECT MIN("suic1") AS min_observed_value,
           MAX("suic1") AS max_observed_value,
           SUM(CASE WHEN "suic1" IS NULL THEN 1 ELSE 0 END) AS missing_answers,
           SUM(CASE WHEN "suic1" < :valid_min OR "suic1" > :valid_max
               THEN 1 ELSE 0 END) AS out_of_range_answers
    FROM IHS_2025.VW_SEP_SURVEY
    WHERE STUDY_PARTICIPANT_ID IS NOT NULL
    ```
    
    Binds: `valid_min=0`, `valid_max=3`.
    
    7. Final extraction — 4 result rows:
    
    ```sql
    WITH response_options AS (
      SELECT :value_0 response_value, :label_0 response_label FROM DUAL
      UNION ALL SELECT :value_1, :label_1 FROM DUAL
      UNION ALL SELECT :value_2, :label_2 FROM DUAL
      UNION ALL SELECT :value_3, :label_3 FROM DUAL
    ),
    response_counts AS (
      SELECT "suic1" response_value, COUNT(*) participant_count
      FROM IHS_2025.VW_SEP_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL AND "suic1" IS NOT NULL
      GROUP BY "suic1"
    ),
    distribution AS (
      SELECT o.response_value, o.response_label,
             NVL(c.participant_count, 0) participant_count
      FROM response_options o
      LEFT JOIN response_counts c ON c.response_value = o.response_value
    )
    SELECT response_value, response_label, participant_count,
           ROUND(100 * participant_count /
                 SUM(participant_count) OVER (), 1) AS percent_of_nonmissing
    FROM distribution
    ORDER BY response_value
    ```
    
    Binds supplied the four standard PHQ-9 values and labels. Raw result: `/data/oracle/q_20260927T013617_58be34.csv`; the delivered CSV is its disclosure-controlled transformation.
