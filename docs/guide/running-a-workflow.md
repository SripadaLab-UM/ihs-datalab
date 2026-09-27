---
title: Run a workflow
summary: Choose a workflow, fill in its parameters, run it, and repeat a run with Run again or Replay.
order: 22
screens: /workflows, /workflows/*
keywords: workflow, run, parameters, delivery, deliver, destination, qc, checks, replay, run again, small cells, save as workflow
---

# Run a workflow

A [workflow](glossary.md#workflow) is a recipe saved to the lab's
`ihs-pipelines` repository after review: pull these data, run these steps,
check these things, deliver here. It runs when a person presses **Run**. DataLab runs
it the same way every time, with no AI involved, so a workflow run never
sends data to a model.

## Run one

1. Open the **Workflows** tab. Each workflow file is listed with whether it
   passes its checks and when it last ran.
2. Choose a workflow. Its page shows the file, with any problems marked, and
   a form for its parameters, such as a start and end date.
3. Fill in the parameters and press **Run**.
4. Watch each step as it runs. **Stop** works until delivery has started;
   after that, files may already be in the destination.

Where each step runs:

- **SQL** steps go through DataLab's data service, read-only, and may read
  only the tables the workflow lists.
- **R** and **pipeline** steps run in a container with no network. They see
  their inputs read-only, and only their declared outputs come back.
- **Checks** (row counts, required columns, unique keys,
  [small cells](glossary.md#small-cells)) are DataLab's own code.

## Delivery

Files are delivered only once every step and check has passed. A failed
check stops the run before anything leaves. The workflow names a
destination, such as `lab-dropbox`, and each computer chooses the folder it
means in **Settings & Safety → Workflow destinations**. Until a folder is
chosen, a run that delivers there fails at delivery. The practice DataLab
delivers only to its own practice folder.

In the real DataLab, every delivered CSV needs a passing `small_cells` check
or a written reason under `deliver.without_small_cells` in the workflow file,
and any other kind of file needs a written reason there. The check
can't see everything (differencing between two delivered files, for example),
so read what a workflow delivers before relying on it.

## The run record

Each run keeps what's needed to reproduce it: the workflow file and the
exact commit, the parameters and each SQL statement, the data it extracted,
the checks' results, checksums of its outputs, and where they were
delivered. Runs stay on your computer; only workflow files go to GitHub.

## Run again or Replay

The database keeps changing while cohorts are ongoing, so there are two ways
to repeat a run:

- **Run again** runs the current file with the same parameters, extracting
  afresh from today's database. Its results can differ.
- **Replay** reruns the processing on the data the run extracted then,
  to reproduce its results exactly. DataLab first says whether a
  [replay](glossary.md#replay) can be exact and, if not, why. Afterwards it
  records whether every output matched the original. A replay doesn't
  deliver unless you ask, and asks again first.

## Make a workflow

- **From the SQL Playground:** **Save as workflow** drafts a workflow from
  the current query.
- **From a conversation:** **Turn this into a workflow**, in the **Queries**
  tab, drafts one from the queries that ran.

DataLab writes the draft itself, with no AI and no data. You review and edit
the file, with its problems marked, before anything is saved. Each possible
participant-data finding, such as an ID typed into the SQL, must be confirmed
first. Where the workflows come from the lab's `ihs-pipelines` repository,
saving is [Save & share](glossary.md#save--share), and the package's tests
must pass. Editing an existing workflow file isn't in the tab yet.
