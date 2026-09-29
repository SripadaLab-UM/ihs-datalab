# Evaluation run 2026-09-29T03:44:03+00:00

Commit `0a39aba4181c`, effort medium, model gpt-5.5, DataLab 0.2.0b8, practice profile (synthetic data only), catalog sha256 `e7889c502cfe`.

**8 of 8 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| person_days | text timestamps: several a day, some after midnight or another date in UTC | 2/2 | ✓ one person-day per participant-date; ✓ day rule stated |
| cohort_coverage | the cohort applied to numerator and denominator: people outside it, a duplicate cohort row | 2/2 | ✓ cohort share; ✓ person-days, no duplicate-join inflation; ✓ day rule stated |
| clob_choices | a CLOB column grouped (TO_CHAR first) | 2/2 | ✓ answer-choice sets and their rows |
| bdate_age | a mixed-case quoted column ("Bdate") | 2/2 | ✓ mean age at baseline; ✓ enrolled n |

## person_days (run 1)

> How many person-days of resting heart rate data are in the 2025 cohort's HealthKit resting heart rate view (VHEALTHKITSAMPLES_RESTINGHEARTRATE)? A person-day is one participant on one day, from any source; include every participant in the view.

- ✓ **one person-day per participant-date**: expected 19,198 (RECORD_DATE's local date), 21,951 (its UTC date) or 21,870 (STARTDATE's date); 25,132 counts each timestamp as a day
- ✓ **day rule stated**: which date makes a day, and in what time zone
- 4 queries, 63s

Answer:

    I understood the request as: count person-days in `IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE`, including every participant present in the view and all sources, with one person-day = one participant on one local `RECORD_DATE` day.
    
    Answer: **19,198 person-days**.
    
    Catalog/schema evidence: live catalog found `IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE` as a 2025 view. `describe_table` showed `PARTICIPANTIDENTIFIER VARCHAR2(15)`, `RECORD_DATE VARCHAR2(26)`, `STARTDATE VARCHAR2(26)`, plus source/device/value fields.
    
    Day rule: local date from `SUBSTR(RECORD_DATE, 1, 10)`. Cohort rule: no external cohort filter; include all participants in the view. Caveat: `/work/kb` was not mounted, so I could not check local knowledge-base notes.
    
    SQL run:
    ```sql
    SELECT
      COUNT(*) AS row_count,
      COUNT(PARTICIPANTIDENTIFIER) AS nonnull_participantidentifier,
      COUNT(RECORD_DATE) AS nonnull_record_date,
      COUNT(STARTDATE) AS nonnull_startdate,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participants,
      MIN(SUBSTR(RECORD_DATE, 1, 10)) AS min_record_local_date,
      MAX(SUBSTR(RECORD_DATE, 1, 10)) AS max_record_local_date
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    ```
    Purpose: profile completeness/date range. Result: 25,132 rows; 66 participants; all rows had participant, `RECORD_DATE`, and `STARTDATE`; local `RECORD_DATE` range 2025-04-02 to 2026-07-01.
    
    ```sql
    SELECT
      COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || RECORD_DATE) AS distinct_participant_timestamps,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || SUBSTR(RECORD_DATE, 1, 10)) AS distinct_participant_local_days,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || STARTDATE) AS distinct_participant_start_timestamps,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || SUBSTR(STARTDATE, 1, 10)) AS distinct_participant_start_local_days
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    WHERE PARTICIPANTIDENTIFIER IS NOT NULL
      AND RECORD_DATE IS NOT NULL
    ```
    Purpose: compare timestamp counts to day counts. Result: 25,132 participant-timestamps vs 19,198 participant-local-days by `RECORD_DATE`.
    
    ```sql
    WITH participant_days AS (
      SELECT PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10) AS local_record_date, COUNT(*) AS rows_per_participant_day
      FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
      WHERE PARTICIPANTIDENTIFIER IS NOT NULL AND RECORD_DATE IS NOT NULL
      GROUP BY PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10)
    )
    SELECT COUNT(*) AS participant_days,
           SUM(CASE WHEN rows_per_participant_day > 1 THEN 1 ELSE 0 END) AS participant_days_with_multiple_rows,
           MAX(rows_per_participant_day) AS max_rows_per_participant_day
    FROM participant_days
    ```
    Purpose: check repeated rows per participant-day. Result: 19,198 participant-days; 5,191 had multiple rows; max 5 rows/day.
    
    ```sql
    SELECT
      COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || SUBSTR(RECORD_DATE, 1, 10)) AS resting_heart_rate_person_days
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    WHERE PARTICIPANTIDENTIFIER IS NOT NULL
      AND RECORD_DATE IS NOT NULL
    ```
    Purpose: final extraction. Row count: 1. Column: `RESTING_HEART_RATE_PERSON_DAYS`.
    
    Files:
    `/work/outputs/2025_healthkit_resting_hr_person_days.csv`
    `/work/outputs/2025_healthkit_resting_hr_person_days_dictionary.md`
    
    Double-check: if you intended HealthKit `STARTDATE` rather than `RECORD_DATE` as the day anchor, that count is different: 21,870 participant-days.

## person_days (run 2)

> How many person-days of resting heart rate data are in the 2025 cohort's HealthKit resting heart rate view (VHEALTHKITSAMPLES_RESTINGHEARTRATE)? A person-day is one participant on one day, from any source; include every participant in the view.

- ✓ **one person-day per participant-date**: expected 19,198 (RECORD_DATE's local date), 21,951 (its UTC date) or 21,870 (STARTDATE's date); 25,132 counts each timestamp as a day
- ✓ **day rule stated**: which date makes a day, and in what time zone
- 4 queries, 87s

Answer:

    Using the 2025 HealthKit resting heart rate view, I count **19,198 person-days**.
    
    I understood the request as: include every participant appearing in `IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE`; define a person-day as one `PARTICIPANTIDENTIFIER` on one local `RECORD_DATE` day; collapse across all sources.
    
    Catalog evidence: `search_catalog` found `IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE` as a view. `describe_table` confirmed `PARTICIPANTIDENTIFIER`, `RECORD_DATE`, `STARTDATE`, `VALUE`, and source fields exist; `RECORD_DATE` is `VARCHAR2(26)`, so I used `SUBSTR(RECORD_DATE, 1, 10)` as the local day rule. The KB was not mounted at `/work/kb`, so I relied on live catalog metadata.
    
    Files:
    `/work/outputs/ihs_2025_healthkit_resting_hr_person_days.csv`
    `/work/outputs/ihs_2025_healthkit_resting_hr_person_days_dictionary.md`
    
    SQL I ran:
    
    ```sql
    SELECT COUNT(*) AS row_count, COUNT(PARTICIPANTIDENTIFIER) AS rows_with_participant,
           COUNT(RECORD_DATE) AS rows_with_record_date, COUNT(STARTDATE) AS rows_with_startdate,
           COUNT(VALUE) AS rows_with_value
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    ```
    Purpose: missingness/profile check. Result row count: 1 summary row; all 25,132 rows had participant, record date, start date, and value.
    
    ```sql
    SELECT COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS participants,
           MIN(SUBSTR(RECORD_DATE, 1, 10)) AS min_local_record_day,
           MAX(SUBSTR(RECORD_DATE, 1, 10)) AS max_local_record_day,
           COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || RECORD_DATE) AS participant_timestamps,
           COUNT(DISTINCT PARTICIPANTIDENTIFIER || '|' || SUBSTR(RECORD_DATE, 1, 10)) AS participant_local_days
    FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
    WHERE PARTICIPANTIDENTIFIER IS NOT NULL AND RECORD_DATE IS NOT NULL
    ```
    Purpose: timestamp-vs-day correctness check. Result row count: 1 summary row; 66 participants, date range 2025-04-02 to 2026-07-01, 25,132 participant-timestamps versus 19,198 participant-days.
    
    ```sql
    WITH participant_days AS (
      SELECT PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10) AS local_record_day,
             COUNT(*) AS rows_per_participant_day,
             COUNT(DISTINCT SOURCEIDENTIFIER) AS source_identifiers_per_day,
             COUNT(DISTINCT SOURCENAME) AS source_names_per_day
      FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
      WHERE PARTICIPANTIDENTIFIER IS NOT NULL AND RECORD_DATE IS NOT NULL
      GROUP BY PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10)
    )
    SELECT COUNT(*) AS participant_local_days,
           SUM(CASE WHEN rows_per_participant_day > 1 THEN 1 ELSE 0 END) AS participant_days_with_multiple_rows,
           MAX(rows_per_participant_day) AS max_rows_per_participant_day,
           SUM(CASE WHEN source_identifiers_per_day > 1 OR source_names_per_day > 1 THEN 1 ELSE 0 END) AS participant_days_with_multiple_sources
    FROM participant_days
    ```
    Purpose: duplicate/source collapse check. Result row count: 1 summary row; multiple rows per participant-day are present, so row counts would overcount person-days.
    
    ```sql
    WITH person_days AS (
      SELECT PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10) AS local_record_day
      FROM IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE
      WHERE PARTICIPANTIDENTIFIER IS NOT NULL AND RECORD_DATE IS NOT NULL
      GROUP BY PARTICIPANTIDENTIFIER, SUBSTR(RECORD_DATE, 1, 10)
    )
    SELECT COUNT(*) AS person_days
    FROM person_days
    ```
    Purpose: final count. Result row count: 1 row, column `PERSON_DAYS`, value **19,198**.
    
    Caveat to double-check: I used local `RECORD_DATE` day, not UTC day or `STARTDATE`. I also suggested a KB update noting that this view can have multiple rows/sources per participant-day, so person-day analyses should collapse by participant and local date.

## cohort_coverage (run 1)

> Take the 2025 cohort to be the enrolled participants in IHS_2025.VW_IHS_PARTICIPANT_SUMMARY (those with a STUDY_PARTICIPANT_ID). What share of that cohort has any HealthKit resting heart rate data, and how many person-days of it do they contribute in total? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 7 queries, 129s

Answer:

    Exploratory bottom line: using the enrolled 2025 cohort as deduplicated `STUDY_PARTICIPANT_ID`-nonmissing participants in `IHS_2025.VW_IHS_PARTICIPANT_SUMMARY`, **58 of 136 participants have HealthKit resting heart rate data: 42.6%**. They contribute **16,906 participant-local-days** in total.
    
    I counted person-days as `PARTICIPANTIDENTIFIER` plus the **local calendar date of `RECORD_DATE`** from `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE`, requiring non-missing `RECORD_DATE` and `VALUE`. The participant summary had 137 enrolled rows but 136 distinct participants, so I used the deduplicated participant denominator. The HealthKit table had multiple samples on some days, so the person-day count is lower than the sample-row count.
    
    I saved the static report at `/work/outputs/healthkit_rhr_2025_coverage.html` and the reproducible script at `/work/scripts/healthkit_rhr_2025_coverage.py`. I also suggested a knowledge-base update noting that the 2025 participant summary should be deduplicated before cohort denominators.

## cohort_coverage (run 2)

> Take the 2025 cohort to be the enrolled participants in IHS_2025.VW_IHS_PARTICIPANT_SUMMARY (those with a STUDY_PARTICIPANT_ID). What share of that cohort has any HealthKit resting heart rate data, and how many person-days of it do they contribute in total? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 6 queries, 142s

Answer:

    Exploratory bottom line: in the 2025 enrolled cohort, **58 of 136 participants** had any HealthKit resting heart rate data, so the cohort share is **42.6%**. Using local calendar day from `RECORD_DATE` as the day rule, they contributed **16,906 person-days** in total.
    
    I used `IHS_2025.VW_IHS_PARTICIPANT_SUMMARY` for the enrolled cohort and `IHS_2025.VHEALTHKITSAMPLES_RESTINGHEARTRATE` for resting heart rate, joined by `PARTICIPANTIDENTIFIER`. The cohort was deduplicated before joining. As a timestamp sensitivity, using local `STARTDATE` instead would give **19,297 person-days**, so downstream analyses should choose the date field deliberately.
    
    I wrote the report to `/work/outputs/healthkit_rhr_coverage_2025.html` and the reproducible script to `/work/scripts/healthkit_rhr_coverage_2025.py`. I also suggested a knowledge-base update noting that the 2025 participant summary should be deduplicated before joins.

## clob_choices (run 1)

> In the 2025 cohort's survey dictionary, what distinct sets of answer choices are there, and how many dictionary rows use each?

- ✓ **answer-choice sets and their rows**: two sets: the PHQ-9 choices on 90 rows and the substance-use choices on 20 (of 110)
- 2 queries, 55s

Answer:

    I understood the request as: in the 2025 cohort survey dictionary, group exact `ANSWERCHOICES` sets and count how many dictionary rows use each.
    
    Result: 2 distinct answer-choice sets across 110 dictionary rows.
    
    | Answer choices | Dictionary rows |
    |---|---:|
    | `0=Not at all|1=Several days|2=More than half the days|3=Nearly every day` | 90 |
    | `0=Never|1=Once or Twice|2=Monthly|3=Weekly|4=Daily or Almost Daily` | 20 |
    
    Files:
    - Dataset: `/work/outputs/ihs_2025_survey_dictionary_answer_choice_sets.csv`
    - Data dictionary: `/work/outputs/ihs_2025_survey_dictionary_answer_choice_sets_dictionary.md`
    
    Catalog evidence: `search_catalog` identified `IHS_2025.STG_SURVEYDICTIONARY` with `ANSWERCHOICES`, `QUESTIONTEXT`, `ANSWERFORMAT`, and survey metadata columns. `describe_table` confirmed `ANSWERCHOICES` is a `CLOB`, so I converted it with `TO_CHAR(SUBSTR(..., 1, 1000))` before grouping.
    
    SQL run:
    ```sql
    SELECT COUNT(*) AS total_rows,
           COUNT(LENGTH(ANSWERCHOICES)) AS rows_with_answerchoices,
           SUM(CASE WHEN ANSWERCHOICES IS NULL THEN 1 ELSE 0 END) AS rows_without_answerchoices,
           MAX(LENGTH(ANSWERCHOICES)) AS max_answerchoices_length,
           MIN(LENGTH(ANSWERCHOICES)) AS min_answerchoices_length
    FROM IHS_2025.STG_SURVEYDICTIONARY
    ```
    Purpose: profile source rows and confirm conversion would not truncate. Row count: 1 summary row.
    
    ```sql
    SELECT TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000)) AS answer_choices,
           COUNT(*) AS dictionary_rows
    FROM IHS_2025.STG_SURVEYDICTIONARY
    GROUP BY TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000))
    ORDER BY COUNT(*) DESC, TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000))
    ```
    Purpose: final extraction. Row count: 2 rows.
    
    Assumptions and caveats: this treats answer-choice sets as exact stored strings, with no relabeling or normalization. This is a dictionary-row count, not participant responses. The local knowledge base was not available in `/work/kb`, so I relied on the live catalog/schema checks.

## clob_choices (run 2)

> In the 2025 cohort's survey dictionary, what distinct sets of answer choices are there, and how many dictionary rows use each?

- ✓ **answer-choice sets and their rows**: two sets: the PHQ-9 choices on 90 rows and the substance-use choices on 20 (of 110)
- 3 queries, 61s

Answer:

    **Answer**
    
    I understood the request as: in `IHS_2025.STG_SURVEYDICTIONARY`, group the survey dictionary rows by distinct `ANSWERCHOICES` values and count how many dictionary rows use each.
    
    Catalog evidence: `search_catalog` found `IHS_2025.STG_SURVEYDICTIONARY` with matching columns `SURVEYNAME`, `SURVEYKEY`, `SURVEYVERSION`, `ANSWERCHOICES`, and `QUESTIONTEXT`. `describe_table` confirmed `ANSWERCHOICES` is a `CLOB`, so I converted it with `TO_CHAR(SUBSTR(...))` for grouping. `/work/kb` was not available in this workspace, so I could not check local KB notes.
    
    | Answer choices | Dictionary rows |
    |---|---:|
    | `0=Not at all|1=Several days|2=More than half the days|3=Nearly every day` | 90 |
    | `0=Never|1=Once or Twice|2=Monthly|3=Weekly|4=Daily or Almost Daily` | 20 |
    
    Files:
    - Dataset: `/work/outputs/ihs_2025_survey_dictionary_answer_choice_sets.csv`
    - Data dictionary: `/work/outputs/ihs_2025_survey_dictionary_answer_choice_sets_dictionary.md`
    
    SQL actually run:
    
    ```sql
    SELECT
      COUNT(*) AS total_rows,
      COUNT(LENGTH(ANSWERCHOICES)) AS rows_with_answerchoices,
      COUNT(*) - COUNT(LENGTH(ANSWERCHOICES)) AS rows_without_answerchoices,
      MAX(LENGTH(ANSWERCHOICES)) AS max_answerchoices_length
    FROM IHS_2025.STG_SURVEYDICTIONARY
    ```
    
    Purpose: profile completeness and length before grouping a CLOB. Row count: 1. Result: 110 total rows, 110 with answer choices, max length 72.
    
    ```sql
    SELECT
      TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000)) AS answer_choices,
      COUNT(*) AS dictionary_rows
    FROM IHS_2025.STG_SURVEYDICTIONARY
    GROUP BY TO_CHAR(SUBSTR(ANSWERCHOICES, 1, 1000))
    ORDER BY dictionary_rows DESC, answer_choices
    ```
    
    Purpose: final extraction of distinct answer-choice sets and row counts. Row count: 2. Columns: `ANSWER_CHOICES`, `DICTIONARY_ROWS`.
    
    Caveat: no participant cohort or person-day rule applies here because this is survey metadata, not participant-level data.

## bdate_age (run 1)

> What was the mean age of the 2025 cohort's enrolled participants when they started the baseline survey, from the date of birth in the baseline survey view (VW_BASELINE_SURVEY)?

- ✓ **mean age at baseline**: expected 29.76 years (29.26 in whole years), from the quoted "Bdate" column and STARTDATE0
- ✓ **enrolled n**: n = 129 enrolled with a date of birth
- 4 queries, 76s

Answer:

    The 2025 cohort’s enrolled participants had a mean age of **29.76 years** when they started the baseline survey (`n = 129`).
    
    I understood “enrolled” as non-null `STUDY_PARTICIPANT_ID`, using the catalog comment that this field is null for people screened but never enrolled. Catalog evidence: `IHS_2025.VW_BASELINE_SURVEY` contains `STARTDATE0` (`DATE`), quoted `"Bdate"` (`DATE`), `STUDY_PARTICIPANT_ID`, and `PARTICIPANTIDENTIFIER`. The enrolled baseline rows were one row per participant, and all enrolled rows had both dates needed for age.
    
    Files:
    - Result CSV: `/work/outputs/ihs_2025_baseline_mean_age.csv`
    - Data dictionary: `/work/outputs/ihs_2025_baseline_mean_age_dictionary.md`
    
    SQL run:
    ```sql
    -- Profile required fields
    SELECT COUNT(*) AS rows_total, COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participantidentifier,
           COUNT(STUDY_PARTICIPANT_ID) AS rows_with_study_participant_id,
           COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS distinct_study_participant_id,
           SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL AND STARTDATE0 IS NOT NULL THEN 1 ELSE 0 END) AS enrolled_rows_with_startdate0,
           SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL AND "Bdate" IS NOT NULL THEN 1 ELSE 0 END) AS enrolled_rows_with_bdate,
           SUM(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL AND STARTDATE0 IS NOT NULL AND "Bdate" IS NOT NULL THEN 1 ELSE 0 END) AS enrolled_rows_complete_for_age
    FROM IHS_2025.VW_BASELINE_SURVEY
    ```
    
    ```sql
    -- Check duplicate enrolled participant rows
    WITH enrolled AS (
      SELECT PARTICIPANTIDENTIFIER, STUDY_PARTICIPANT_ID
      FROM IHS_2025.VW_BASELINE_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL
    )
    SELECT ...
    FROM DUAL
    ```
    
    ```sql
    -- Final extraction
    WITH enrolled AS (
      SELECT STUDY_PARTICIPANT_ID,
             MONTHS_BETWEEN(STARTDATE0, "Bdate") / 12 AS age_years_at_baseline_start
      FROM IHS_2025.VW_BASELINE_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL
        AND STARTDATE0 IS NOT NULL
        AND "Bdate" IS NOT NULL
    )
    SELECT COUNT(*) AS participant_count,
           AVG(age_years_at_baseline_start) AS mean_age_years_at_baseline_start
    FROM enrolled
    ```
    
    Caveat: I used exact Oracle `MONTHS_BETWEEN(...)/12`; a whole-years age definition would give a slightly different mean. The local knowledge base was not available at `/work/kb`, so this is based on live catalog metadata and query checks.

## bdate_age (run 2)

> What was the mean age of the 2025 cohort's enrolled participants when they started the baseline survey, from the date of birth in the baseline survey view (VW_BASELINE_SURVEY)?

- ✓ **mean age at baseline**: expected 29.76 years (29.26 in whole years), from the quoted "Bdate" column and STARTDATE0
- ✓ **enrolled n**: n = 129 enrolled with a date of birth
- 3 queries, 365s

Answer:

    The mean age was **29.762 years** at baseline survey start, among **129 enrolled 2025 participants**. Rounded: **29.8 years**.
    
    I understood “enrolled participants” as `STUDY_PARTICIPANT_ID IS NOT NULL`, based on the column comment in `IHS_2025.VW_BASELINE_SURVEY`: null means screened but never enrolled. Age was continuous years at `STARTDATE0`, using `"Bdate"` from the same view: `MONTHS_BETWEEN(STARTDATE0, "Bdate") / 12`.
    
    Files:
    `/work/outputs/ihs_2025_mean_age_baseline_start.csv`
    `/work/outputs/ihs_2025_mean_age_baseline_start_dictionary.md`
    
    Catalog/schema evidence: `search_catalog` found `IHS_2025.VW_BASELINE_SURVEY` with `STARTDATE0`, `"Bdate"`, `STUDY_PARTICIPANT_ID`, and `PARTICIPANTIDENTIFIER`. `describe_table` confirmed `STARTDATE0` and `"Bdate"` are `DATE` columns, and `STUDY_PARTICIPANT_ID` identifies enrollment status. The local `/work/kb` directory was not present, so I could not use knowledge-base notes.
    
    SQL run:
    
    ```sql
    SELECT
      COUNT(*) AS baseline_rows,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS distinct_participants,
      COUNT(STUDY_PARTICIPANT_ID) AS rows_with_study_participant_id,
      COUNT("Bdate") AS rows_with_birthdate,
      COUNT(STARTDATE0) AS rows_with_startdate0,
      COUNT(CASE WHEN STUDY_PARTICIPANT_ID IS NOT NULL AND "Bdate" IS NOT NULL AND STARTDATE0 IS NOT NULL THEN 1 END) AS complete_enrolled_rows
    FROM IHS_2025.VW_BASELINE_SURVEY
    ```
    
    Purpose: profile completeness. Row count returned: 1 aggregate row. Result: 139 baseline rows; 129 complete enrolled rows.
    
    ```sql
    WITH enrolled_baseline AS (
      SELECT PARTICIPANTIDENTIFIER, STUDY_PARTICIPANT_ID, STARTDATE0, "Bdate" AS BIRTHDATE
      FROM IHS_2025.VW_BASELINE_SURVEY
      WHERE STUDY_PARTICIPANT_ID IS NOT NULL
    )
    SELECT
      COUNT(*) AS enrolled_rows,
      COUNT(DISTINCT PARTICIPANTIDENTIFIER) AS enrolled_distinct_participants,
      COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS enrolled_distinct_study_ids,
      COUNT(CASE WHEN STARTDATE0 IS NULL THEN 1 END) AS missing_startdate0,
      COUNT(CASE WHEN BIRTHDATE IS NULL THEN 1 END) AS missing_birthdate,
      COUNT(CASE WHEN BIRTHDATE > STARTDATE0 THEN 1 END) AS birthdate_after_startdate,
      COUNT(CASE WHEN MONTHS_BETWEEN(STARTDATE0, BIRTHDATE) / 12 < 15 THEN 1 END) AS age_under_15,
      COUNT(CASE WHEN MONTHS_BETWEEN(STARTDATE0, BIRTHDATE) / 12 > 80 THEN 1 END) AS age_over_80
    FROM enrolled_baseline
    ```
    
    Purpose: duplicate/missing/date sanity check. Row count returned: 1 aggregate row. Result: 129 enrolled rows, 129 distinct participants, no missing dates or flagged age/date issues.
    
    ```sql
    SELECT
      COUNT(*) AS N_ENROLLED_PARTICIPANTS,
      AVG(MONTHS_BETWEEN(STARTDATE0, "Bdate") / 12) AS MEAN_AGE_YEARS_AT_BASELINE_START
    FROM IHS_2025.VW_BASELINE_SURVEY
    WHERE STUDY_PARTICIPANT_ID IS NOT NULL
      AND STARTDATE0 IS NOT NULL
      AND "Bdate" IS NOT NULL
    ```
    
    Purpose: final extraction. Row count returned: 1 aggregate row. Columns: `N_ENROLLED_PARTICIPANTS`, `MEAN_AGE_YEARS_AT_BASELINE_START`.
    
    Caveat to double-check: this uses continuous age, not age last birthday/floored age.
