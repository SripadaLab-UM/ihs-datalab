# Evaluation run 2026-09-29T04:01:01+00:00

Commit `73dd5f12a2ba`, effort medium, model gpt-5.5, DataLab 0.2.0b8, practice profile (synthetic data only), catalog sha256 `e7889c502cfe`.

**2 of 2 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| coverage_open | the same, with the cohort left to the agent: people with data outside it | 2/2 | ✓ cohort share; ✓ person-days, no duplicate-join inflation; ✓ day rule stated |

## coverage_open (run 1)

> What share of the 2025 cohort's enrolled participants have any HealthKit resting heart rate data, and how many person-days of it do they contribute? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 1 queries, 135s

Answer:

    Exploratory bottom line: **58 of 136 enrolled 2025 participants had any non-missing HealthKit resting heart-rate data, or 42.6%**. Those participants contributed **19,297 person-days** using the local calendar date of the HealthKit sample `STARTDATE`.
    
    I used `IHS_2025.VW_IHS_PARTICIPANT_SUMMARY` for the enrolled denominator (`STUDY_PARTICIPANT_ID` not null, de-duplicated to one row per participant) and `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE` for resting HR. The included HealthKit rows had no missing `STARTDATE`, no non-numeric values, and no values outside a broad 0-250 bpm plausibility range. The `RECORD_DATE` day rule would give a different total, **16,906 person-days**, so `STARTDATE` is the better interpretation for “days of measured data.”
    
    Report: `/work/outputs/healthkit_rhr_coverage_2025.html`  
    Source script: `/work/scripts/healthkit_rhr_coverage_2025.R`
    
    No uncertainty interval is reported because this is a descriptive coverage count from the currently available database, not a sampled estimate.

## coverage_open (run 2)

> What share of the 2025 cohort's enrolled participants have any HealthKit resting heart rate data, and how many person-days of it do they contribute? Exploratory is fine: no plan needed.

- ✓ **cohort share**: expected 58 of 136 (42.6%): the cohort has 137 summary rows for 136 people, and 66 people in the table include 8 outside the cohort
- ✓ **person-days, no duplicate-join inflation**: expected 16,906, 19,369 or 19,297 person-days (by day rule); joining the duplicate summary row gives 17,244 and the like
- ✓ **day rule stated**: which date makes a day
- 4 queries, 123s

Answer:

    Exploratory bottom line: 58 of 136 enrolled 2025 participants had at least one HealthKit resting heart rate row, which is 42.6% of the enrolled cohort. Using the local calendar date of `STARTDATE` as the day rule, they contributed 19,297 person-days of resting heart rate data.
    
    I used distinct enrolled `PARTICIPANTIDENTIFIER`s from `IHS_2025.VW_IHS_PARTICIPANT_SUMMARY` where `STUDY_PARTICIPANT_ID` is not null, joined to `IHS_2025.HEALTHKITSAMPLES_RESTINGHEARTRATE`. A duplicate cohort row was deduplicated before joining. `RECORD_DATE` gave fewer person-days, 16,906, so the date-field choice matters; I used `STARTDATE` because it reflects the measurement date.
    
    Report: `/work/outputs/healthkit_rhr_coverage_2025.html`  
    Source: `/work/scripts/healthkit_rhr_coverage_2025.py`
    
    The local KB was unavailable in this workspace, so I grounded this in the live catalog/table descriptions instead.
