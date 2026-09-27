# Evaluation run 2026-09-27T09:46:45+00:00

Commit `ad1e97b7186c`, effort medium, model gpt-5.5, DataLab 0.1.0, practice profile (synthetic data only), catalog sha256 `0c66b97f0d0e`.

**0 of 5 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| plan_describe | plan type: a descriptive question gets a describe plan, not an invented exposure | 0/1 | ✓ type; ✗ add-ons only where they apply |
| plan_coverage | plan type: a coverage audit, with only the add-ons it needs | 0/1 | ✓ type; ✗ add-ons only where they apply |
| plan_prediction | plan type: prediction, with validation and what's known at prediction time | 0/1 | ✓ type; ✗ add-ons only where they apply |
| plan_mixed | plan type: association, with timing and repeated observations as add-ons | 0/1 | ✓ type; ✗ add-ons only where they apply |
| plan_affects | plan type: an 'affects' question, with the intended claim made explicit (or asked about) | 0/1 | ✓ type; ✗ add-ons only where they apply; ✓ says what claim is intended |

## plan_describe (run 1)

> Describe nightly sleep duration in the 2025 cohort's Fitbit data during the intern year, month by month.

- ✓ **type**: Describe or compare (proposed describe)
- ✗ **add-ons only where they apply**: at most 2 (proposed: pilot_to_full, repeated_observations, temporal_alignment)
- analysis plan declined (HTTP 204)
- 0 queries, 30s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "describe",
      "analysis_type_label": "Describe or compare",
      "rationale": "The request is to describe a measure over time within one cohort, without estimating an exposure effect or making predictions.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Describe nightly Fitbit sleep duration in the IHS 2025 cohort during the intern year, summarized month by month. The purpose is to show the distribution and coverage of Fitbit-measured sleep across the intern year, not to test a causal claim or compare groups."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Data source checked so far: catalog and column metadata only, not outcome values. Primary table: IHS_2025.FITBITSLEEPLOGS, using PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, MINUTESASLEEP, ISMAINSLEEP, TYPE, LOGTYPE, DURATION, and TIMEINBED as needed for checks. Unit of analysis: Fitbit sleep episode/night, restricted to main sleep records when ISMAINSLEEP indicates true/main sleep. Intern year window assumed to be 2025-07-01 through 2026-06-30, using STARTDATE to assign each sleep record to a month; I will verify observed date coverage and report if the table does not cover the full assumed window. Records without participant identifier, STARTDATE, or MINUTESASLEEP will be counted and excluded from duration summaries."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting summaries I will check row counts, distinct participants, date range, duplicate participant-date main-sleep records, missingness in key fields, impossible or implausible sleep durations, and availability/coverage by month. Fitbit data can be missing non-randomly when participants do not wear/sync devices, so month-to-month changes may reflect coverage as well as sleep behavior. Calendar-month summaries do not adjust for rotations, call schedules, weekends, or site/program differences. No participant identifiers or row-level records will appear in outputs, and small participant counts will be suppressed as needed."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static HTML report in /work/outputs plus reproducible analysis source in /work. The report will include a plain-language summary, sample flow, table provenance and SQL, monthly coverage, and monthly sleep-duration summaries with a static figure and table. Completion criterion: each intern-year month has participant/record coverage and distribution summaries, with limitations stated clearly."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Main measure: nightly sleep duration from IHS_2025.FITBITSLEEPLOGS.MINUTESASLEEP, summarized in hours (minutes / 60). Date/month: STARTDATE, a TIMESTAMP WITH TIME ZONE, assigned to calendar month of sleep start. Inclusion measure: ISMAINSLEEP to restrict to main sleep records; TYPE and LOGTYPE will be tabulated to understand source coding. Summaries by month: number of sleep records, number of participants, median nightly hours, IQR, mean and SD, and selected percentiles if coverage permits. Participant-month summaries may also be shown as a sensitivity to reduce influence of participants with many nights."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Nightly observations are repeated within participant. The primary description will report observation-level nightly summaries by month, accompanied by participant counts and median nights per participant-month. To show whether heavy contributors dominate, I will include a participant-month sensitivity: first average or median nightly sleep within each participant-month, then summarize those participant-month values by calendar month."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "The primary month assignment will use STARTDATE because this is a sleep-log start timestamp and the table has typed time-zone-aware dates. I will report the observed STARTDATE/ENDDATE range and flag records crossing month boundaries; no lagged alignment to next work day is planned for this descriptive question."
        },
        {
          "kind": "pilot_to_full",
          "label": "Pilot, then full run",
          "content": "Pilot first: run the extraction and analysis pipeline on a small, reproducible two-month slice of the intern year, including all participants in those months, to verify filters, date parsing, duplicate handling, missingness checks, and output format. I will report pilot scope, data-quality findings, expected full-run size, and any needed plan revision. After your approval, I will run the same pipeline for the full assumed intern year."
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
- ✗ **add-ons only where they apply**: at most 2 (proposed: pilot_to_full, repeated_observations, temporal_alignment)
- analysis plan declined (HTTP 204)
- 0 queries, 40s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "data_quality",
      "analysis_type_label": "Data quality or coverage",
      "rationale": "This is a coverage/completeness question about whether expected daily Garmin records are present over time, not an exposure-outcome association.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: How complete is Garmin daily summary data for the IHS 2025 cohort, summarized month by month? Purpose: identify which months have strong or weak Garmin daily coverage and whether gaps reflect participant uptake, missing daily records among Garmin users, duplicate rows, or date-range issues."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025. I have checked catalog metadata for IHS_2025.GARMINDAILYSUMMARY, IHS_2025.VGARMINDAILYSUMMARY, and IHS_2025.STUDYPARTICIPANTS, plus the join path. Primary table: IHS_2025.GARMINDAILYSUMMARY, using PARTICIPANTIDENTIFIER or PARTICIPANTID, SUMMARYID, CALENDARDATE, key daily fields such as STEPS, heart-rate fields, DURATIONINSECONDS, and INSERTEDDATE. Roster table: IHS_2025.STUDYPARTICIPANTS, using participant identifiers, ENROLLMENTDATE, and WITHDRAWDATE if usable. Unit of analysis: participant-day, aggregated to participant-month and calendar month. Scope will be calendar dates observed in the 2025 Garmin daily table, with month-by-month summaries. If the Garmin table includes dates outside 2025, I will report that and summarize 2025 calendar months separately."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Checks: participant ID missingness and mapping consistency; duplicate participant-date records; missing or impossible calendar dates; date range; records outside the cohort year; coverage by month; missingness of core daily fields among present daily records; participants in Garmin not found in STUDYPARTICIPANTS; and whether enrollment/withdrawal dates materially affect the denominator. Limitations: a present Garmin daily summary row is evidence that some daily summary was received, but it cannot prove the participant wore the device all day or that all sensor streams were valid. Without an explicit device assignment/eligibility table, completeness relative to 'expected Garmin users' must be approximated by participants with any Garmin daily data, and completeness relative to all enrolled participants may understate completeness if not everyone was expected to use Garmin."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "I will produce a concise static HTML report in /work/outputs and reproducible analysis source in /work. The report will include a month-by-month coverage table and figure, sample-flow counts, data-quality flags, denominator definitions, exact SQL provenance, and notes on assumptions. It will avoid participant identifiers and suppress any participant counts below 11 as '<11'."
        },
        {
          "kind": "expected_structure",
          "label": "Expected structure and rules",
          "content": "Expected structure: ideally one Garmin daily summary row per participant per calendar day among participants expected to have Garmin data. CALENDARDATE should be non-missing and within the intended observation window; participant identifiers should be non-missing; participant-date duplicates should be absent or rare and counted separately. Core daily summaries such as steps, duration, heart-rate, calories, and stress may be missing independently, so row presence and field completeness will be summarized separately."
        },
        {
          "kind": "assessment",
          "label": "Assessment method",
          "content": "Month-level completeness will be summarized as: observed Garmin participant-days; distinct participants with at least one daily row in the month; observed participant-days divided by possible participant-days among participants with any Garmin daily data active in that month; and, if roster/enrollment dates are usable, observed participant-days divided by possible enrolled participant-days for the full 2025 cohort. I will also report median participant-level days observed per month and the percent of participant-months above practical thresholds such as >=1 day, >=15 days, and >=80% of days in month."
        },
        {
          "kind": "flag_handling",
          "label": "Flagged records",
          "content": "Flagged records will not be corrected silently. Duplicate participant-date records, missing IDs, missing dates, dates outside 2025, and records that do not match the roster will be counted and described. For completeness summaries, the primary row-presence metric will count unique participant-date combinations; duplicates will be retained only in quality checks, not counted as extra complete days."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The repeated structure is participant-days nested within participants and months. This audit is descriptive; I will report both observation counts and participant counts, and avoid treating participant-days as independent people."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Coverage will be aligned by GARMINDAILYSUMMARY.CALENDARDATE at calendar-day and calendar-month grain. Enrollment and withdrawal dates, if used for the all-cohort denominator, will be converted to dates before comparing with CALENDARDATE. Time-zone offsets will be described but not used to reassign dates unless the data checks show a clear need."
        },
        {
          "kind": "pilot_to_full",
          "label": "Pilot, then full run",
          "content": "Pilot: first run count-level/profile queries and a small reproducible extraction sufficient to verify identifiers, date range, duplicate logic, and monthly aggregation. I will report pilot scope and ask before scaling if the full extraction looks large. If bounded counts show the full monthly aggregation is small, I will run the complete aggregation directly because it returns only aggregate data."
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
- ✗ **add-ons only where they apply**: at most 2 (proposed: missing_data, pilot_to_full, temporal_alignment)
- analysis plan declined (HTTP 204)
- 0 queries, 39s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "prediction",
      "analysis_type_label": "Prediction",
      "rationale": "This is a prediction question because the goal is to use sleep information from the first four internship weeks to predict a later September PHQ-9 total, not to claim that sleep causes depression symptoms.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the IHS 2025 cohort, can Fitbit-measured sleep during the first four weeks of internship predict September PHQ-9 total score? Purpose: provide an initial, reproducible assessment of whether early-internship Fitbit sleep contains predictive signal for later depressive symptoms, while avoiding causal language."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025 only. Candidate tables checked in the catalog and described before this plan: `IHS_2025.VFITBITSLEEPLOGS` for Fitbit sleep and `IHS_2025.VW_SEP_SURVEY` for September survey PHQ-9 items. Unit of analysis: participant. Exposure/predictor window: first four weeks of internship; unless you edit this plan, I will operationalize that as 2025-07-01 through 2025-07-28 inclusive, using sleep episodes whose sleep date begins in that window. Outcome: September PHQ-9 total from the nine September symptom items. Inclusion: enrolled 2025 participants with a non-missing September PHQ-9 total and enough Fitbit sleep coverage in the first four weeks to compute participant-level sleep summaries. Fitbit-only analysis: Garmin/Apple/HealthKit sleep will not be included."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling I will check linkage by `PARTICIPANTIDENTIFIER`, duplicate survey rows, sleep date parsing, sleep record ranges, main-sleep flags, impossible sleep durations, September PHQ-9 item ranges, missingness, and first-four-week sleep coverage. Main limitations: this is observational prediction, not causal evidence; Fitbit users with adequate early sleep data and September survey responders may not represent all interns; small sample size may limit validation; PHQ-9 item naming is inferred from survey columns and will be documented; the July 1 start date is an analyst assumption unless corrected."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "I will produce a concise static HTML report in `/work/outputs` plus the complete analysis source in `/work`. The report will include sample flow, data-quality checks, sleep coverage, model specification, prediction performance with uncertainty where feasible, and limitations. It will not include participant identifiers or row-level records."
        },
        {
          "kind": "prediction_target",
          "label": "Prediction target and horizon",
          "content": "Target: September PHQ-9 total score for each eligible 2025 participant. Horizon: approximately two months after the first-four-week sleep window, depending on the actual September survey completion date. The analysis asks whether earlier Fitbit sleep improves prediction of later PHQ-9 total within the observed cohort."
        },
        {
          "kind": "available_information",
          "label": "Information available at prediction time",
          "content": "Candidate predictors available by the end of week 4: mean minutes asleep per main-sleep night, median minutes asleep, within-person SD of minutes asleep, number of valid sleep nights, and percent of expected nights observed. To keep the pilot defensible with limited sample size, the primary model will use mean minutes asleep and coverage; additional sleep summaries will be descriptive or sensitivity predictors only if sample size supports them. No September survey information will be used as predictors."
        },
        {
          "kind": "validation",
          "label": "Validation and performance",
          "content": "Because this is one cohort with likely modest sample size, I will use a staged pilot first. In the full analysis, validation will avoid leakage by computing all sleep summaries before using September outcomes and by fitting any transformations or model-selection choices within resampling when applicable. Primary performance will be out-of-sample RMSE/MAE and cross-validated R-squared for predicting PHQ-9 total; I will compare a sleep model against an intercept-only baseline. If the eligible sample is too small for stable cross-validation, I will report optimism-aware exploratory performance and avoid strong prediction claims."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Sleep logs have `STARTDATE` and `ENDDATE` stored as character timestamps. I will parse them and define the sleep date from `STARTDATE`; only episodes starting 2025-07-01 through 2025-07-28 will count for the primary window. September PHQ-9 will use `VW_SEP_SURVEY.STARTDATE1`/`ENDDATE1` to describe survey timing and ensure it occurs after the sleep window."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Primary PHQ-9 total will require all nine September items present and in the expected 0-3 range. Primary sleep summaries will require at least 14 valid main-sleep nights in the 28-day window; I will report how many participants are lost under this threshold and examine sensitivity to at least 7 and at least 21 valid nights. Missingness will be described rather than imputed in the primary analysis."
        },
        {
          "kind": "pilot_to_full",
          "label": "Pilot, then full run",
          "content": "Pilot: first run the full extraction, cleaning, linkage, PHQ-9 scoring, sleep summarization, checks, and model code on a deterministic subset of participants, for example those whose hashed participant identifier falls into a small fraction, while preserving participant-level analysis. I will report pilot scope, counts, data-quality findings, run time, and any issues. I will stop after the pilot and ask for approval before the full run, unless bounded counts show the whole analysis is very small and quick."
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
- ✗ **add-ons only where they apply**: at most 3, including repeated_observations, temporal_alignment (proposed: missing_data, pilot_to_full, repeated_observations, sensitivity, temporal_alignment)
- analysis plan declined (HTTP 204)
- 1 queries, 52s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether nightly sleep duration is related to next-day mood within the same intern, so the target is a within-person observational association rather than a causal effect.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are nights with shorter sleep followed by lower mood the next day for the same intern? Purpose: quantify the within-person sleep-to-next-day-mood association to inform whether this pattern is present in the study data and worth more detailed follow-up."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025. Metadata checked: daily mood is in IHS_2025.VW_DAILY_MOOD; Fitbit sleep logs are in IHS_2025.VFITBITSLEEPLOGS; Garmin sleep summaries are in IHS_2025.VGARMINSLEEPSUMMARY. A small coverage count, before association analyses, found mood rows for 150 participants, Fitbit sleep rows for 65, and Garmin sleep rows for 28. Primary scope: participant-days with a valid Fitbit main sleep episode ending before the next-day mood entry and a valid next-day mood score. Unit of analysis: intern-day, nested within intern. Primary exposure: previous-night sleep duration from Fitbit MINUTESASLEEP among ISMAINSLEEP records. Outcome: next-day MOOD_SCORE from VW_DAILY_MOOD, treated as numeric after checking values. Garmin sleep will be considered a sensitivity source if validity and coverage checks support a comparable total-asleep measure from sleep stage durations."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling, check participant ID linkage, duplicate/multiple sleep episodes per night, duplicate/multiple mood entries per day, valid parsing of sleep and mood dates, temporal alignment, impossible sleep values, mood score coding and range, missingness, device/source coverage, and how many participants contribute within-person variation in both sleep and mood. This is observational and within-person, so it does not prove that sleep causes next-day mood; time-varying factors such as workload, stress, illness, call schedule, or reporting behavior may explain part of the association. Small participant/device groups and sparse overlap will be reported with small-cell suppression where needed."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "Produce a concise static HTML report in /work/outputs and a complete reproducible analysis script in /work. The report will include sample flow, data-quality checks, participant and observation counts, the primary within-person effect estimate with uncertainty, at least one simple sensitivity analysis, and plain-language limitations. No participant identifiers or row-level records will be included in outputs."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the expected difference in next-day mood score associated with a 1-hour shorter-than-usual previous-night sleep duration for the same intern, among intern-days with both valid Fitbit sleep and next-day mood. This supports an association claim only, not a causal claim."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Sleep: Fitbit VFITBITSLEEPLOGS MINUTESASLEEP, converted to hours, restricted to main sleep episodes where ISMAINSLEEP indicates main sleep when available; sleep date will be based on the night ending before the mood day. Mood: VW_DAILY_MOOD MOOD_SCORE, converted to numeric after checking permissible values; if multiple mood entries occur on the same calendar day for an intern, summarize by that day's mean mood score and report how common this is. Garmin sensitivity, if used: VGARMINSLEEPSUMMARY total asleep duration derived from DEEPSLEEPDURATIONINSECONDS + LIGHTSLEEPDURATIONINSECONDS + REMSLEEPINSECONDS, converted to hours, after checking validation and coverage."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: within-person fixed-effects linear regression of next-day mood on person-mean-centered previous-night sleep duration in hours, so the estimate compares an intern with themself across nights. Include calendar day-of-week indicators if coverage supports them, because weekday/weekend reporting and sleep patterns may differ. Estimate uncertainty with participant-clustered robust standard errors. Report the effect per 1-hour shorter sleep night by reversing the sign of the centered-sleep coefficient, with 95% confidence interval."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Repeated intern-days are handled by participant fixed effects for between-person differences and participant-clustered uncertainty. Report total observations, number of participants, median observations per participant, and the number of participants who actually contribute estimable within-person variation in both sleep and mood."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Align previous-night sleep to next-day mood by deriving a sleep-end calendar day and linking it to mood entries on that same next day. Check whether Fitbit STARTDATE/ENDDATE parse consistently and whether Garmin CALENDARDATE corresponds to sleep end date before using it. Exclude ambiguous or unparseable dates and nights without a next-day mood observation."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Use complete-case intern-days for the primary model after valid sleep, mood, and alignment checks. Report missingness and overlap at each stage, including participants with mood only, sleep only, and both. No imputation is planned."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Planned sensitivities: compare the primary estimate with and without day-of-week indicators; repeat after excluding implausible sleep durations outside a defensible range such as 2-14 hours; if Garmin overlap and validity are adequate, run the same within-person analysis for Garmin-derived sleep separately rather than pooling device sources by default."
        },
        {
          "kind": "pilot_to_full",
          "label": "Pilot, then full run",
          "content": "First run a pilot on a reproducible subset of participants, for example the first 15 eligible Fitbit participants after sorting by hashed participant identifier, to exercise extraction, date alignment, quality checks, model fitting, and report generation. Stop after the pilot and report scope, data-quality findings, run time, and expected full-run size. Proceed to the full 2025 analysis only after approval, reusing the same pipeline and settings unless a plan revision is approved."
        }
      ],
      "proposed_after": {
        "queries": 1,
        "tables": [
          "IHS_2025.VFITBITSLEEPLOGS",
          "IHS_2025.VGARMINSLEEPSUMMARY",
          "IHS_2025.VW_DAILY_MOOD"
        ],
        "more_tables": 0
      }
    }

## plan_affects (run 1)

> Does sleeping less make interns' mood worse in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✗ **add-ons only where they apply**: at most 2 (proposed: pilot_to_full, repeated_observations, temporal_alignment)
- ✓ **says what claim is intended**: the plan says whether it's a causal claim or an association
- analysis plan declined (HTTP 204)
- 0 queries, 46s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether lower sleep relates to worse mood; with observational longitudinal data the defensible target is an association, especially within-person, not a causal effect.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are nights with less sleep followed by worse daily mood reports among interns? Purpose: quantify the sleep-mood relationship in a way that separates within-person day-to-day changes from between-person differences as much as these data allow. I will describe this as an observational association, not evidence that sleep causes mood to worsen."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025 only. Candidate measures checked in the catalog and by table descriptions: daily mood from IHS_2025.VW_DAILY_MOOD (PARTICIPANTIDENTIFIER, MOOD_STARTDATE, MOOD_ENDDATE, MOOD_SCORE); sleep from device summaries, primarily IHS_2025.VFITBITSLEEPLOGS (PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, MINUTESASLEEP, ISMAINSLEEP, TYPE, LOGTYPE) and/or IHS_2025.VGARMINSLEEPSUMMARY (PARTICIPANTIDENTIFIER, CALENDARDATE, sleep duration components). Unit of analysis: participant-day or participant mood report matched to the preceding/main sleep period. Inclusion: enrolled 2025 participants with at least one usable sleep record and one numeric mood score that can be temporally aligned. Exact date range, device availability, duplicate rules, and valid ranges will be checked before modeling."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting results I will check table coverage, date ranges, participant linkage, duplicate sleep or mood records per day, parseability and range of MOOD_SCORE, impossible sleep durations, missingness, unmatched sleep/mood records, and participants with enough within-person variation to estimate a day-to-day sleep association. Main limitations: device users may differ from non-users; missing device or mood reports may be related to workload or distress; same-day timing may be ambiguous; unmeasured confounding and reverse directionality remain; observational results cannot establish that sleeping less makes mood worse."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A concise static HTML report in /work/outputs plus the complete analysis source in /work. The report will state the approved plan, data provenance and SQL queries, sample flow, data-quality checks, primary association estimate with uncertainty interval, sensitivity results, and limitations. No participant identifiers or row-level records will be included in outputs; small cells will be suppressed or aggregated."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary target quantity: the within-person association between sleep duration and next available daily mood score among 2025 interns, expressed as the expected difference in mood score per 1 hour less sleep, with uncertainty. I will also report a descriptive between-person association separately if the data support it. This supports an association claim only, not a causal claim."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Mood: MOOD_SCORE from IHS_2025.VW_DAILY_MOOD, treated as numeric only after checking parseability and observed range; lower vs higher direction will be documented from observed coding/metadata if available before interpretation. Sleep: device sleep duration in hours. Fitbit: MINUTESASLEEP/60 from IHS_2025.VFITBITSLEEPLOGS, preferably main sleep records (ISMAINSLEEP) when available. Garmin: total sleep hours from duration/component fields in IHS_2025.VGARMINSLEEPSUMMARY after checking which fields are populated and plausible. If both device sources overlap for a person-day, a predefined source rule or sensitivity comparison will be used rather than averaging blindly."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: a longitudinal association model with participant fixed effects or participant-centered sleep duration, estimating whether a person reports worse mood after nights when they slept less than their own average. Calendar time/day-of-week adjustment will be considered if coverage supports it, because internship time trends and weekdays may affect both sleep and mood. Uncertainty will use clustering by participant or a mixed/gee-style approach appropriate for repeated observations. I will report effect sizes with 95% confidence intervals, not just p-values."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The analysis will report total observations, unique participants, and the number of participants with at least two aligned observations and nonzero within-person sleep and mood variation. Participants with only one aligned observation can contribute to descriptive coverage but not the within-person estimate."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Sleep will be aligned to mood at the participant-day level. The preferred alignment is main sleep ending before the mood report or the prior/night sleep assigned to the mood report date; if timestamps make that unreliable, I will use a clear calendar-day rule and label it as a limitation. I will check alternate same-day versus previous-night alignment as a sensitivity when feasible."
        },
        {
          "kind": "pilot_to_full",
          "label": "Pilot, then full run",
          "content": "First I will run a small reproducible pilot that exercises extraction, parsing, alignment, quality checks, and the model on a bounded subset of participants or dates. I will report pilot scope, sample flow, data-quality issues, run time, and expected full-run size, then ask before scaling to the full 2025 cohort."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }
