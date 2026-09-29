# Evaluation run 2026-09-29T03:54:58+00:00

Commit `e84c2e696a44` (with uncommitted changes), effort medium, model gpt-5.5, DataLab 0.2.0b8, practice profile (synthetic data only), catalog sha256 `e7889c502cfe`.

**2 of 2 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| coverage_open | the same, with the cohort left to the agent: people with data outside it | 2/2 | ✓ cohort share; ✓ person-days, no duplicate-join inflation; ✓ day rule stated |

## coverage_open (run 1)

> What share of the 2025 cohort's enrolled participants have any HealthKit resting heart rate data, and how many person-days of it do they contribute? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 6 queries, 139s

Answer:

    Exploratory bottom line: **58 of 136 enrolled 2025 participants had HealthKit resting heart rate data on or after enrollment, or 42.6%**, contributing **19,247 participant-days**.
    
    I defined “enrolled” as non-missing `SECONDARYIDENTIFIER` in `IHS_2025.STUDYPARTICIPANTS`, and counted HealthKit resting HR from `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE`. Person-days are distinct participant-by-calendar-day using `TRUNC(STARTDATE)`.
    
    A sensitivity check including all linked rows regardless of enrollment date gives the same participant numerator, **58**, and **19,297 person-days**; the difference is 50 pre-enrollment person-days.
    
    QC notes: no missing `STARTDATE` or `VALUE` in the primary set, all values were numeric, units were `count/min`, and the primary set had 22,176 sample rows.
    
    Report: `/work/outputs/healthkit_rhr_coverage_2025.html`  
    Source script: `/work/scripts/healthkit_rhr_coverage_2025.py`

## coverage_open (run 2)

> What share of the 2025 cohort's enrolled participants have any HealthKit resting heart rate data, and how many person-days of it do they contribute? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 8 queries, 139s

Answer:

    Exploratory bottom line: among the 136 enrolled 2025 participants, 58 had at least one HealthKit resting heart rate record, so the share is 42.6%. Those enrolled participants contributed 19,297 distinct person-days of resting heart rate data.
    
    I used `IHS_2025.STUDYPARTICIPANTS` for enrollment, defining enrolled as non-null `SECONDARYIDENTIFIER`, and `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE` for the measure. Person-days are distinct enrolled participant plus `STARTDATE` calendar day, requiring non-missing participant ID, start date, and value.
    
    I wrote the reproducible source and report here:
    
    - Report: `/work/outputs/healthkit_rhr_coverage_2025.html`
    - Source: `/work/scripts/healthkit_rhr_coverage_2025.py`
    
    Key checks: no duplicate participant identifiers in the enrolled denominator, no missing participant/date/value fields in the RHR table, all checked RHR values were numeric, and units were `count/min`. A small number of HealthKit RHR participants were not included because they were not enrolled or did not match the participant table; those small counts are suppressed in the report.
    
    I also suggested a knowledge-base update for the HealthKit resting heart rate table/date/unit details, so the lab can review and keep that convention.
