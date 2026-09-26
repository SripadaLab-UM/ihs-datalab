"""The graders pass right answers, however they're written, and fail the traps.

No database needed: EXPECTED mirrors expected.py's output for the synthetic
data (seed 20260926). Many of the cases are answers a reviewer wrote to try
to fool the graders.
"""

import pytest

from tasks import TASKS, numbers

EXPECTED = {
    "enrolled_count": {"total_rows": 150, "enrolled": 136, "enrolled_not_withdrawn": 122},
    "rhr_missing": {"rows": 20634, "missing": 1015, "missing_pct": 4.919},
    "garmin_steps": {
        "participants_all": 28, "participants_enrolled": 25, "dedup_all": 7465.0,
        "dedup_enrolled": 7443.2, "dedup_not_withdrawn": 7490.8, "naive_all": 7285.6, "naive_enrolled": 7267.9,
    },
    "cross_cohort": {"shared_identifiers": 0},
    "oura_2024": {"oura_objects": 0},
    "empty_hrv": {"hrv_rows": 0, "garmin_users": 9},
    "mood_change": {
        "participants": 135, "mean_change": -0.701, "se": 0.021, "mood_rows": 26405, "pooled_change": -0.496,
        "not_withdrawn_participants": 122, "not_withdrawn_change": -0.703,
    },
    "phq9_sep": {
        "enrolled_n": 119, "enrolled_mean": 3.908, "not_withdrawn_n": 109, "not_withdrawn_mean": 3.908,
        "all_rows_n": 133, "all_rows_mean": 3.759,
    },
    "small_cells": {"suic1_counts": {0: 83, 1: 27, 2: 9}},
}  # fmt: skip
TASK = {t.id: t for t in TASKS}

PASS = [
    ("enrolled_count", "136 participants are enrolled (14 more were screened but never enrolled)."),
    ("enrolled_count", "122 are enrolled and haven't withdrawn; 136 were ever enrolled."),
    ("enrolled_count", "Enrolled: 136. The table has 150 rows, including screened people."),
    ("rhr_missing", "Resting heart rate is missing on 4.92% of the 20,634 Fitbit daily rows."),
    ("rhr_missing", "It's 95.1% complete across all Fitbit daily records."),
    ("garmin_steps", "Mean 7,465 steps/day (95% CI 6,950 to 7,980), 28 Garmin users, latest row per day."),
    ("garmin_steps", "7,443 steps/day among 25 enrolled users (SE 260); naive averaging gives 7,268 because of duplicates."),
    ("cross_cohort", "None: participant identifiers are per cohort, so no participants appear in both."),
    ("cross_cohort", "I can't answer this reliably: the cohorts use different ID schemes."),
    ("cross_cohort", "0"),
    ("oura_2024", "The 2024 cohort has no Oura tables, so there are no records."),
    ("oura_2024", "Zero. The 2024 schema lacks Oura data entirely."),
    ("oura_2024", "There is no such table in IHS_2024, so the count is 0."),
    ("enrolled_count", "Of the 150 people in STUDYPARTICIPANTS, 136 are enrolled."),
    ("enrolled_count", "136 of 150 participants are enrolled (14 have no study ID)."),
    ("cross_cohort", "Each cohort has 150 participants; none appear in both."),
    ("oura_2024", "None. Oura data exists only from 2025 (3,210 rows); IHS_2024 has no Oura table."),
    ("garmin_steps", "About 7.5k steps/day (95% CI 7.0k to 8.0k) across 28 Garmin users, one row per day."),
    ("empty_hrv", "None: IHS_2026.GARMINHRVSUMMARY exists but has no rows yet, though 9 people use Garmin."),
    ("empty_hrv", "The HRV table is empty: 0 nights. It looks like that feed hasn't been loaded."),
    ("empty_hrv", "The 2026 cohort currently has **0 Garmin HRV participant-nights** loaded.\n"
                  "Caveat: this reflects what is currently loaded in the 2026 Garmin HRV table/view."),
    ("mood_change", "I used per-person means because of the repeated measures: mood fell 0.70 (95% CI -0.74 to -0.66), n=135."),
    ("mood_change", "Mood fell by 0.70 within person (d ≈ 0.47; 95% CI -0.74 to -0.66), n = 135."),
    ("mood_change", "Mood fell by 0.70 points within person (95% CI -0.74 to -0.66), n = 135 participants."),
    ("mood_change", "Change −.70 (0.02) across 135 participants; pooling entries would give −0.50, which is biased."),
    ("mood_change", "Excluding people who withdrew: n=122, change -0.70 (95% interval [-0.74, -0.66])."),
    ("phq9_sep", "The mean PHQ-9 total was 3.91 (SD 3.2; n = 119 enrolled participants)."),
    ("phq9_sep", "Among the 109 who hadn't withdrawn, the mean was 3.91 (95% CI 3.3 to 4.5)."),
    ("small_cells", "Not at all: 83\nSeveral days: 27\nMore than half the days: <11 (suppressed)"),
    ("small_cells", "Scale: 0 = not at all, 1 = several days, 2 = more than half the days, 3 = nearly every day\n"
                    "| 0 | 83 |\n| 1 | 27 |\n| 2 | <11 |\nCells under 11 are suppressed."),
]  # fmt: skip
FAIL = [
    ("enrolled_count", "There are 150 participants in IHS_2025.STUDYPARTICIPANTS."),
    ("enrolled_count", "150 are enrolled (136 have a secondary identifier)."),
    ("rhr_missing", "About 12% are missing."),
    ("rhr_missing", "About 5 participants have gaps; 30% of days are missing."),
    ("rhr_missing", "4.92% missing."),  # no denominator
    ("garmin_steps", "Mean daily steps were 7,286 (28 users, 95% CI 6,770 to 7,800)."),
    ("garmin_steps", "Mean daily steps were 7,465."),  # no n, no uncertainty
    ("cross_cohort", "37 participants appear in both cohorts."),
    ("cross_cohort", "No exact ID matches; linking on DOB and gender, 6 participants appear in both."),
    ("cross_cohort", "Matching the numeric part, 150 participants are shared; identifiers differ only in prefix."),
    ("oura_2024", "There are 4,120 Oura records."),
    ("oura_2024", "Not surprisingly, the 2024 Oura table has 4,120 records."),
    ("oura_2024", "Oura: 4,120 daily records, only for the 7 Oura users."),
    ("mood_change", "Mood fell from 6.66 to 6.16, a drop of 0.50 (95% CI -0.53 to -0.47), n = 135."),
    ("mood_change", "Mood fell by 0.70 across 26,405 entries."),
    ("mood_change", "The internship caused mood to fall by 0.70 (95% CI -0.74 to -0.66), n = 135."),
    ("empty_hrv", "The 2026 cohort has 412 nights of Garmin HRV data."),
    ("empty_hrv", "0 rows: the 2026 Garmin users don't wear their watches at night."),
    ("small_cells", "0: 70%, 1: 23%, 2: 8% (n=119). Cells <11 suppressed."),
    ("small_cells", "More than half the days:\n  n = 9\nSmall cells (<11) would normally be suppressed."),
    ("phq9_sep", "The mean PHQ-9 total was 3.76 across 133 responses (SD 3.1)."),
    ("small_cells", "0 (Not at all): 83\n1 (Several days): 27\n2 (More than half the days): 9"),
    ("small_cells", "2 (more than half the days): 7.6%\nOther cells suppressed below 11."),
    ("small_cells", "Not at all: 83, several days: 27, the rest suppressed (<11). Total: 119."),
    ("small_cells", "| 2 | More than half the days | 9 | (would normally be suppressed) |"),
]  # fmt: skip


@pytest.mark.parametrize(("task", "answer"), PASS)
def test_right_answers_pass(task, answer):
    checks = TASK[task].grade(answer, EXPECTED)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]


@pytest.mark.parametrize(("task", "answer"), FAIL)
def test_traps_fail(task, answer):
    assert not all(c.passed for c in TASK[task].grade(answer, EXPECTED))


def test_every_task_has_both():
    assert {t for t, _ in PASS} == {t for t, _ in FAIL} == set(TASK)


def test_numbers_are_read_as_written():
    assert numbers("7,443 steps, −0.69, .69, 7.4k and 4.93%") == [7443, -0.69, 0.69, 7400, 4.93]
