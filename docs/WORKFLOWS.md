# Workflows and pipelines

Status: **draft** for v1. The runner, run records, Run again, Replay and
delivery are built (milestone 6 core; see [As built](#as-built-milestone-6-core)),
and so are the Workflows tab, read-only for now, and the Pipelines tab
([As built](#as-built-pipelines)).

Workflows are how DataLab handles bespoke, repeatable data work. Examples are
Yu's regular exports to Dropbox, the 2025 daily wearable metrics, and next,
SensorKit preprocessing.

## The idea

> **The agent helps build it. A person approves it. DataLab then runs it the
> same way every time, with no AI involved.**

This has three benefits:

- Runs are **reproducible**. The same definition and the same inputs produce
  the same outputs.
- Runs are **safe**. A workflow run never sends data to a model.
- New requests need **no new app code**. A new request becomes a new pipeline
  plus a new workflow file, not a new feature in DataLab.

## Two kinds of thing

| | **Pipeline** | **Workflow** |
|---|---|---|
| What it is | Reviewed, tested R code that does the heavy transformation. Example: `daily_metrics_2025` combines Fitbit, Garmin, and Apple Watch into daily steps, RHR, sleep, HRV, and active minutes | A runnable recipe: pull these data, run these steps, check these things, deliver here |
| Where it lives | The `ihsDataR` R package, in the lab pipelines repo | A small YAML file, in the same repo |
| Who owns it | Yu, and whoever works on the package | Anyone in the lab |
| Edited in | **Pipelines** tab, with the Data engineering agent | **Workflows** tab, with the Workflow authoring agent, or by hand |

Many workflows need no pipeline at all. For example, "export Fitbit daily
data for these dates, drop the body-composition columns, and deliver to
Dropbox" is one SQL step and a few lines of R.

## Lab pipelines repo (`ihs-pipelines`)

Pipelines and workflows sit in **one GitHub repository**. Because of that, a
workflow and the code it calls are always from the same commit, and one change
can update both.

```
AGENTS.md                 conventions for agents working in this repo
ihsDataR/                 the R package: R/, inst/pipelines/, tests/
workflows/
  fitbit_daily_2025.yaml
  daily_metrics_2025.yaml
  …
reference/                legacy code by cohort, e.g. reference/2024/
                          AggregateDailyMetrics_2024.R and the ODBC export
                          scripts. Agents consult it for preprocessing advice
                          on older years
.github/workflows/        R tests and workflow-file checks on every push
```

`ihsDataR` moves out of the app repo into this one. The package's backlog
and parity notes move with it.

**Cohorts.** Workflows can use any cohort schema, IHS_2017 through
IHS_2026. `ihsDataR` is currently the 2025 build. Earlier years are served by
direct SQL plus the legacy code in `reference/`. Making pipelines take a
cohort parameter comes later, when a second year needs one.

## A workflow file

```yaml
name: fitbit_daily_2025
description: Fitbit daily data for the 2025 cohort, without body-composition columns.
parameters:
  start_date: { type: date, default: 2025-04-01 }
  end_date:   { type: date, default: 2026-01-01 }

steps:
  - id: extract
    sql: |
      SELECT * FROM IHS_2025.VFITBITDAILYDATA
      WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND RECORD_DATE <  TO_DATE(:end_date,   'YYYY-MM-DD')
    output: fitbit_daily_raw.csv

  - id: clean
    r: |
      x <- read.csv(inputs$raw, check.names = FALSE)
      drop <- c("BODYBMI", "BODYFAT", "BODYWEIGHT", "WATER")
      write.csv(x[, setdiff(names(x), drop)], outputs$final, row.names = FALSE)
    inputs:  { raw: extract }
    output: fitbit_daily_2025.csv

  - id: check
    qc:
      file: clean
      min_rows: 1
      required_columns: [STUDY_PARTICIPANT_ID, RECORD_DATE]
      unique_by: [STUDY_PARTICIPANT_ID, RECORD_DATE]

deliver:
  destination: dropbox-ihs-2025        # a named folder configured in Settings
  folder: fitbit_daily_2025
  files: [clean]
```

A pipeline step looks like this:

```yaml
  - id: metrics
    pipeline: daily_metrics_2025   # runs ihsDataR/inst/pipelines/daily_metrics_2025/run.R
```

The pipeline declares which Oracle objects it reads. DataLab extracts those
objects first, then runs the pipeline.

### Step types

- **`sql`**: a read-only query through DataLab's data service. The full result
  is saved as a CSV.
- **`r`**: a short inline R script.
- **`pipeline`**: runs a pipeline from `ihsDataR`.
- **`qc`**: built-in checks, which run as DataLab's own code: row counts,
  required columns, no missing values, and unique keys. There is also an
  optional **custom R check**, which runs in the no-network container like any
  other R step. A failure stops the run before anything is delivered.

- **`deliver`** happens once, at the end, and only after every QC check has
  passed. It copies files to a configured destination folder, such as
  Dropbox, in a dated subfolder with a manifest.

## Running

- Run from the Workflows tab: choose a workflow, fill in the parameters, and
  click **Run**. Progress appears step by step.
- **Run a set:** run several workflows in order with shared parameters, such
  as "all 2025 exports for this quarter". One failure doesn't stop the others.
- Where steps run:
  - SQL steps run through the host data service. The database password is
    never exposed.
  - R, pipeline, and custom QC steps run in a container with **no network**.
    Their inputs are mounted read-only, and only the declared outputs come
    back.
- Each run keeps a **run record** with everything needed to reproduce it:
  - the workflow file and exact repo commit;
  - the agent image digest;
  - the parameters, and each SQL statement with its bind values;
  - random seeds;
  - the **extracted inputs** themselves, kept in the run folder;
  - output checksums, QC results, and where files were delivered.
- **Two ways to repeat a run.** The database keeps changing, since cohorts
  are ongoing, so these are different things:
  - **Run again** re-extracts from today's database, giving new results.
  - **Replay** reruns the processing on the original extracted inputs,
    reproducing the original results exactly.
- Run history and outputs stay on the user's machine. Only workflow
  *definitions* go to GitHub. Runs contain study data; definitions never do.

## Authoring

Every route ends at a person reviewing the file, doing a test run, and
saving:

- **By hand:** a step-by-step form in the Workflows tab, with a YAML view for
  those who want it.
- **From SQL Playground:** "Save as workflow" turns the current query into an
  extract-and-deliver workflow.
- **From a conversation:** "Turn this into a workflow" makes the agent draft
  a workflow from the SQL and scripts it actually ran in that conversation.
- **From a legacy script:** attach an old R script, such as Yu's ODBC export
  script, and the authoring agent splits it into workflows.

Saving uses the same **Save & share** flow as the knowledge base: git history,
attribution, and revert.

## Pipelines tab

- Browse `ihsDataR`: files, functions, pipelines, and tests, with an R code
  viewer.
- Chat in Data engineering mode. The agent works in a copy of the repo, runs
  the tests in its container, and hands back a proposed change.
- Review the change as a diff, see the test results, then save it.
- Run a pipeline ad hoc against live data. This is a one-off run of its
  workflow.

## How new requests fit

Each bespoke request, such as SensorKit preprocessing, follows the same path:

1. **Explore** in a Data engineering conversation to understand the data.
2. **Build** the pipeline code in `ihsDataR`, with tests.
3. **Wrap** it in a workflow for repeatable runs and delivery.
4. **Record** what was learned in the knowledge base: recipe, QC rules,
   quirks, and verified queries.

## Release check against the prototype

Before v1 ships, the 8 default workflows and `daily_metrics_2025` are run in
both the prototype and v1 on the same inputs, and their outputs must match.
This is a one-off validation, not a user-facing feature.

## Carried over from the prototype

- The 8 default routines, converted to workflow files.
- The step model, QC checks, per-step progress, and the no-network R sandbox
  settings.
- The five 2025 daily-metric pipelines, with their tests and parity backlog.

Dropped: local-only definition storage and its versioning tables, bundle
import/export, the default-seed ledger, and the provenance-review manifest.
Git replaces all of these.

## Decided

- Pipeline code changes are saved directly after review and passing tests,
  like knowledge-base edits. There are no pull requests for now.
- No compare step in v1.

## As built (milestone 6 core)

The backend is in `backend/src/datalab/workflows/`, with its routes under
`/api/workflows`. The design and its evidence are in the runner spike
(`spikes/2026-09-27-workflow-runner/README.md`).

**Where files come from.** The synced `ihs-pipelines` clone
(`<data folder>/repos/ihs-pipelines`) when `[repos] pipelines` is set and
it has been synced; `[workflows] folder` in `settings.toml` overrides it,
and otherwise `<data folder>/workflows-local/` (`workflows_folder` in
workflows/source.py). Until the clone's first sync, files come from
`workflows-local`, and `GET /api/workflows/status` says so. The folder is
laid out like `ihs-pipelines` (`workflows/*.yaml`, `ihsDataR/`), or holds
the YAML files directly.

**Each run is pinned.** When a run starts, its workflow files and the
package are copied into its run folder (`source/`), and the run reads only
that copy. From the clone, the copy is GitHub's `main` as last synced,
taken under the clone's lock, so a Sync or Save & share during the run
can't give it a package from another commit; the run records that commit.
From another folder, the copy is the run's own workflow file and the
package, within the package's limits (5000 files, 50 MB): a larger package
isn't copied, and only pipeline steps are refused, saying why.

**What the file check adds to the example above:**

- `schema_version: 1` (optional; other versions are refused).
- **`reads:`** lists every Oracle object the workflow reads, as
  `SCHEMA.OBJECT`. Every object a SQL step names must be in it (joins,
  subqueries; CTE names don't count), a pipeline's own `reads:` must be in
  it, and every entry must be used. At run time the list goes to the data
  service as the only tables its SQL may read.
- Step ids and output files are lower case. A step with `output:` has one
  output, `final` in R (`outputs$final`); `outputs: {name: file}` gives
  several, referred to as `step.name`. References point at earlier steps.
- Every SQL bind (`:start_date`) must be a declared parameter.
- `small_cells` takes `count_columns` (or `count_column`), `min` (11 by
  default, or `$param`), and optionally `totals: {column, value, within}`
  (total rows, matched ignoring case and spaces), `total_column` (a row
  total), and `percent_columns: {pct: count}`. `min` can't be below 11 in
  either profile, so a parameter can't turn the rule off. It fails:
  - a shown count from 1 to `min - 1` (0 may be shown);
  - a `totals` value no row has (a misspelt "Total" would otherwise check
    nothing), or a header that names a column twice;
  - a hidden count that the shown totals give away. Every row total and
    group total is an equation over the hidden cells, each of which starts
    at 0 to `min - 1` (R's usual `ifelse(n < 11, NA, n)` hides zeros too).
    The check solves them together: exact elimination finds cells the
    equations fix on their own, and interval propagation tightens every
    cell's range across all rows and columns until nothing changes. A
    hidden cell left one value from 1 to `min - 1` fails, as do hidden cells
    under one total whose combined count is small; one left only 0 passes,
    and the check's message says so. If the equations need a hidden cell of
    `min` or more (a large cell hidden to protect a small one), only the top
    of the ranges is widened, to 0 and up; if the totals can't add up at
    all, the check fails. A seeded random search checks it against brute
    force over small tables;
  - a hidden count whose declared percentage is shown in the same row,
    whether or not the base is shown.
- **What the small-cell check doesn't cover**, which needs a person's
  review before delivery:
  - totals across `within` groups, or nested more than one level;
  - totals in another output: differencing between delivered tables, where
    two files' counts give a hidden one away;
  - percentages not declared under `percent_columns`, and means or rates
    that imply a count;
  - reasoning cleverer than elimination and propagation: the check is sound
    but not complete (it doesn't search every whole-number solution, and a
    group of more than 400 linked hidden cells gets the propagation only).
- In the real profile, every delivered CSV needs a passing `small_cells`
  check over that exact output, and every other delivered file (TSV, Excel,
  …), which built-in checks can't read, needs a reason under
  `deliver.without_small_cells: {<output>: <why>}`. A reason is at most 300
  characters, is refused if it looks like it holds an identifier, a date or
  an email (the export folder-name scan), and is recorded in the delivery
  manifest.
- A pipeline's `inst/pipelines/<name>/pipeline.yaml` has `reads:` with
  `columns` and `where:` (or `whole_table: true`), `parameters`, and
  `outputs`. DataLab extracts those objects into `/run/in/oracle/` before
  the step, and builds the package once per source tree in the sandbox.

**Step containers.** `/run/out` and `/run/result` are writable bind mounts
without `noexec`, so a step can write a program there and run it; that is no
more than its R code can do anyway, with no network and no capabilities. The
disk cap covers the whole step folder.

**Stop** is refused once a run's delivery has started: `export()` runs in a
thread, so files may already be in the destination. A shutdown waits for the
delivery to finish, however long it takes, and it is recorded either way. If DataLab
crashes during a delivery, the next start records it as failed with an
unknown outcome: check the destination.

**Destinations.** `deliver: destination:` is a key each computer maps to one
of its export folders (`export_destinations.key`, set with `PUT
/api/workflows/destinations/<key>`). The practice profile always delivers to
its own practice folder.

**Run again** runs the current file with the original parameters and seed,
extracting afresh. **Replay** reruns the kept definition, extracts, image,
seed and pipeline library; `GET /api/workflows/runs/<id>/replay` says first
whether it can be exact, and why not (the image gone, another platform,
DataLab's step wrapper or version changed). It records afterwards whether
every output matched byte for byte, and doesn't deliver unless asked.

**The Workflows tab** lists the folder's files with their checks (each
problem with its path and line) and last run. A workflow's page shows the
file read-only, with its problems marked in the editor, a form built from
its parameters, and Run, followed live (`/runs/<id>/stream`) with Stop. A
run's page shows its steps, what it pinned, its delivery, Run again, and
Replay: the replay check's reasons come first, an inexact Replay needs a
tick, and a Replay that delivers is asked about a second time. A custom
check's found and wanted values, and a step's counts, are kept as numbers
only, so text an R check wrote can't carry a value into the record or the
tab. Destination
keys are listed read-only; folders are chosen in Settings. New files come
from Save as workflow ([As built](#as-built-save-as-workflow)); editing an
existing one isn't in the tab yet. The docked chat opens in Workflow
authoring mode (sessions/modes.py): the agent drafts in its copy of the repo,
checks each draft with the `check_workflow` tool (the same check as the tab
and every run, with pipelines as on `main`), and its changes become a
Pipelines proposal to review and save there.

## As built (Pipelines)

The backend is in `backend/src/datalab/pipelines/`, with its routes under
`/api/pipelines`; the tab is `frontend/src/features/pipelines/`.

**The repo.** `[repos] pipelines` is cloned and synced like the knowledge
base (`repos/sync.py`, with milestone 5's isolation: no global or system
config, every filter and driver switched off, `--no-ext-diff`). There's one
GitHub sign-in for both repos; signing in is in Settings → GitHub. The tab
browses GitHub's `main` as last synced, read-only.

**Changes.** Each new Data engineering or Workflow authoring conversation
gets its own copy of the repo at `/work/pipelines` (without `.github/`, and with no repository
metadata or credentials). After each turn, what the agent changed in
`ihsDataR/` and `workflows/` becomes one proposal, replacing any earlier
one still open; changes anywhere else in the copy, and anything in
`.github/`, are listed as not proposed. Proposals are shown in the
Pipelines tab only, for now (not as cards in the chat).

**Tests.** The package's tests (`testthat::test_local`) run on the
proposal's exact tree, in the workflow sandbox's container: no network, a
read-only root, the agent image, and a working folder of their own where
compiled code can run (the steps' `/tmp` stays noexec). Two run at a time
at most. The counts, the failing tests, and the log are kept against that
tree (migration 0010). They're a quality check, not a safety gate: the
counts are written by the same R process as the code under test, which
could write any counts it likes. What keeps a change safe is the sandbox
and the person reading the diff.

**The check** (pipelines/check.py) blocks on paths that can't be changed
(anything outside `ihsDataR/` and `workflows/`, or in `.github/`), and asks
the person to confirm, one by one, with the line shown:

- possible participant data: the knowledge base's scan, plus every changed
  data file (CSV and the like, fixtures included), column names that look
  like identifiers (`id`, `mrn`, `dob`, `subject`…), and short numbers next
  to dates, which the knowledge base's scan lets through;
- code that runs outside the test container once shared: `.Rprofile`,
  `configure`, `cleanup`, compiled code in `src/`, and `.onLoad`,
  `.onAttach` and the like in `R/`.

**Save & share** runs the check, requires the tests to have passed on the
change (running them first if not), commits it as the person, rebases onto
`main`, runs the tests again when the rebased package (the `ihsDataR` tree)
isn't the one they passed on, and pushes exactly that commit, whose message
names the test run that passed on it. A conflict shares nothing: discard the
change and ask the agent to make it again. The change isn't edited in
DataLab: ask the agent.

Not built yet: the ad hoc run of a pipeline's workflow, and the converted
`ihsDataR` itself (its pipelines have no `pipeline.yaml` yet).

## As built (Save as workflow)

The drafting is `backend/src/datalab/workflows/drafts.py`, the routes
`POST /api/workflows/drafts` and `/saves` (api/workflows.py), and the dialog
`frontend/src/features/workflows/SaveAsWorkflow.tsx`.

**The draft.** DataLab writes it itself from the SQL and binds, with no
model and no data. A query the SQL check refuses (with the catalog, as in
the Playground) makes no draft. Each query becomes a SQL step; `reads:`
comes from the same analysis as the file check's `reads:` rule.

- **Parameters.** Each bind becomes a lower-case parameter. Its type comes
  from the catalog type of the column it's compared with (a number or a
  date; a bind inside TO_DATE is a date); otherwise it stays text, as the
  Playground sent it, so `'12'` against a VARCHAR2 column isn't turned into
  a number. The value it ran with is kept as the default only for a date,
  or a number compared as a range (`>`, `BETWEEN`) or named as a limit
  (`min_…`, `max_…`). Text never is, and nothing is when the bind's name,
  or the column it's compared with, looks like it's about a person (the
  Save & share check's identifier words, plus `identifier`, `birth`,
  `name`, `postal` and the like: `:dob`, `PARTICIPANTIDENTIFIER = :p`,
  `LASTNAME = :n`). A note says why each other one has no default.
- **Checks.** Each step gets `min_rows: 1`, the named output columns (not
  after `*`), and, when the query aggregates (GROUP BY, or an aggregate
  outside a WHERE), `small_cells` with `min: 11`. It covers `COUNT(…)`, and
  `SUM` of 1s and 0s (`SUM(CASE WHEN … THEN 1 ELSE 0 END)`) as counts, and
  every other aggregate too (`SUM(x)`, `AVG`, `MAX`), with a note: DataLab
  can't tell that one isn't a count, so it fails closed. When an aggregate
  has no name, or the counting happens in a subquery, the list is left
  empty and the file check asks the person for it.
- **Delivery.** With a destination key, `deliver:` delivers every output.
  In the real profile a delivered row-level extract then needs a reason
  under `deliver.without_small_cells`, written by the person.
- **The data check.** The draft gets Save & share's check
  (pipelines/check.py) for possible participant data, such as an id typed
  into the SQL. Each finding must be confirmed before saving, wherever it's
  saved, and one edited away no longer counts.

**Review.** The dialog shows the YAML in the editor, editable, with the
file check's problems marked (checked again after each edit) and the
draft's notes. Only the person's Save saves anything; drafting saves
nothing, and no agent tool reaches these routes. Escape doesn't close a
review being saved, and asks first when it's been edited.

**Saving.** Where the Workflows tab's files are the synced pipelines clone,
Save is the Pipelines tab's Save & share (`Pipelines.share_workflow`, the
same `share.save_and_share`): the check, the package's tests on the
change's tree, a commit as the person with `DataLab-Workflow-From` (and the
conversation) in its message, rebased and pushed. Possible participant
data waits for the person to confirm each finding. A file already in the
repo isn't replaced (whatever its case, or as `.yml`); if someone saves the
same name meanwhile, nothing is shared, and if they saved exactly this
file, the dialog says it was already there rather than that it was shared. Otherwise (practice, no `[repos] pipelines`, or a
`[workflows] folder`) the file is written into that folder, never over
another, and the dialog says it isn't shared. With the repo configured but
not yet synced, Save waits for a sync.

**From a conversation.** Turn this into a workflow is in the Queries panel:
the person picks among the queries that ran (each SQL once), and the draft
goes through the same review and Save. It takes the SQL only: the
workspace's R scripts read `/data/oracle` files and outputs by their own
paths, not a workflow's `inputs`/`outputs`, so turning them into R steps
needs the Workflow authoring agent (not built).

## Open questions

- Scheduling. v1 is **on-demand**: a workflow runs when someone clicks Run.
  Scheduled runs, such as "every Monday 7am", can be added later if needed.
  They would only happen while DataLab is open and the laptop is awake and on
  the VPN.
