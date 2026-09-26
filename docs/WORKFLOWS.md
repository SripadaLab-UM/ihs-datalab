# Workflows and pipelines

Status: **draft** for v1. This is a proposal under discussion and has not been
implemented.

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

## Open questions

- Scheduling. v1 is **on-demand**: a workflow runs when someone clicks Run.
  Scheduled runs, such as "every Monday 7am", can be added later if needed.
  They would only happen while DataLab is open and the laptop is awake and on
  the VPN.
