# Evaluation run 2026-09-27T10:04:48+00:00

Commit `7b5ed216de8b`, effort medium, model gpt-5.5, DataLab 0.1.0, practice profile (synthetic data only), catalog sha256 `0c66b97f0d0e`.

**10 of 10 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| plan_describe | plan type: a descriptive question gets a describe plan, not an invented exposure | 2/2 | ✓ type; ✓ add-ons only where they apply |
| plan_coverage | plan type: a coverage audit, with only the add-ons it needs | 2/2 | ✓ type; ✓ add-ons only where they apply |
| plan_prediction | plan type: prediction, with validation and what's known at prediction time | 2/2 | ✓ type; ✓ add-ons only where they apply |
| plan_mixed | plan type: association, with timing and repeated observations as add-ons | 2/2 | ✓ type; ✓ add-ons only where they apply |
| plan_affects | plan type: an 'affects' question, with the intended claim made explicit (or asked about) | 2/2 | ✓ type; ✓ add-ons only where they apply; ✓ says what claim is intended |

## plan_describe (run 1)

> Describe nightly sleep duration in the 2025 cohort's Fitbit data during the intern year, month by month.

- ✓ **type**: Describe or compare (proposed describe)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, temporal_alignment
- analysis plan declined (HTTP 204)
- 1 queries, 49s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "describe",
      "analysis_type_label": "Describe or compare",
      "rationale": "This is a descriptive month-by-month summary of one Fitbit measure within one cohort, with no exposure, comparison group, or causal/associational estimand.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Describe nightly Fitbit sleep duration in the 2025 IHS cohort during the intern year, month by month. The goal is to show the typical amount of sleep and the amount of usable Fitbit sleep coverage in each month, so the cohort\u2019s longitudinal sleep pattern can be interpreted with appropriate caution."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Candidate source checked in the catalog and table metadata: IHS_2025.FITBITSLEEPLOGS, with PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, MINUTESASLEEP, ISMAINSLEEP, LOGTYPE, TYPE, TIMEINBED, and related sleep fields. I also checked IHS_2025.VFITBITSLEEPLOGS and will prefer the base table because STARTDATE/ENDDATE are typed timestamps there. Assumed intern-year window: sleep starts on or after 2025-07-01 and before 2026-07-01. A bounded pre-plan coverage query found 16,934 sleep-log rows from 65 participants in that window, with no duration summaries inspected. Unit of analysis for the main summary: eligible sleep night/log; month is assigned from STARTDATE. I will exclude rows missing participant ID, STARTDATE, or MINUTESASLEEP, and will focus on main sleep logs (ISMAINSLEEP indicating true/main sleep) to avoid counting naps as nightly sleep."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before summarizing duration, I will check row counts, participant counts, date range, missingness in key fields, observed ISMAINSLEEP/TYPE/LOGTYPE values, duplicate or multiple main-sleep records per participant-night, impossible or implausible durations, and month-by-month coverage. These summaries can mislead if Fitbit use varies by participant or month, if missingness is related to sleep, if time zones or sleep-start dates place a night into a neighboring month, or if Fitbit\u2019s sleep-stage algorithm changes or misclassifies sleep. Results will be descriptive and not interpreted as causal changes over time."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static HTML report in /work/outputs plus the complete analysis source in /work. The report will include the question, data provenance and SQL, inclusion/exclusion flow, coverage by month, monthly sleep-duration summaries in hours (median and quartiles, plus mean/SD if useful), a month-by-month figure, and limitations. No participant identifiers or row-level records will appear in outputs; small participant counts will be suppressed as <11 if they arise."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Primary measure: nightly sleep duration = IHS_2025.FITBITSLEEPLOGS.MINUTESASLEEP divided by 60 to express hours, restricted to main sleep logs using ISMAINSLEEP after confirming observed coding. Summaries by calendar month of STARTDATE during 2025-07 through 2026-06: number of sleep logs/nights, number of contributing participants, median, IQR (25th and 75th percentiles), and mean/SD as descriptive complements. Coverage: sleep-log counts and participant counts by month, plus missingness/exclusion counts."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Participants can contribute many nights, so monthly summaries will be descriptive at the sleep-log/night level and will separately report the number of contributing participants. I will also compute participant-month summaries (each participant\u2019s median/mean sleep within a month, then summarize those participant-level values across people) as a sensitivity/coverage-aware check so heavy Fitbit users do not dominate the interpretation."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Month will be assigned from the sleep STARTDATE timestamp because the question is month-by-month nightly sleep and the source has a timestamp with time zone. I will report this choice and flag it as a limitation: sleep episodes crossing midnight or time-zone handling could shift some nights across month boundaries. The intern-year window is assumed to be 2025-07-01 through 2026-06-30 based on the 2025 cohort; if you prefer a different date rule, the plan should be edited before approval."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Rows with missing participant ID, STARTDATE, or MINUTESASLEEP will be excluded from duration summaries and counted in the flow. Month-level coverage will be shown so low-use months are not overinterpreted. I will not impute missing nights."
        }
      ],
      "proposed_after": {
        "queries": 1,
        "tables": [
          "IHS_2025.FITBITSLEEPLOGS"
        ],
        "more_tables": 0
      }
    }

## plan_describe (run 2)

> Describe nightly sleep duration in the 2025 cohort's Fitbit data during the intern year, month by month.

- ✓ **type**: Describe or compare (proposed describe)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, temporal_alignment
- analysis plan declined (HTTP 204)
- 0 queries, 33s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "describe",
      "analysis_type_label": "Describe or compare",
      "rationale": "The question asks for month-by-month description of a single Fitbit sleep measure in one cohort, not an exposure-outcome association or prediction.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Describe nightly Fitbit sleep duration among 2025 IHS cohort participants during the intern year, month by month, to show the observed pattern and data coverage across training months. This is a descriptive analysis, not a causal or comparative claim."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025. Primary table checked in metadata: IHS_2025.FITBITSLEEPLOGS; candidate view IHS_2025.VFITBITSLEEPLOGS has the same sleep columns but character date fields. Unit of analysis: a main-sleep episode/night from Fitbit sleep logs. Planned intern-year window: nights starting from 2025-07-01 through 2026-06-30, inclusive of start dates. Include records with nonmissing PARTICIPANTIDENTIFIER, STARTDATE, MINUTESASLEEP, and ISMAINSLEEP indicating the main sleep episode. Exclude non-main sleep logs for the primary summaries, because the request is nightly sleep duration rather than naps or all sleep bouts."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting summaries, check record counts, participant counts, date range, duplicate participant-start/end rows, number of sleep records per participant-month, missingness in key fields, ISMAINSLEEP values, and impossible or implausible durations. The primary summaries could be misleading if device wear/sync coverage differs by month or if participants with more observed nights dominate the night-level mean. Results describe recorded Fitbit main-sleep logs only, not necessarily all sleep, and do not imply causes of month-to-month changes."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static researcher-facing HTML report in /work/outputs plus the complete analysis source in /work. The report will include the plan label, provenance (tables and SQL files), sample flow, data-quality checks, a month-by-month table, and a month-by-month figure. I will keep participant identifiers and row-level sleep logs out of /work/outputs."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep duration: IHS_2025.FITBITSLEEPLOGS.MINUTESASLEEP converted to hours as MINUTESASLEEP / 60. Month: calendar month of STARTDATE for nights beginning during the intern year. Main sleep indicator: IHS_2025.FITBITSLEEPLOGS.ISMAINSLEEP. Planned summaries by month: number of main-sleep records/nights, number of participants with at least one record, median sleep hours, IQR, mean sleep hours, and an uncertainty interval for the mean that accounts for repeated nights within participants where feasible."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Nightly observations are repeated within participants. I will present both night-level summaries and participant-month summaries so months are not interpreted only as a pool of nights. Uncertainty for mean sleep duration will account for clustering by participant, or be labelled descriptive only if the data structure prevents a stable clustered interval."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "A night will be assigned to the calendar month of STARTDATE, interpreted as the night beginning date. Records crossing midnight will remain assigned by STARTDATE. The intern-year window uses STARTDATE from 2025-07-01 to 2026-06-30 inclusive; records outside this window are excluded from primary summaries."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Missing STARTDATE, MINUTESASLEEP, PARTICIPANTIDENTIFIER, or main-sleep status will be counted and excluded from primary sleep-duration summaries. Month-level coverage will be reported as observed nights and participants with records, because absence of Fitbit records may reflect non-wear, non-syncing, or no eligible sleep record rather than no sleep."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_coverage (run 1)

> How complete is the 2025 cohort's Garmin daily data, month by month?

- ✓ **type**: Data quality or coverage (proposed data_quality)
- ✓ **add-ons only where they apply**: proposed: missing_data
- analysis plan declined (HTTP 204)
- 0 queries, 42s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "data_quality",
      "analysis_type_label": "Data quality or coverage",
      "rationale": "This is a completeness/coverage audit of a device data source, not an association or outcome analysis.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: How complete is the 2025 cohort's Garmin daily summary data month by month? Purpose: quantify month-level coverage so researchers can judge whether Garmin daily data are usable for analyses and where gaps in availability may matter."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Primary table checked so far from metadata: IHS_2025.VGARMINDAILYSUMMARY. Unit of analysis: participant-day Garmin daily summary record, summarized to participant-month and calendar month. Measures available from metadata include PARTICIPANTID, PARTICIPANTIDENTIFIER, SUMMARYID, CALENDARDATE, duration, steps, heart-rate, stress, and activity fields. Primary population: participants in the 2025 schema with at least one Garmin daily summary record; if a reliable 2025 cohort roster/device denominator is identifiable without expanding the question, I will report it separately as context rather than mixing it into the primary coverage denominator. Time window: calendar months represented by CALENDARDATE in the 2025 Garmin daily summary table unless a reviewed cohort-specific window is identified during metadata/count checks."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Checks before interpreting coverage: missing participant IDs or calendar dates; duplicate participant-date records; impossible or out-of-range dates; months with partial calendar coverage; whether daily records appear to represent one record per participant per date; missingness in key daily fields such as STEPS and heart-rate measures. Limitations: this audit can show whether daily summary rows and selected fields are present, but it cannot prove the participant wore the device for the full day or explain whether absence reflects non-wear, sync failure, non-enrollment, device choice, or pipeline delay."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A concise static HTML report in /work/outputs plus reproducible source in /work. The report will include a month-by-month table and figure with participant counts, observed daily rows, possible participant-days, row coverage percentage, duplicate counts, missing-key-field percentages, and plain-language notes. Suppress any participant counts below 11 in deliverables."
        },
        {
          "kind": "expected_structure",
          "label": "Expected structure and rules",
          "content": "Expected structure: one Garmin daily summary row per participant per calendar date for participants contributing Garmin data. CALENDARDATE should be non-missing and within a plausible 2025-cohort date range; PARTICIPANTID or PARTICIPANTIDENTIFIER should be present; duplicate participant-date records should be rare and reported. Daily metric fields may be missing, but high missingness will be reported separately from row presence."
        },
        {
          "kind": "assessment",
          "label": "Assessment method",
          "content": "For each calendar month, count unique Garmin participants with at least one record, total observed rows, duplicate participant-date rows, distinct observed participant-days, days in the month represented in the data, and possible participant-days among participants with any Garmin data during the audit window. Completeness will be summarized as distinct observed participant-days divided by possible participant-days for the month, with an additional active-contributor measure: participants with at least one record that month divided by all Garmin contributors in the audit window."
        },
        {
          "kind": "flag_handling",
          "label": "Flagged records",
          "content": "Flagged records are counted and described rather than corrected: missing IDs, missing dates, duplicate participant-date rows, dates outside the apparent cohort/data window, and missing selected daily fields. For coverage percentages, duplicate rows will not increase completeness; participant-date is counted once. Records missing participant ID or calendar date cannot contribute to participant-day coverage and will be reported separately."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Field completeness will be summarized for core daily measures that are present in the table metadata and useful for downstream analyses, especially STEPS, RESTINGHEARTRATEINBEATSPERMINUTE, AVERAGEHEARTRATEINBEATSPERMINUTE, and stress/activity duration fields. These missingness summaries are secondary to row coverage."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_coverage (run 2)

> How complete is the 2025 cohort's Garmin daily data, month by month?

- ✓ **type**: Data quality or coverage (proposed data_quality)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations
- analysis plan declined (HTTP 204)
- 0 queries, 37s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "data_quality",
      "analysis_type_label": "Data quality or coverage",
      "rationale": "This is a data-quality/coverage question about whether expected Garmin daily records are present month by month, not an association or prediction question.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: How complete is Garmin daily summary data for the IHS 2025 cohort, month by month? Purpose: quantify monthly Garmin daily data coverage so researchers can judge which months are usable and where missingness or ingestion gaps may affect later analyses."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Catalog metadata checked before this plan: candidate daily table IHS_2025.GARMINDAILYSUMMARY and view IHS_2025.VGARMINDAILYSUMMARY; participant table IHS_2025.STUDYPARTICIPANTS. Columns checked: Garmin PARTICIPANTID, PARTICIPANTIDENTIFIER, SUMMARYID, CALENDARDATE, duration and activity/HR fields; participant PARTICIPANTIDENTIFIER, PARTICIPANTID, SECONDARYIDENTIFIER, ENROLLMENTDATE, WITHDRAWDATE. Unit of analysis for completeness: participant-day within calendar month. Scope will use calendar months represented in 2025 cohort Garmin daily data, and will link to STUDYPARTICIPANTS to assess enrollment/withdrawal where possible. Because no separate Garmin assignment/status table was found in catalog search, the primary denominator will be participants with at least one Garmin daily record; I will also report the broader enrolled-cohort denominator when linkage permits, clearly labeled as a lower-bound coverage view rather than expected Garmin wear."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Checks before interpreting completeness: row count, distinct participants, date range, missing participant/date fields, duplicate participant-days, duplicate SUMMARYID values, Garmin records that do not link to STUDYPARTICIPANTS, records outside enrollment-to-withdrawal windows where those dates are parseable, and impossible basic values such as negative steps or duration. Limitations: absence of an independent device assignment/eligibility table means we cannot know who was expected to contribute Garmin data; participant-days without a record may reflect non-wear, device/account issues, delayed ingestion, or ineligibility. Monthly percentages may mislead when few participants are contributing, so small participant counts will be suppressed or flagged following the DataLab small-cell rule."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A concise static HTML report in /work/outputs with a month-by-month coverage table and figure, plus the complete analysis source in /work. The report will include definitions, tables used, SQL provenance, sample flow, data-quality flags, and privacy-preserving aggregate counts only; no participant identifiers or row-level records will be included."
        },
        {
          "kind": "expected_structure",
          "label": "Expected structure and rules",
          "content": "Expected Garmin daily structure: at most one usable daily summary per participant per CALENDARDATE, with non-missing PARTICIPANTIDENTIFIER or PARTICIPANTID and non-missing CALENDARDATE. For a participant counted as a Garmin contributor in a month, the expected monthly participant-days are the calendar days in that month that fall within the participant\u2019s observable Garmin window and, where available, enrollment-to-withdrawal window. Basic numeric fields such as STEPS and DURATIONINSECONDS should not be negative."
        },
        {
          "kind": "assessment",
          "label": "Assessment method",
          "content": "I will aggregate in SQL to participant-day/month level rather than export row-level identifiers to deliverables. Month-by-month completeness will be observed participant-days divided by expected participant-days. I will report number of rows, unique linked Garmin participants, active Garmin contributors per month, observed participant-days, expected participant-days, percent completeness, duplicate participant-days, and missing/invalid key fields. A secondary table will show coverage relative to all linked enrolled participants, explicitly labeled as cohort-level observed coverage rather than expected Garmin completeness."
        },
        {
          "kind": "flag_handling",
          "label": "Flagged records",
          "content": "Records with missing participant identifier or missing CALENDARDATE will be counted as unusable for participant-day completeness and reported as flags. Duplicate records for the same participant-date will count once as observed coverage and extra rows will be reported as duplicates. Records failing basic plausibility checks will be counted and described; they will not be corrected. Small cells with fewer than 11 participants will be suppressed or combined in outputs."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The audit is repeated by design: many days per participant and months per participant. Results will be descriptive, with participant-day as the completeness unit and participant counts shown alongside participant-day percentages so heavy contributors do not hide low participant participation."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Missing Garmin daily records will be treated as missing coverage only within the defined expected window. Because the data do not independently distinguish non-wear, data-transfer failure, and lack of Garmin eligibility, interpretation will separate observed data completeness among Garmin contributors from broader cohort coverage."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_prediction (run 1)

> Can Fitbit sleep in the first four weeks of the internship predict the September PHQ-9 total in the 2025 cohort?

- ✓ **type**: Prediction (proposed prediction)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, sensitivity, temporal_alignment
- analysis plan declined (HTTP 204)
- 0 queries, 46s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "prediction",
      "analysis_type_label": "Prediction",
      "rationale": "The question asks whether information available during the first four internship weeks can predict a later September PHQ-9 score, so a prediction framework is the best fit.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, do Fitbit sleep summaries from the first four weeks of internship predict September PHQ-9 total score? Purpose: quantify whether early sleep data add useful predictive signal for later depressive symptom burden, without implying that sleep causes PHQ-9 changes."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Candidate sources checked in catalog/metadata: Fitbit sleep logs from IHS_2025.VFITBITSLEEPLOGS; September survey PHQ-9 items from IHS_2025.VW_SEP_SURVEY; baseline survey PHQ-9 items from IHS_2025.VW_BASELINE_SURVEY as optional benchmark information available before internship. Unit of analysis: participant. Outcome: September PHQ-9 total, calculated as the sum of interest1, down1, asleep1, tired1, appetite1, failure1, concentr1, activity1, and suic1 when item values are valid. Primary predictor set: participant-level Fitbit sleep summaries from the first 28 internship days, assumed to be July 1 through July 28, 2025 unless the data reveal a reviewed cohort-specific anchor. Sleep logs will be reduced before joining to survey data to avoid multiplying rows. Inclusion: enrolled 2025 participants with a valid September PHQ-9 total and enough Fitbit sleep coverage in the first four weeks for summary features. The minimum sleep coverage threshold will be set after a pilot coverage check, with 14 of 28 nights as the default candidate threshold."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling I will check table joins by participant identifier, duplicate survey rows per participant, duplicate or overlapping sleep logs, date parsing and temporal order, September item ranges, Fitbit sleep ranges and impossible values, whether main-sleep flags are usable, sleep coverage in the first four weeks, missingness, and the number of participants contributing to the prediction sample. Main limitations: observational prediction only, possible selection bias from Fitbit use and September survey completion, device/logging missingness, uncertainty from the assumed internship start anchor, and limited sample size for validation. Validation must keep all data from a participant together because the modeling unit is participant."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "I will produce a concise static HTML report in /work/outputs plus the complete analysis source in /work. The report will include the approved question, data provenance with every SQL query result file, sample flow, data-quality checks, model specification, prediction performance with uncertainty where feasible, sensitivity to the sleep-coverage threshold, and limitations. No participant identifiers or row-level records will be placed in /work/outputs."
        },
        {
          "kind": "prediction_target",
          "label": "Prediction target and horizon",
          "content": "Target: September PHQ-9 total score for each 2025 participant with a September survey. Horizon: prediction from first-four-week Fitbit sleep summaries to the September survey, roughly 1 to 2 months later depending on survey completion date. This is a participant-level continuous-outcome prediction task, not a causal estimand."
        },
        {
          "kind": "available_information",
          "label": "Information available at prediction time",
          "content": "Predictors available at prediction time: Fitbit sleep summaries over July 1-28, 2025, including number of valid sleep nights, mean and variability of minutes asleep, time in bed, sleep efficiency, minutes awake, and sleep timing where parsable. I will use main sleep records when ISMAINSLEEP identifies them; if that field is incomplete, I will define one sleep episode per participant-night using the longest plausible sleep record and report this choice. As a benchmark, I may compare sleep-only prediction with baseline PHQ-9 total from VW_BASELINE_SURVEY because it is available before internship and is a strong clinically plausible predictor; the main answer will still isolate sleep-only performance."
        },
        {
          "kind": "validation",
          "label": "Validation and performance",
          "content": "Because the 2025 cohort is a single cohort, validation will be internal. The pilot will exercise the full pipeline on a deterministic participant subset and report feasibility only. For the full run, I will estimate out-of-sample performance using repeated cross-validation or leave-one-out cross-validation depending on the final sample size, with preprocessing and model fitting done inside each training fold to avoid leakage. Performance will be RMSE, MAE, and cross-validated R-squared relative to predicting the training mean. I will compare sleep-only performance with a baseline-only benchmark if baseline PHQ-9 coverage supports it."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Sleep is repeated nightly, but prediction is participant-level. I will summarize first-four-week nights to participant-level features before fitting models. I will report the number of raw sleep records, participant-nights, participants with any sleep data, and participants meeting coverage thresholds."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "The primary first-four-week window is July 1 through July 28, 2025, interpreted from Fitbit STARTDATE/ENDDATE after parsing their string timestamps. September PHQ-9 comes from VW_SEP_SURVEY and must occur after the sleep window. I will check survey completion dates and flag participants whose survey timing is missing or outside the expected September window."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "I will report missing PHQ-9 items, missing September totals, missing baseline PHQ-9 items if used, and Fitbit coverage. PHQ-9 totals will require all nine items valid unless a reviewed scoring rule permits prorating; without that rule, incomplete PHQ-9 totals will be excluded from modeling. For prediction features, I will use complete-case modeling for selected features after applying the sleep coverage threshold."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "I will check whether conclusions change when the minimum first-four-week sleep coverage threshold is changed from the default 14 nights to 7 nights and 21 nights, provided cell/sample sizes remain large enough to report."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_prediction (run 2)

> Can Fitbit sleep in the first four weeks of the internship predict the September PHQ-9 total in the 2025 cohort?

- ✓ **type**: Prediction (proposed prediction)
- ✓ **add-ons only where they apply**: proposed: none
- analysis plan declined (HTTP 204)
- 0 queries, 34s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "prediction",
      "analysis_type_label": "Prediction",
      "rationale": "The question asks whether early Fitbit sleep can predict a later September PHQ-9 total, so the primary claim is out-of-sample predictive performance rather than a causal effect.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 intern cohort, can Fitbit sleep recorded during the first four weeks of internship predict each participant's September PHQ-9 total score? Purpose: estimate whether early objective sleep summaries carry useful predictive signal for later depressive symptom burden, without implying that sleep causes later PHQ-9 scores."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025 only. Unit of analysis: participant. Predictor window: first four weeks of internship, assumed to be July 1-28, 2025 unless revised. Sleep source checked in metadata: IHS_2025.VFITBITSLEEPLOGS, with participant identifier, STARTDATE/ENDDATE strings, MINUTESASLEEP, TIMEINBED, EFFICIENCY, ISMAINSLEEP, TYPE, and sleep-stage columns. Outcome source checked in metadata: IHS_2025.VW_SEP_SURVEY, with PHQ-9 item columns interest1, down1, asleep1, tired1, appetite1, failure1, concentr1, activity1, suic1, plus STARTDATE1/ENDDATE1. Join path checked: shared PARTICIPANTIDENTIFIER; sleep must be aggregated to participant level before joining to the September survey. Inclusion: participants with a September survey row and enough main-sleep Fitbit nights in the first four weeks to summarize sleep; the minimum nights threshold will be set before the full run after a pilot coverage check."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling, check participant linkage, duplicate September survey rows, PHQ-9 item ranges and missingness, sleep date parsing, impossible sleep values, duplicate or overlapping sleep logs, main-sleep availability, first-four-week coverage, and whether outcome completion dates are after the predictor window. Results will be interpreted as prediction/association only. Limitations likely include selective Fitbit use, missing September survey response, a single cohort, and modest sample size relative to candidate predictors."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static HTML report in /work/outputs plus complete source code in /work. The report will include sample flow, data-quality checks, chosen sleep summaries, model specification, validation approach, performance metrics with uncertainty, a compact table/figure of results, sensitivity analyses, and the exact SQL provenance. No participant identifiers or row-level records will be placed in /work/outputs."
        },
        {
          "kind": "prediction_target",
          "label": "Prediction target and horizon",
          "content": "Target: participant-level September PHQ-9 total score, computed as the sum of the nine September survey item columns if all items are present and in the expected 0-3 range. Prediction horizon: from Fitbit sleep during July 1-28, 2025 to PHQ-9 measured on the September survey."
        },
        {
          "kind": "available_information",
          "label": "Information available at prediction time",
          "content": "Candidate predictors available at prediction time: participant-level summaries from first-four-week Fitbit main-sleep records, initially mean minutes asleep, variability in minutes asleep, mean sleep efficiency, mean time in bed, and number/proportion of nights with valid sleep. Predictors will be kept few relative to sample size; coverage variables are included because missing wearable data can itself be informative but also risks reflecting device adherence rather than sleep physiology."
        },
        {
          "kind": "validation",
          "label": "Validation and performance",
          "content": "Because this is one cohort, use internal validation designed to avoid leakage: construct all predictors using only the July 1-28 window, then evaluate participant-level prediction with repeated cross-validation or bootstrap optimism correction depending on pilot sample size. Primary performance metrics: cross-validated RMSE/MAE and out-of-sample R-squared compared with an intercept-only model. Coefficients will be reported with caution as exploratory model interpretation, not as causal effects."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_mixed (run 1)

> Is a shorter night's sleep followed by lower mood the next day, within the same intern, in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, sensitivity, temporal_alignment
- analysis plan declined (HTTP 204)
- 0 queries, 40s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "This asks whether nightly sleep duration is associated with next-day mood within the same intern, not whether sleep causally affects mood.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the IHS 2025 cohort, when an intern sleeps less than their own usual amount on a given night, is their mood score lower the next day? Purpose: estimate a within-person temporal association that separates day-to-day covariation from stable differences between interns."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025 only. I have checked catalog metadata and table schemas, but have not queried sleep or mood values. Candidate mood measure is IHS_2025.VW_DAILY_MOOD, using PARTICIPANTIDENTIFIER, MOOD_STARTDATE/MOOD_ENDDATE, and MOOD_SCORE. Candidate sleep measures are wearable sleep summaries: IHS_2025.VFITBITSLEEPLOGS using STARTDATE/ENDDATE, MINUTESASLEEP, ISMAINSLEEP, TYPE, LOGTYPE; and IHS_2025.VGARMINSLEEPSUMMARY using CALENDARDATE and sleep-stage duration columns. HealthKit interval sleep exists but is lower-level interval data, so I will not use it as the primary source unless Fitbit/Garmin coverage is inadequate or you ask for it. Unit of analysis: person-day mood observations linked to the prior night's sleep summary for the same participant. Include observations with a valid mood score and a valid prior-night sleep duration; exclude unparseable dates, impossible sleep duration values, duplicate/unresolvable same-person same-date summaries, and participants without estimable within-person variation."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling I will check participant ID linkage, date parsing, duplicated person-days, range/coding of MOOD_SCORE, impossible sleep durations, missingness, observation coverage, device/source availability, and temporal alignment. Interpretation will be observational association only; lower sleep may be correlated with workload, illness, stress, reporting behavior, or device wear. Mood scores may be ordinal or text-coded; if the coding is not clearly numeric, I will report the coding issue rather than force an unjustified model."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A concise static HTML report in /work/outputs plus the complete analysis source in /work. The report will include sample flow, data-quality checks, the within-person estimate with uncertainty, a sensitivity check, and limitations. It will not include participant identifiers or row-level records; small participant counts will be suppressed according to DataLab rules."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the within-person difference in next-day mood score associated with one hour less sleep than that participant's own average sleep duration, among participants with linked sleep and next-day mood observations and within-person sleep variation. This supports an association claim, not a causal effect claim."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep: prior-night wearable sleep duration in hours, primarily MINUTESASLEEP/60 from Fitbit main sleep logs and/or total Garmin sleep seconds converted to hours if coverage supports including Garmin. Mood: next-day MOOD_SCORE from VW_DAILY_MOOD, parsed and summarized after inspecting allowed values and ranges. Dates: mood calendar day from MOOD_STARTDATE; sleep assigned to the next day's mood based on the sleep episode/date ending before that mood day or Garmin calendar date, checked empirically before final alignment."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary method: person fixed-effects regression of next-day mood on person-centered prior-night sleep duration, so each intern serves as their own comparison. I will include day-of-week fixed effects if coverage is sufficient because weekday/work patterns may affect both sleep and mood. Uncertainty will use participant-clustered standard errors. I will report the coefficient for one hour less sleep as an effect size with a 95% confidence interval, alongside descriptive within-person plots/summaries."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "I will report the number of linked person-day observations, unique participants, and participants with at least two linked observations and nonzero within-person variation in sleep and mood. The model will use repeated daily observations while clustering uncertainty by participant."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Primary alignment: sleep from night N linked to mood recorded on day N+1, using dates derived from timestamp columns and checked for timezone/date issues. If a participant has multiple sleep summaries that map to the same mood day, I will use a prespecified hierarchy: Fitbit main sleep when available, otherwise Garmin summary; within a source, choose the main/longest sleep episode and flag duplicates. Gaps are left missing rather than imputed."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "I will quantify missing mood, missing prior-night sleep, and unmatched mood/sleep days at the participant-day level. The primary analysis will be complete-case for linked valid person-days; I will describe how coverage differs across participants and avoid generalizing beyond observed days."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Sensitivity checks: repeat the estimate using sleep categorized as shorter-than-usual versus not, and repeat with stricter sleep-duration validity bounds if range checks suggest outliers. If both Fitbit and Garmin contribute materially, I will also report source-specific estimates or a source-adjusted model."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_mixed (run 2)

> Is a shorter night's sleep followed by lower mood the next day, within the same intern, in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, sensitivity, temporal_alignment
- analysis plan declined (HTTP 204)
- 4 queries, 58s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether nightly sleep is related to next-day mood within the same intern, so the target is a longitudinal within-person association rather than a descriptive summary or prediction model.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the IHS 2025 cohort, are nights with shorter sleep followed by lower mood the next day for the same intern? Purpose: estimate the within-person sleep-to-next-day-mood association to guide scientific interpretation and decide whether the pattern is strong enough to pursue in a fuller manuscript-ready analysis."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Candidate measures checked in the catalog and with describe_table: IHS_2025.FITBITSLEEPLOGS has PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, ISMAINSLEEP, MINUTESASLEEP, TIMEINBED; IHS_2025.VW_DAILY_MOOD has PARTICIPANTIDENTIFIER, MOOD_STARTDATE, MOOD_ENDDATE, MOOD_SCORE. Join metadata shows they share participant identifier only, so dates must be aligned before joining. Coverage counts already checked, without querying sleep-duration or mood-score values: Fitbit sleep logs have 20,401 rows from 65 participants, mood has 26,405 rows from 150 participants. Unit of analysis: participant-day mood observations with the prior night's main Fitbit sleep linked by participant and calendar date. Primary scope: participants with at least one valid prior-night Fitbit main sleep record and next-day mood score. Rows with missing participant ID, missing date, missing sleep duration, or nonnumeric/missing mood score will be excluded from the primary model after being counted."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting results, check ID linkage, duplicate participant-day records, timestamp/date alignment, sleep-duration ranges, mood-score parsing and range, observation coverage by participant, and whether enough interns contribute repeated linked days and within-person sleep variation. Main limitations: observational association, possible time-varying confounding such as rotation intensity or stressful events, missing-not-at-random wearable/survey days, measurement differences across devices, and uncertainty from temporal alignment. Small cells or subgroup counts under 11 participants will be suppressed in deliverables."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "First deliver a pilot report and source code in /work/outputs and /work, covering a small reproducible subset that exercises extraction, linkage, checks, and the model. The pilot will report preliminary scope, sample flow, data-quality issues, runtime, and expected full-run size, then ask for approval before scaling. After full-run approval, produce a static HTML report with the primary estimate, uncertainty interval, plots/tables using aggregated data only, sensitivity results, provenance for every SQL query, and complete reproducible source code."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the average within-person difference in next-day mood score associated with a 1-hour shorter-than-that-person-usual prior-night sleep duration, among 2025 interns who contribute linked Fitbit sleep and daily mood data. This supports an associational claim only: it does not establish that shorter sleep causes lower mood."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep exposure: Fitbit MINUTESASLEEP from IHS_2025.FITBITSLEEPLOGS, converted to hours, restricted to main nightly sleep using ISMAINSLEEP when the coding supports it. If multiple main-sleep records map to the same participant-night, the pilot will characterize duplicates and use a prespecified resolution rule, preferably the longest or most complete main sleep record after checking values. Outcome: daily MOOD_SCORE from IHS_2025.VW_DAILY_MOOD, parsed as numeric if valid. The outcome date will be derived from MOOD_STARTDATE. Sleep night will be aligned to the following mood date using the sleep end date or start/end-derived local calendar date after checking timestamp behavior."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: linear mixed model or equivalent fixed-effects regression of next-day mood on within-person centered prior-night sleep hours, with participant fixed effects or random intercepts chosen based on software stability in the pilot. Include day-of-week indicators and calendar time trend if available and estimable, because mood and sleep can vary by schedule/time. Uncertainty: cluster-robust standard errors by participant or a mixed-model confidence interval that accounts for repeated observations; report effect size with 95% confidence interval. Report participant counts, linked observation counts, and the number of participants with estimable within-person sleep variation."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The primary contrast is within-person: sleep hours will be person-mean centered, and participants with no within-person sleep variation in linked records cannot contribute to the within-person slope. Repeated mood days within an intern will be handled by participant fixed effects/random intercepts and participant-clustered uncertainty."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Primary alignment: a sleep episode ending on date D is linked to mood recorded on date D, interpreted as the mood report after the previous night's sleep, subject to timestamp checks. If the data indicate mood timestamps occur before sleep end for some records or time zone handling is ambiguous, report this and use a sensitivity alignment of sleep date D to mood date D+1 or restrict to plausible evening mood entries."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Report coverage from raw tables to linked analytic records: participants and rows with sleep only, mood only, both, missing/non-numeric mood, missing/impossible sleep, duplicates, and insufficient within-person variation. No imputation in the primary analysis."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Sensitivity checks: estimate per 1-hour shorter sleep after excluding implausible sleep durations; compare using person-centered sleep relative to each person's mean versus absolute sleep hours with participant fixed effects; test alternate temporal alignment if needed; and, if coverage warrants, summarize Garmin/HealthKit sleep-source coverage separately rather than pooling devices without comparability checks."
        }
      ],
      "proposed_after": {
        "queries": 4,
        "tables": [
          "IHS_2025.FITBITSLEEPLOGS",
          "IHS_2025.VGARMINSLEEPSUMMARY",
          "IHS_2025.VHEALTHKITSAMPLES_SLEEPANALYSISINTERVAL",
          "IHS_2025.VW_DAILY_MOOD"
        ],
        "more_tables": 0
      }
    }

## plan_affects (run 1)

> Does sleeping less make interns' mood worse in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, sensitivity, temporal_alignment
- ✓ **says what claim is intended**: the plan says whether it's a causal claim or an association
- analysis plan declined (HTTP 204)
- 0 queries, 40s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether less sleep relates to worse mood; with observational longitudinal data this is best treated as an association rather than a causal effect unless a causal design is specified.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are interns' mood ratings worse on days after they slept less?\n\nPurpose: Estimate the longitudinal association between nightly sleep duration and subsequent daily mood within interns, while making clear that this does not by itself show that sleep causes mood changes."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025 only.\n\nPrimary candidate tables checked in the catalog and column metadata: IHS_2025.VFITBITSLEEPLOGS for Fitbit sleep logs and IHS_2025.VW_DAILY_MOOD for daily mood. These share PARTICIPANTIDENTIFIER but no shared date column, so dates must be derived and aligned before joining.\n\nUnit of analysis: participant-day with an observed main nightly sleep episode and a mood score aligned to the following calendar day. Include participants with at least one aligned sleep-mood day; the within-person estimate will require participants with at least two aligned observations and variation in sleep duration. I have checked metadata but have not queried outcome values."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting results I will check: row counts and distinct participant counts in both sources; duplicate sleep or mood records per participant-day; date parsing and date ranges; impossible or implausible sleep values; whether ISMAINSLEEP identifies a single main sleep episode; missing sleep and mood; unmatched sleep and mood days; mood score coding and numeric range; and how many participants contribute within-person variation.\n\nLimitations: observational association can be affected by workload, rotation, baseline mental health, device wear, survey response timing, and other time-varying factors. Fitbit users may not represent all interns. If mood score direction is not documented in metadata, I will infer it only after checking coding and will state the assumption."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "First I will run a small reproducible pilot that exercises the extraction, alignment, data-quality checks, and model on a bounded subset, then report pilot scope, sample flow, data issues, and expected full-run size before asking whether to scale to the full cohort.\n\nFinal deliverables after approval to scale: a concise static HTML report in /work/outputs with methods, sample sizes, effect estimates and uncertainty intervals, sensitivity checks, limitations, and no participant identifiers or row-level records; plus the complete analysis source in /work."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the within-person association between nightly sleep duration and next-day mood score among 2025 interns with aligned Fitbit sleep and daily mood observations. I will express this as the expected difference in mood score for a 1-hour shorter-than-usual sleep duration for the same participant, with uncertainty intervals that account for repeated observations.\n\nThis is not a causal estimand unless the study team specifies and supports a causal design; wording will avoid \u201csleep makes mood worse\u201d as a causal claim."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep: IHS_2025.VFITBITSLEEPLOGS.MINUTESASLEEP, summarized as hours asleep for the main sleep episode where ISMAINSLEEP indicates the main sleep record when available. STARTDATE and ENDDATE will be parsed to determine the sleep night and alignment.\n\nMood: IHS_2025.VW_DAILY_MOOD.MOOD_SCORE, parsed and checked as the daily mood score; MOOD_STARTDATE and MOOD_ENDDATE define response timing and calendar day. If the score direction is not explicit, I will report the observed coding/range and state how \u201cworse\u201d is defined."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: a repeated-measures regression of mood score on person-mean-centered sleep duration, including participant fixed effects or an equivalent within-person specification. This targets whether a given intern reports worse mood after sleeping less than their own average. I will use cluster-robust uncertainty by participant where feasible.\n\nPlanned adjustment: day-of-week or calendar time if coverage supports it, to reduce confounding by weekly schedule patterns. I will not add broad covariates without checking availability and temporal ordering."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The analysis separates within-person and between-person information. The primary estimate uses within-person deviations in sleep duration; I will report total aligned observations, distinct participants, and the number of participants with enough observations and sleep variation to contribute to the within-person estimate. Uncertainty will be clustered by participant or otherwise matched to repeated observations."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Default alignment: the main sleep episode ending on calendar day D is paired with mood recorded on day D, representing next-day mood after that sleep episode. I will check whether sleep timestamps and mood timestamps are consistently parseable and whether multiple mood entries occur per day. If multiple mood entries exist on a day, I will aggregate to participant-day before modeling and report the rule used."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "I will quantify missingness and coverage at each step: sleep records without parseable dates or minutes asleep, mood records without parseable/numeric scores, participant-days missing either component, and participants lost during alignment. Complete-case aligned days will be used for the primary analysis; coverage limitations will be emphasized."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Planned sensitivity checks: compare the primary next-day alignment with same-calendar-date alternatives if timestamp patterns make that plausible; repeat using thresholds for implausible sleep duration such as excluding <2 or >14 hours; and, if multiple mood records per day occur, compare mean versus last mood score for that day."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }

## plan_affects (run 2)

> Does sleeping less make interns' mood worse in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✓ **add-ons only where they apply**: proposed: missing_data, repeated_observations, sensitivity, temporal_alignment
- ✓ **says what claim is intended**: the plan says whether it's a causal claim or an association
- analysis plan declined (HTTP 204)
- 0 queries, 40s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether less sleep is related to worse mood; with these observational repeated measures, the defensible target is an association rather than a causal effect.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are interns' mood scores worse on days aligned with shorter sleep? Purpose: provide an exploratory estimate of the sleep-mood relationship in the repeated daily data, while avoiding causal language unless a stronger design is later specified."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025 only. Candidate tables checked in metadata before this plan: IHS_2025.VFITBITSLEEPLOGS and IHS_2025.VW_DAILY_MOOD. Unit of analysis: participant-day after aligning a main sleep episode to a mood survey date. Population: 2025 participants with at least one usable sleep log and one usable daily mood score that can be aligned by participant and date. Inclusion/exclusion assumptions to verify in the pilot: keep main sleep episodes where ISMAINSLEEP indicates the primary sleep; use nonmissing numeric MINUTESASLEEP; use nonmissing numeric MOOD_SCORE; exclude impossible or implausible values based on observed coding/ranges after checking distributions."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting results I will check table coverage, date ranges, duplicate sleep or mood records per participant-day, whether MOOD_SCORE is numeric and its direction/range, sleep duration ranges, main-sleep coding, unmatched sleep and mood days, missingness, and how many participants contribute within-person variation in both sleep and mood. Main limitations: wearable coverage and mood survey completion may be nonrandom; same-day alignment can mix temporal ordering; unmeasured stressors, rotation schedule, workload, and baseline mental health can confound the association; observational data cannot establish that sleep makes mood worse."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "After approval, I will run a small reproducible pilot first and stop before a full run. The pilot deliverables will be a concise report in /work/outputs, reusable source code in /work, sample-flow and data-quality tables, an effect-size estimate with uncertainty, and a note on whether the pipeline is ready to scale. No participant identifiers or row-level records will be placed in /work/outputs."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the within-person association between nightly sleep duration and aligned mood score, estimated as the expected difference in mood score for a 1-hour shorter sleep duration, comparing a participant with themself across days. This supports an association statement only, not a causal claim. A secondary between-person descriptive contrast may summarize whether interns who sleep less on average also report different average mood, kept separate from the within-person estimate."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep exposure: IHS_2025.VFITBITSLEEPLOGS.MINUTESASLEEP, converted to hours, restricted to the main sleep episode using ISMAINSLEEP if coding supports that choice after checking values. Mood outcome: IHS_2025.VW_DAILY_MOOD.MOOD_SCORE, parsed as numeric after verifying its coding, range, and whether higher means better or worse mood. Time variables: VFITBITSLEEPLOGS.STARTDATE/ENDDATE and VW_DAILY_MOOD.MOOD_STARTDATE/MOOD_ENDDATE; these will be converted to calendar dates for alignment."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Pilot model: linear mixed-effects or fixed-effects regression suitable for repeated participant-day data, depending on available software and observed data size. The primary contrast will separate within-person sleep deviations from each participant's own mean sleep; uncertainty will be clustered by participant or estimated with a model that accounts for repeated observations. I will consider calendar time/day-of-week adjustment if the pilot shows adequate data coverage and clear timing."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Repeated daily observations are expected. I will report observations, participants, median observations per participant, and the number of participants with at least two aligned days and nonzero within-person variation in sleep and mood. The within-person result will be based only on participants who contribute estimable within-person variation."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Primary alignment for the pilot: a sleep episode ending on a calendar date is linked to mood on that same calendar date, interpreted as sleep before or around that day's mood report only after checking timestamps. If mood timing suggests a next-day or prior-day alignment is more appropriate, I will treat that as a sensitivity or propose a plan revision before changing the primary analysis."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Missing sleep, mood, participant ID, or alignment date will be summarized in the sample flow and excluded from the primary complete-case pilot. I will report coverage by source and time, and note that missingness may be related to mood, workload, or device use."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Planned sensitivity checks for the pilot: compare same-day versus next-day alignment if timestamps support both; examine results excluding implausible sleep values using transparent thresholds; and compare the within-person estimate with an unadjusted descriptive association."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }
