# Evaluation run 2026-09-29T03:46:49+00:00

Commit `e84c2e696a44` (with uncommitted changes), effort medium, model gpt-5.5, DataLab 0.2.0b8, practice profile (synthetic data only), catalog sha256 `e7889c502cfe`.

**1 of 1 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| cohort_coverage | the cohort applied to numerator and denominator: people outside it, a duplicate cohort row | 1/1 | ✓ cohort share; ✓ person-days, no duplicate-join inflation; ✓ day rule stated |

## cohort_coverage (run 1)

> Take the 2025 cohort to be the enrolled participants in IHS_2025.VW_IHS_PARTICIPANT_SUMMARY (those with a STUDY_PARTICIPANT_ID). What share of that cohort has any HealthKit resting heart rate data, and how many person-days of it do they contribute in total? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 5 queries, 132s

Answer:

    Exploratory bottom line: **58 of 136 enrolled 2025 participants had HealthKit resting heart-rate data, or 42.6% of the cohort**. Together, they contributed **19,297 person-days** with non-missing resting heart-rate values.
    
    I counted the cohort as distinct non-null `STUDY_PARTICIPANT_ID`s in `IHS_2025.VW_IHS_PARTICIPANT_SUMMARY`, joined HealthKit records by `PARTICIPANTIDENTIFIER`, and counted person-days as distinct participant × `STARTDATE` calendar date pairs in `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE`. The joined records had no missing `STARTDATE` or `VALUE`; units were `count/min`.
    
    I saved the reproducible summary here: `/work/outputs/healthkit_resting_hr_coverage_2025.html`  
    Source script: `/work/scripts/healthkit_resting_hr_coverage_2025.R`
    
    Key limitation: I did not restrict to a specific internship window; the observed joined HealthKit resting-HR records span 2025-04-02 through 2026-06-30.
