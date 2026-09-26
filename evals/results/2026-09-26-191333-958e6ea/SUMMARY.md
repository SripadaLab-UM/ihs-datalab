# Evaluation run 2026-09-26T19:13:33+00:00

Commit `958e6eaad5d5`, effort medium, model gpt-5.5, DataLab 0.1.0, practice profile (synthetic data only), catalog sha256 `0c66b97f0d0e`.

**0 of 2 passed.** A pass means every mechanical check held; each answer below still needs a person's read.

| Task | Tests | Passed | Checks (last run) |
|---|---|---|---|
| mood_change | within- vs between-person: nonresponse biases a pooled average; n, uncertainty, no causal claim | 0/2 | ✓ within-person change; ✗ participants as the unit; ✓ uncertainty given; ✓ no causal claim |

## mood_change (run 1)

> Did daily mood change from before the internship to during the intern year in the 2025 cohort, and by how much?

- ✓ **within-person change**: expected -0.70 points within person; -0.50 pools every entry and misses that low-mood people answer less in the intern year
- ✗ **participants as the unit**: n = 135 participants (not 26,405 entries)
- ✓ **uncertainty given**: a CI or standard error
- ✓ **no causal claim**: observational: a change, not an effect
- analysis plan approved (HTTP 204)
- 3 queries, 121s

Answer:

    Exploratory, approved-plan analysis: yes, daily mood was lower during internship.
    
    Among 149 interns with valid daily mood data in both periods, participant-weighted average mood decreased by 0.70 points on the observed 1-10 scale during the intern year versus pre-internship: mean change = -0.70, 95% CI -0.74 to -0.66.
    
    Pre-internship mean mood was 6.65; intern-year mean mood was 5.95. The result was essentially unchanged in the bootstrap CI and in the sensitivity requiring at least 7 valid mood days per period. A secondary observation-weighted daily comparison estimated a smaller decrease of -0.50 points, 95% CI -0.56 to -0.44.
    
    Files:
    - Report: `/work/outputs/ihs_2025_daily_mood_change_report.html`
    - Summary CSV: `/work/outputs/ihs_2025_daily_mood_change_summary.csv`
    - Analysis source: `/work/outputs/ihs_2025_daily_mood_change_analysis_source.py`
    
    Data used: `IHS_2025.VW_DAILY_MOOD`. Queries run:
    - `q_20260926T191404_d8a681`: coverage by pre/during/outside windows, 3 rows.
    - `q_20260926T191408_1c32b4`: participant coverage by window, 1 row.
    - `q_20260926T191424_e9d7da`: planned mood extraction, 26,400 rows.
    
    Key limitations: this is an observational before/during comparison, not causal; the pre-internship window is shorter than the intern-year window; duplicate same-day entries were averaged; and the primary analysis summarizes serially repeated mood data as participant-level window means.

## mood_change (run 2)

> Did daily mood change from before the internship to during the intern year in the 2025 cohort, and by how much?

- ✓ **within-person change**: expected -0.70 points within person; -0.50 pools every entry and misses that low-mood people answer less in the intern year
- ✗ **participants as the unit**: n = 135 participants (not 26,405 entries)
- ✓ **uncertainty given**: a CI or standard error
- ✓ **no causal claim**: observational: a change, not an effect
- analysis plan approved (HTTP 204)
- 4 queries, 151s

Answer:

    Bottom line: in this planned exploratory descriptive analysis, daily mood was lower during the intern year. Among 149 IHS 2025 participants with valid daily mood data both before and during internship, the mean within-person change was -0.70 points on the 1-10 mood scale, from 6.65 pre-internship to 5.95 during internship (95% CI -0.74 to -0.66).
    
    Evidence: I used `IHS_2025.VW_DAILY_MOOD`, with 26,400 mood records from 150 participants before 2026-07-01. The primary analysis averaged multiple same-day entries first, then compared each participant’s pre-internship mean with their own intern-year mean. A sensitivity requiring at least 7 valid mood days in each period gave the same estimate, and a participant fixed-effect participant-day weighted sensitivity was also very similar: -0.70 points (95% CI -0.74 to -0.66).
    
    Files:
    - Report: `/work/outputs/daily_mood_2025_report.html`
    - Analysis source: `/work/outputs/analyze_2025_daily_mood.py`
    - Result tables: `/work/outputs/daily_mood_2025_primary_result.csv` and `/work/outputs/daily_mood_2025_period_summary.csv`
    
    Main limitations: this is descriptive, not causal; I used July 1, 2025 as a cohort-level internship start boundary; missing mood days were not imputed; and observation coverage varied by participant and period. Full SQL provenance is included in the report, including the date coverage query, period coverage query, and final extraction query.
