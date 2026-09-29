# Scientific evaluations

Questions with known answers on the **synthetic** database, asked through
DataLab the way a researcher would, and graded against answers calculated
independently (plain SQL in `expected.py`, not DataLab and not the agent).

Each task is built around a trap the synthetic data carries on purpose
(see `synthetic/README.md`, "Quirks included"):

| Task | Tests |
|---|---|
| `enrolled_count` | Screened-but-not-enrolled rows (a denominator) |
| `rhr_missing` | Missingness: the share, and what it's a share of |
| `garmin_steps` | Superseded duplicate rows (one row per participant-day); n and uncertainty |
| `cross_cohort` | Identifiers that are per cohort (no false joins) |
| `oura_2024` | A table the cohort doesn't have (no invented numbers) |
| `empty_hrv` | A table that exists but is empty (missing data, not a finding) |
| `mood_change` | Within- vs between-person: low-mood interns answer less, so a pooled average understates the drop; n, uncertainty, no causal claim |
| `phq9_sep` | Who counts as a participant in a survey; uncertainty |
| `small_cells` | A count under 11 that must be suppressed (directly, as a percentage, or by subtraction) |
| `person_days` | Text timestamps, several a day: a person-day is a participant and a date, under a stated day rule, not a distinct timestamp |
| `cohort_coverage` | One cohort for numerator and denominator: people with data outside it, and a participant twice in the cohort's view (no duplicate-join inflation) |
| `coverage_open` | The same, with the cohort left to the agent |
| `clob_choices` | Grouping a CLOB column (the dictionary's ANSWERCHOICES) |
| `bdate_age` | A mixed-case quoted column (`"Bdate"`) in an age calculation |
| `plan_describe` | A descriptive question gets a describe plan, not an invented exposure and outcome |
| `plan_coverage` | A coverage audit gets a data-quality plan |
| `plan_prediction` | A prediction question gets a prediction plan (validation, what's known when) |
| `plan_mixed` | A within-person association, with timing and repeated observations as add-ons |
| `plan_affects` | An "X affects Y" question: the intended claim made explicit in the plan, or asked about |

The `plan_` tasks grade the plan the agent proposes, not an answer (DataLab's
own checks already make it well formed; these check it's the right kind).
Each also checks its add-on sections are ones that can apply to the question:
none about comparing cohorts for a one-cohort question, and no "Pilot, then
full run" that only restates Analysis mode's usual pilot. It can't judge
whether an add-on that could apply was needed (association plans often use
all four that can): that's for the person reading the plans.
The runner sends the plan back unapproved and stops the turn once it's
proposed, so each takes a minute or two.

What the agent can know is only what DataLab shows it. The synthetic
database documents its conventions the way a documented database would,
as column comments (a NULL `SECONDARYIDENTIFIER` means screened, never
enrolled); for the real database these belong in the lab knowledge base.

Still to add: a between-person question with a null answer ("do people who
walk more report better mood?", where the person, not the day, is the
unit), and cross-cohort pooling across schema drift.

## Running

```sh
uv run --project backend python evals/run.py            # all tasks, ~20-40 min
uv run --project backend python evals/run.py --only mood_change
```

Needs the synthetic database (`synthetic/db.sh start`), Docker, and a saved
U-M GPT key. The answer key and the eval DataLab use the database on
`DATALAB_PRACTICE_DB_PORT` (default 1522), as practice DataLab does. The runner starts its own DataLab, on port 8767 (`--port` for another) with a fresh
data folder (removed afterwards; `--data-root` says where to make it) and a catalog rebuilt from the synthetic
database, so eval conversations never mix with yours. A practice DataLab can
keep running alongside: each instance labels its containers with its data
folder and cleans up only its own. `--model gpt-5.6-sol` evaluates another
approved model. `--repeat N` runs each task N times, for pass rates. Plans the agent proposes are approved as written; research-helper
questions are declined; the rigor review is off, so each answer is graded
as given. For a `plan_` task the first plan is sent back instead, and the
turn stopped.

Results go to `results/<date>-<commit>/`: `SUMMARY.md` (every answer, with
its checks), one JSON file per task, the expected answers, and the run's
details (commit, model, DataLab version). Commit them.

## What a pass means

The checks are mechanical and narrow: the right number within a stated
tolerance, the denominator, a stated uncertainty, a suppressed cell. A pass
is necessary, not sufficient; a person reads every answer in `SUMMARY.md`
and notes anything the checks can't see (wrong reasoning that lands on the
right number, a claim stated more firmly than the evidence allows).

`test_graders.py` checks that each grader passes right answers, however
they're written, and fails the traps. `expected.py --check` checks that each
trap still separates right from wrong on freshly generated data. Both run in
CI; the evals themselves don't (they call the model).

**Re-run the set** whenever the prompts, skills, the model, or the Codex
version change, and compare with the last committed run.
