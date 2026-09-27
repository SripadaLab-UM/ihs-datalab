# Evaluation run 2026-09-27T10:00:17+00:00

Commit `f4b9b2c4ce56`, effort medium, model gpt-5.5, DataLab 0.1.0, practice profile (synthetic data only), catalog sha256 `0c66b97f0d0e`.

**3 of 5 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| plan_describe | plan type: a descriptive question gets a describe plan, not an invented exposure | 0/1 | ✓ type; ✗ add-ons only where they apply |
| plan_coverage | plan type: a coverage audit, with only the add-ons it needs | 1/1 | ✓ type; ✓ add-ons only where they apply |
| plan_prediction | plan type: prediction, with validation and what's known at prediction time | 1/1 | ✓ type; ✓ add-ons only where they apply |
| plan_mixed | plan type: association, with timing and repeated observations as add-ons | 1/1 | ✓ type; ✓ add-ons only where they apply |
| plan_affects | plan type: an 'affects' question, with the intended claim made explicit (or asked about) | 0/1 | ✓ type; ✗ add-ons only where they apply; ✓ says what claim is intended |

## plan_describe (run 1)

> Describe nightly sleep duration in the 2025 cohort's Fitbit data during the intern year, month by month.

- ✓ **type**: Describe or compare (proposed describe)
- ✗ **add-ons only where they apply**: at most 2 (proposed: missing_data, repeated_observations, temporal_alignment)
- analysis plan declined (HTTP 204)
- 0 queries, 33s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "describe",
      "analysis_type_label": "Describe or compare",
      "rationale": "This is a descriptive question about the distribution of Fitbit sleep duration over intern-year months, without an exposure, comparison group, or causal claim.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, what does nightly Fitbit sleep duration look like month by month during the intern year?\n\nPurpose: Produce a concise descriptive summary of sleep duration over time, suitable for understanding cohort-level patterns and coverage. This will not estimate a causal effect or test whether sleep changed because of internship."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025.\n\nData source checked so far: catalog and column metadata only. The candidate Fitbit sleep source is IHS_2025.FITBITSLEEPLOGS, with participant identifier, STARTDATE and ENDDATE timestamps, MINUTESASLEEP, TIMEINBED, ISMAINSLEEP, TYPE, LOGTYPE, and related sleep-stage columns. I will use the raw table rather than VFITBITSLEEPLOGS because STARTDATE/ENDDATE are typed timestamps in the raw table.\n\nAssumed intern-year window unless the data indicate a reviewed cohort-specific convention: July 1, 2025 through June 30, 2026, grouped into calendar months by sleep STARTDATE. Unit of analysis: a participant-night Fitbit sleep log. Primary inclusion will be main sleep records where ISMAINSLEEP indicates true/main sleep, STARTDATE is in the intern-year window, and MINUTESASLEEP is non-missing and within plausible range. I will report the effect of exclusions and coverage by month."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Checks before interpretation: row counts and participant counts; date range; duplicate or multiple main sleep logs per participant-night; values outside plausible ranges; missing MINUTESASLEEP, STARTDATE, participant identifier, or ISMAINSLEEP; distribution of TYPE and LOGTYPE; and monthly coverage by participant.\n\nMain limitations: Fitbit availability and wear/sync behavior may vary by person and month, so monthly summaries may reflect both sleep and data coverage. Repeated nights from the same participants make participant-night summaries descriptive rather than independent observations. Small participant counts will be suppressed or combined according to DataLab rules."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "Deliverables: a static HTML report in /work/outputs with the question, assumptions, SQL provenance, sample flow, data-quality checks, a month-by-month table, and a month-by-month figure; plus the complete source code in /work. The report is done when it contains no participant identifiers or row-level records, states filters and sample sizes, and can be regenerated from the saved script and query result files."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Primary measure: nightly sleep duration from IHS_2025.FITBITSLEEPLOGS.MINUTESASLEEP, converted to hours for reporting. I will summarize each month using number of sleep logs, number of contributing participants, mean, standard deviation, median, interquartile range, and selected percentiles if cell-size rules allow. I will also include participant-level monthly means as a sensitivity/descriptive companion so months with many nights from a few participants do not dominate the story."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The primary table describes participant-nights and explicitly reports both observations and participants. Because participants contribute repeated nights, I will avoid p-values and independence-based month comparisons. I will include a participant-weighted monthly summary based on each participant's monthly mean sleep duration as an additional descriptive view."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Month will be assigned using STARTDATE, interpreted as the date the sleep episode began. If the logs show many sleep episodes beginning before midnight or a date convention that looks inconsistent with nightly sleep, I will flag this in the pilot and ask before changing the alignment rule."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Missing or implausible sleep duration and missing dates/identifiers will be counted and excluded from duration summaries. I will report monthly coverage so the reader can distinguish sleep-duration patterns from changing data availability."
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
- ✓ **add-ons only where they apply**: at most 2 (proposed: repeated_observations, temporal_alignment)
- analysis plan declined (HTTP 204)
- 0 queries, 40s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "data_quality",
      "analysis_type_label": "Data quality or coverage",
      "rationale": "This is a completeness and coverage audit of expected Garmin daily records, not an exposure-outcome analysis.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: How complete is Garmin daily summary data for the 2025 IHS cohort, month by month? Purpose: quantify monthly coverage so the team can judge whether Garmin daily data are usable for later analyses and identify months with weak coverage."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025. Candidate tables checked in the catalog before this plan: IHS_2025.GARMINDAILYSUMMARY / VGARMINDAILYSUMMARY and participant tables including IHS_2025.STUDYPARTICIPANTS. Planned source table: IHS_2025.GARMINDAILYSUMMARY, using PARTICIPANTIDENTIFIER, PARTICIPANTID, SUMMARYID, CALENDARDATE, duration and core daily measures such as STEPS only for validity checks. Population denominator: enrolled 2025 study participants, defined as STUDYPARTICIPANTS rows with non-null SECONDARYIDENTIFIER, unless checks show this is not the right enrollment marker. Unit of analysis for completeness: participant-day; reporting unit: calendar month. Time window: observed Garmin CALENDARDATE range, summarized by month; if the range extends beyond the expected internship year, I will show that explicitly rather than trimming silently."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Checks before interpretation: table row counts, distinct participant counts, date range, missing participant/date identifiers, duplicate participant-date records, unmatched Garmin participant identifiers against STUDYPARTICIPANTS, impossible dates, and availability of rows with plausible daily content. Limitations: absence of a daily Garmin row may reflect non-wear, device non-use, syncing/API gaps, non-Garmin participants, or late onboarding; this audit cannot distinguish those causes without device assignment or connection logs. Calendar-month completeness may understate early/late months if participants enroll throughout the year."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static HTML report in /work/outputs and the complete reproducible source script in /work. The report will include monthly observed participant-days, expected participant-days, percent completeness, participants with any Garmin daily row, median participant-level monthly coverage, duplicate/unmatched/missingness checks, and a concise interpretation. No participant identifiers or row-level records will be included."
        },
        {
          "kind": "expected_structure",
          "label": "Expected structure and rules",
          "content": "Expected structure: at most one Garmin daily summary record per participant per CALENDARDATE for an enrolled 2025 participant. A complete month for a participant means one valid daily row for each calendar day that participant is in the denominator for that month. Dates should be valid calendar dates; daily numeric fields should be nonnegative where applicable. Garmin daily data are expected to be repeated observations within participants."
        },
        {
          "kind": "assessment",
          "label": "Assessment method",
          "content": "I will summarize coverage by month as observed unique participant-days divided by expected participant-days. The primary denominator will be enrolled participants present in STUDYPARTICIPANTS; I will also report a secondary Garmin-active denominator among participants with at least one Garmin daily row, because many enrolled participants may never provide Garmin data. I will show monthly participant counts with any row and participant-level coverage distribution (median/IQR), suppressing small participant counts below 11 where needed."
        },
        {
          "kind": "flag_handling",
          "label": "Flagged records",
          "content": "Records with missing participant identifier or missing CALENDARDATE will be counted as invalid and excluded from participant-day completeness numerators. Duplicate participant-date rows will be counted in diagnostics but collapsed to one observed participant-day for coverage. Rows that do not match an enrolled participant will be counted and excluded from enrolled-cohort completeness; I will report whether this materially affects results without listing identifiers."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The analysis treats daily rows as repeated observations within participant. Coverage percentages are descriptive, so no inferential model is planned; participant-level coverage summaries will be used to avoid interpreting many days from the same participant as many independent people."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Completeness will be based on GARMINDAILYSUMMARY.CALENDARDATE, a DATE field. I will not re-derive local dates from STARTTIMEINSECONDS unless date checks suggest inconsistencies. Month assignment will use calendar month of CALENDARDATE."
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
- ✓ **add-ons only where they apply**: at most 2 (proposed: none)
- analysis plan declined (HTTP 204)
- 0 queries, 36s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "prediction",
      "analysis_type_label": "Prediction",
      "rationale": "The question asks whether information available in the first four internship weeks can predict a later PHQ-9 score, so prediction is the best-fitting plan type.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, can Fitbit sleep measured during the first four weeks of internship predict September PHQ-9 total score? Purpose: to assess whether early objective sleep summaries have useful prognostic signal for subsequent depressive symptoms. This will be framed as predictive performance and association, not as evidence that sleep causes later PHQ-9."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025. Unit of analysis: participant. Candidate sleep source checked in catalog: IHS_2025.VFITBITSLEEPLOGS, with PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, MINUTESASLEEP, TIMEINBED, EFFICIENCY, ISMAINSLEEP, TYPE, and sleep-stage fields. Outcome source checked in catalog: IHS_2025.VW_SEP_SURVEY, with September PHQ-9 item columns interest1, down1, asleep1, tired1, appetite1, failure1, concentr1, activity1, suic1. Baseline survey source checked in catalog: IHS_2025.VW_BASELINE_SURVEY, with baseline PHQ-9 item columns and enrollment/start dates. Assumption to confirm in the data: \u201cfirst four weeks of internship\u201d means the fixed window 2025-07-01 through 2025-07-28 inclusive, using Fitbit sleep-log start date; if the cohort uses another official start date, I will ask before changing it. Include enrolled participants with a valid September PHQ-9 total and at least a minimum amount of Fitbit sleep coverage in the window, with the minimum treated as a sensitivity choice."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling I will check joinability by participant identifier, duplicate September survey rows, PHQ-9 item ranges and missingness, Fitbit sleep date parsing, impossible sleep values, multiple sleep logs per night, main-sleep flags, participant-level sleep coverage, and whether September survey completion occurs after the sleep window. Main limitations: Fitbit users with enough early sleep data may differ from others; prediction in one cohort may be optimistic if sample size is small; the model will not support causal claims."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A static HTML report in /work/outputs plus complete source code in /work. The report will include sample flow, data-quality checks, model specification, predictive performance with uncertainty, coefficient/effect-size summaries, sensitivity to the Fitbit coverage threshold, and exact SQL provenance. No participant identifiers or row-level records will be included in /work/outputs."
        },
        {
          "kind": "prediction_target",
          "label": "Prediction target and horizon",
          "content": "Target: September PHQ-9 total score, computed as the sum of the nine September PHQ-9 item columns when item values are valid and sufficiently complete. Prediction horizon: approximately 5-9 weeks after the first-four-weeks sleep window, depending on September survey completion date. Prediction population: enrolled 2025 interns with September PHQ-9 and early Fitbit sleep data."
        },
        {
          "kind": "available_information",
          "label": "Information available at prediction time",
          "content": "Primary predictors known by the end of week 4: participant-level summaries of Fitbit sleep logs during 2025-07-01 to 2025-07-28, likely mean minutes asleep, mean time in bed, mean efficiency, variability in minutes asleep, and number/proportion of nights observed. I will include baseline PHQ-9 total as a comparator/adjustment because it is available before internship and is expected to be strongly prognostic; I will report sleep-only and baseline-plus-sleep models so the incremental predictive value of Fitbit sleep is visible."
        },
        {
          "kind": "validation",
          "label": "Validation and performance",
          "content": "Because this is a single 2025 cohort, validation will use resampling within the cohort rather than a separate external cohort. The primary model will be linear regression with cross-validated prediction error (RMSE/MAE and out-of-sample R-squared), using folds split by participant so no person appears in both training and test data. Feature selection, scaling, and any tuning will happen inside the training folds to prevent leakage. Bootstrap or repeated cross-validation will be used for uncertainty where feasible."
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
- ✓ **add-ons only where they apply**: at most 4, including repeated_observations, temporal_alignment (proposed: missing_data, repeated_observations, sensitivity, temporal_alignment)
- analysis plan declined (HTTP 204)
- 1 queries, 43s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks whether night-to-night sleep duration is associated with next-day mood within the same intern, so the target is a within-person longitudinal association rather than a causal effect.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are shorter sleep nights followed by lower mood scores the next day within the same intern?\n\nPurpose: Estimate the same-person, day-to-day association between nightly sleep duration and next-day mood. This will describe temporal covariation in the observed study data; it will not by itself show that sleep causes mood changes."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort/schema: IHS_2025.\n\nCandidate measures checked in the catalog and schemas: IHS_2025.FITBITSLEEPLOGS (MINUTESASLEEP, STARTDATE, ENDDATE, ISMAINSLEEP), IHS_2025.GARMINSLEEPSUMMARY (duration and stage-duration columns, CALENDARDATE), and IHS_2025.VW_DAILY_MOOD (MOOD_STARTDATE, MOOD_ENDDATE, MOOD_SCORE). Coverage counts already checked before this plan: Fitbit sleep 20,401 rows/65 participants; Garmin sleep 9,571 rows/28 participants; daily mood 26,405 rows/150 participants.\n\nUnit of analysis: participant-day pairs, where one sleep night is aligned to the following calendar day's mood for the same participant. The analysis population will include participants with at least two valid paired observations and within-person variation in sleep duration; participants without estimable within-person variation will be counted but excluded from the within-person estimate."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before modeling I will check: date ranges and temporal alignment; duplicate sleep or mood records per participant-day; unmatched sleep and mood records; impossible or implausible sleep durations and mood scores; nonnumeric mood coding; missingness; device/source overlap; and how many participants contribute paired days and within-person sleep variation.\n\nExpected limitations: mood reporting and wearable availability may be nonrandom; device-specific sleep algorithms may differ; unmeasured time-varying factors such as workload, schedule, illness, call nights, or stress may confound the association; residual serial correlation may remain even with clustered uncertainty. Results will be described as observational association, not causal effect."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "I will first run a small reproducible pilot that exercises extraction, alignment, checks, and the planned model; report pilot scope, sample flow, data-quality issues, run time, and projected full-run size; then ask for approval before scaling to the full 2025 extraction.\n\nAfter the full run is approved, I will produce a concise HTML report in /work/outputs and complete reproducible analysis source in /work. The report will include sample flow, data-quality checks, the main effect estimate with uncertainty interval, a simple figure/table of the association, sensitivity analyses, query provenance, and limitations without participant identifiers or row-level records."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the expected difference in next-day mood score associated with one fewer hour of sleep than that intern's own average sleep, among 2025 interns with valid paired sleep-mood days. The claim is a within-person temporal association, not a causal effect."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Exposure: nightly sleep duration in hours. Primary source will be wearable sleep logs in IHS_2025, using Fitbit MINUTESASLEEP for main-sleep records when available and Garmin sleep duration or summed measured sleep stages when available; exact operationalization will be finalized in the pilot after checking source overlap, ranges, and duplicate records. If both device sources exist for the same participant-day, I will handle this by a prespecified source rule after inspecting coverage, favoring a single record per participant-night.\n\nOutcome: next-day mood score from IHS_2025.VW_DAILY_MOOD.MOOD_SCORE, converted to numeric only if coding supports that. If multiple mood entries occur on a day, the planned daily mood summary is the mean score for that participant-day, with sensitivity to using the first entry of the day if duplicates are common."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: a within-person linear model with participant fixed effects, estimating mood on day t as a function of sleep duration from the preceding night, centered within participant. I will include day-of-week indicators as a basic adjustment for weekly rhythm if coverage supports it. Uncertainty will use participant-clustered standard errors or a mixed model with participant-level dependence handled; the report will state which implementation was used and any remaining dependence concerns.\n\nThe coefficient will be reported per 1 hour less sleep with a 95% confidence interval. I will also report descriptive within-person correlations when appropriate, clearly as secondary."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "Repeated daily observations are central to the question. The analysis will separate within-person sleep deviations from between-person differences by centering sleep duration within participant or using participant fixed effects. I will report observations, participants, participants with at least two paired days, and participants with nonzero within-person sleep variation."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Sleep will be assigned to the next mood day using calendar dates derived from sleep end date or the device sleep calendar date, after checking available timestamp types and date ranges. Mood will be assigned by MOOD_STARTDATE calendar date. I will explicitly check whether a one-day lag produces plausible pairings and quantify unmatched records and gaps."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "Complete-case paired days will be used for the main analysis: valid sleep duration and valid next-day numeric mood. I will report missingness and coverage by source and participant. I will not impute missing sleep or mood in this initial analysis."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Sensitivity checks will include: device/source handling if both Fitbit and Garmin contribute; excluding implausibly short or long sleep nights using stated thresholds; and, if multiple mood entries per day are common, comparing daily mean mood with first-entry mood."
        }
      ],
      "proposed_after": {
        "queries": 1,
        "tables": [
          "IHS_2025.FITBITSLEEPLOGS",
          "IHS_2025.GARMINSLEEPSUMMARY",
          "IHS_2025.VW_DAILY_MOOD"
        ],
        "more_tables": 0
      }
    }

## plan_affects (run 1)

> Does sleeping less make interns' mood worse in the 2025 cohort?

- ✓ **type**: Association or estimation (proposed association)
- ✗ **add-ons only where they apply**: at most 2 (proposed: missing_data, repeated_observations, sensitivity, temporal_alignment)
- ✓ **says what claim is intended**: the plan says whether it's a causal claim or an association
- analysis plan declined (HTTP 204)
- 0 queries, 43s

Answer:

    {
      "schema_version": 2,
      "analysis_type": "association",
      "analysis_type_label": "Association or estimation",
      "rationale": "The question asks how nightly sleep relates to mood, so the appropriate first analysis is an observational repeated-measures association rather than a causal effect estimate.",
      "sections": [
        {
          "kind": "question_and_purpose",
          "label": "Question and purpose",
          "content": "Question: In the 2025 IHS cohort, are interns' daily mood scores lower after nights when they slept less? Purpose: estimate the short-term within-person association between wearable-measured sleep duration and daily mood, while avoiding a causal interpretation unless a stronger design is specified."
        },
        {
          "kind": "data_and_scope",
          "label": "Data and scope",
          "content": "Cohort: IHS_2025 only. Population: enrolled participants with at least one daily mood report and at least one wearable sleep observation that can be aligned by participant and date. Metadata checked so far: VW_DAILY_MOOD has PARTICIPANTIDENTIFIER, MOOD_STARTDATE, MOOD_ENDDATE, MOOD_SCORE; FITBITSLEEPLOGS has PARTICIPANTIDENTIFIER, STARTDATE, ENDDATE, MINUTESASLEEP, ISMAINSLEEP and related sleep fields; GARMINSLEEPSUMMARY has PARTICIPANTIDENTIFIER, CALENDARDATE, DURATIONINSECONDS and sleep-stage durations. Join paths show shared PARTICIPANTIDENTIFIER only, so dates must be aligned before joining. I have not yet checked row counts, coverage, value ranges, or which sleep source has sufficient coverage."
        },
        {
          "kind": "checks_and_limitations",
          "label": "Checks and limitations",
          "content": "Before interpreting results I will check participant counts, row counts, duplicate participant-date records, parseability and range of MOOD_SCORE, impossible sleep durations, sleep-source availability, overlap between sleep and mood dates, unmatched rows, missingness, and whether participants have enough within-person variation in sleep to estimate a within-person association. Main limitations: observational association, potential time-varying confounding, self-selection into mood reporting and wearable wear, device/source differences, and residual dependence among repeated observations."
        },
        {
          "kind": "deliverables",
          "label": "Deliverables",
          "content": "A concise static HTML report in /work/outputs plus the complete analysis source in /work. The report will include sample flow, data-quality checks, model results with effect sizes and confidence intervals, sensitivity analyses, and the exact SQL provenance. It will not include participant identifiers or row-level records."
        },
        {
          "kind": "target_quantity",
          "label": "Target quantity (estimand)",
          "content": "Primary estimand: the within-person difference in daily mood score associated with one hour less sleep the preceding night among 2025 interns with aligned wearable sleep and mood data. This supports an association claim only: it will not show that sleeping less causes worse mood. I will also summarize the between-person association separately, because interns who generally sleep less may differ from interns who generally sleep more."
        },
        {
          "kind": "measures",
          "label": "Measures and summaries",
          "content": "Mood: VW_DAILY_MOOD.MOOD_SCORE, using MOOD_STARTDATE/MOOD_ENDDATE for timing after checking that scores are numeric or can be defensibly coded. Sleep: wearable sleep duration in hours. Fitbit candidate is FITBITSLEEPLOGS.MINUTESASLEEP, restricted where appropriate to main sleep episodes using ISMAINSLEEP after checking values. Garmin candidate is GARMINSLEEPSUMMARY.DURATIONINSECONDS or summed stage durations, using CALENDARDATE, after checking validation and ranges. The pilot will determine whether a single primary source or a clearly documented device-source approach is supportable from coverage and comparability."
        },
        {
          "kind": "method",
          "label": "Method and adjustment",
          "content": "Primary model: a repeated-measures regression of mood score on prior-night sleep hours decomposed into within-person deviation from each participant's mean sleep and between-person mean sleep. Include participant fixed effects or equivalent within-person centering for the primary within-person estimate; include day-of-week and calendar-time terms if coverage supports them. Uncertainty will account for repeated observations by clustering at participant level or using a mixed/repeated-measures model with participant-level dependence. Report effect size as mood-score units per 1 hour less sleep with 95% confidence interval, plus descriptive summaries."
        },
        {
          "kind": "repeated_observations",
          "label": "Repeated observations",
          "content": "The unit of analysis is participant-day after date alignment. I will report observations, distinct participants, and participants contributing estimable within-person variation: at least two aligned participant-days with nonidentical sleep duration and nonmissing mood. Multiple sleep or mood records for the same participant-date will be handled by a pre-specified aggregation after duplicate checks: main sleep episode for Fitbit when available, otherwise daily total/longest defensible sleep record; mood records on the same date summarized by mean if the scale supports it."
        },
        {
          "kind": "temporal_alignment",
          "label": "Timing and alignment",
          "content": "Primary alignment: prior-night sleep linked to the next daily mood report by participant and calendar date. For Fitbit, sleep ENDDATE date will be treated as the waking/date-of-mood candidate after timezone-aware date extraction where possible. For Garmin, CALENDARDATE will be treated as the sleep night/date provided by the device; the pilot will verify date behavior. Sensitivity: same-calendar-day alignment and, if feasible, excluding records with ambiguous timing or duplicated dates."
        },
        {
          "kind": "missing_data",
          "label": "Missing data",
          "content": "I will quantify missing mood, missing sleep, and the overlap of available sleep and mood by participant and date. Complete-case aligned participant-days will be used for the primary model; no imputation will be done in the first analysis. I will describe how differential missingness or mood-reporting behavior could bias the association."
        },
        {
          "kind": "sensitivity",
          "label": "Sensitivity analyses",
          "content": "Sensitivity checks will include: shorter-sleep threshold summaries such as <6 hours versus >=6 hours as a descriptive complement, alternate date alignment where defensible, excluding implausible sleep durations using transparent thresholds, and fitting the model separately by sleep source if both Fitbit and Garmin contribute enough participants."
        }
      ],
      "proposed_after": {
        "queries": 0,
        "tables": [],
        "more_tables": 0
      }
    }
