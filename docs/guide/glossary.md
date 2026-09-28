---
title: Glossary
summary: What DataLab's own words mean, in a sentence or two each.
order: 90
keywords: terms, words, definitions, meaning, vocabulary
---

# Glossary

The first paragraph under each term is also its tooltip in DataLab.

## Data session

A conversation whose agent can query the study database, read-only, and
can't reach websites. Its only outside connection is U-M GPT, the model
service approved for study data.

You choose the kind of session when you start a conversation, through its
[mode](#mode), and it can't change afterwards. A data session can read files
you attach, read-only. Files can come into a data session from a research
session, but nothing goes the other way: the only way data leaves DataLab is
when you [export](#export) it. To look something up on the web, the agent
can ask the [research helper](#research-helper), and only with your approval.

## Research session

A conversation whose agent can use the web, but has no connection to the
study database. Anything you attach to it may reach the internet.

Use it for literature, methods and trying new packages. DataLab can't know
what an attached file contains, so it makes no promise about what's in one:
attach only what may go online. Notes and code from a research session can be
brought into a data session; nothing moves from a data session into a
research session.

## Mode

What a conversation is for, such as Analysis, Data extraction, Data
engineering or Research. It sets the agent's instructions and the suggested
questions, and decides whether it's a data or a research session.

A mode is a starting point, not a restriction. Analysis, Data extraction and
Data engineering are [data sessions](#data-session); Research is a
[research session](#research-session). Two more open only from the tab that
docks them: Workflow authoring (Workflows tab) and Knowledge writing
(Knowledge tab), both data sessions.

## Plan

A short written plan the agent proposes before it looks at outcome data. You
edit and approve it; it's then frozen with a time and a fingerprint, and
later work is labelled as following it or exploratory.

A plan is a record of what you agreed to do, not a lock: nothing stops the
agent running an off-plan query. It's asked to label such work "exploratory
(off-plan)", and the rigor review checks that it did. A plan that passes
DataLab's checks is well formed; that doesn't make the analysis right. See
[Analysis plans and pilots](plans.md).

## Pilot

A small first run of an analysis on part of the data, to check that the
extraction, checks and method work before the full run. In Analysis mode the
agent runs a pilot, reports what it found, and asks you before scaling up.

Pilot results are preliminary. Pilots come from the agent's instructions, not
from a rule DataLab enforces, so the decision to scale up is yours. See
[Analysis plans and pilots](plans.md#pilots).

## Rigor review

A second pass after the answer, in which the same model checks its own work
against a checklist. It's a second opinion to prompt your own judgement, not
proof: its findings can be wrong in either direction.

The **Rigor review** switch is in the header of a data session. It's on by
default in Analysis mode and off in the other modes, and it roughly doubles
the time and cost of each answer. The checklist asks whether numbers are
traced, the plan was followed and off-plan work labelled, causal language is
justified, sample sizes are given before and after exclusions, estimates come
with uncertainty, the right measures were used, no group of fewer than 11
participants is shown or can be worked out, and nothing is overstated. See
[Read an answer](reading-an-answer.md#the-rigor-review).

## Trace and provenance

The trace is DataLab's own check that each number in an answer appears in
that turn's query results, command output, or data files: a match, not proof
it's right. The rigor review is the agent checking itself, so they can differ.

Provenance is the chain behind a result: click a number to see where it
appears, or open a file and choose **How was this made?** to see the turn,
commands, scripts and queries behind it. Neither shows any data. See
[Read an answer](reading-an-answer.md).

## Checkpoint

A copy of the conversation's workspace files that DataLab saves after every
turn, where the agent can't change it. From **History** you can put the files
back as they were after any turn.

Restoring puts back files only: the conversation isn't rewound, and the agent
is told on its next turn. DataLab saves the current files first, so a restore
can be undone. Very large files aren't checkpointed; the restore screen lists
any that can't be put back.

## Read-only

Something the agent can read but not change. The study database is read-only
to the agent, and so is every file or folder you attach.

For the database, DataLab switches on only the read-only roles for each
connection, accepts only `SELECT` queries, and runs each one in a read-only
transaction. For attachments, read-only stops changes, not reading: the agent
can read every file in a folder you attach, including files added to it
later. Attach only what the agent needs.

## Practice

A separate DataLab with only made-up (synthetic) data, for learning, demos
and trying things out. It can't reach the real study database, attach your
own files, sign in to GitHub, or export anywhere but its own practice folder.

The practice DataLab shows **practice · synthetic data** at the top right of
every screen. Settings it can't change are marked **Fixed on the practice
DataLab**. See [Practice and real data](practice-and-real.md).

## Proposal

A change the agent suggests to a shared lab repository: a knowledge-base page
or skill, or pipeline code and workflows. Nothing is shared until a person
reviews the exact change and presses Save & share.

Knowledge proposals appear as cards under the answer; pipeline proposals
appear in the Pipelines tab. See [Knowledge proposals](knowledge-proposals.md)
and [Pipelines](pipelines.md).

## Save & share

The button that saves a reviewed change to the lab's GitHub repository, under
your name. DataLab checks the change first, including a scan for anything
that looks like participant data.

The scan is an aid: it can miss things, and your review is what counts. Each
possible finding must be ticked off before you can save. Afterwards DataLab
says plainly what happened: saved (with the commit), someone else changed the
same page, the check stopped it, or it failed. Nothing is shared otherwise.
You need to be [signed in to GitHub](github-sign-in.md).

## Workflow

A small recipe file that pulls data, runs fixed steps and checks, and
delivers the results. It's reviewed and saved to the lab's repository, and
when a person presses Run, DataLab runs it the same way every time, with no
AI involved.

Workflows live in the lab's `ihs-pipelines` repository on GitHub, saved there
after review with [Save & share](#save--share). Their results are delivered
only after every check has passed. See
[Run a workflow](running-a-workflow.md).

## Pipeline

Reviewed, tested R code in the lab's `ihsDataR` package that does heavy data
processing, such as combining devices into daily metrics. A workflow can run
a pipeline as one of its steps.

Pipelines are changed through the Pipelines tab, with the Pipelines
assistant, and saved only after their tests pass. See [Pipelines](pipelines.md).

## Replay

Rerunning a past workflow run on the data it extracted then, to reproduce its
results exactly. **Run again** is different: it extracts afresh from today's
database, so its results can differ.

DataLab says before a replay whether it can be exact, and why not if it
can't. Afterwards it records whether every output matched the original byte
for byte. A replay doesn't deliver unless you ask it to. See
[Run a workflow](running-a-workflow.md#run-again-or-replay).

## Small cells

Counts of fewer than 11 participants, which mustn't appear in shared results.
A workflow's `small_cells` check reads the count columns it's told about and
stops delivery on a count from 1 to 10 (or below a higher threshold, if the
workflow sets one). It can't see everything; a person still reviews.

It works out hidden counts only from the totals it's told about (a total row
or a total column): without them it checks only the counts shown. The
threshold is 11 by default and can't be set lower. It can't see columns it
wasn't told are counts, differencing between two delivered files,
percentages it wasn't told about, or means that imply a count. In the real
DataLab, every delivered CSV needs a passing `small_cells` check or a written
reason.

## Data accessed

The record of every query the agent ran in a conversation: which tables,
when, how many rows, and the exact SQL. It's the **Queries** tab beside a
data session's chat.

DataLab also keeps every query in a local audit log, with workflow runs'
queries too. Both hold only metadata, never result values.

## Export

The only way results leave DataLab: you choose files, and if you like the
conversation as a report, and a folder you set up in Settings → Export
folders. The agent can prepare files but can't export them.

Each export goes into its own dated folder with a note of what it is and where
it came from, and nothing already there is overwritten. See
[Export results](exporting.md).

## Research helper

A temporary helper a data session's agent can ask to look something up on the
web. You see the exact question first, and you can edit it, send it, or
decline it.

The helper gets only that question: no conversation, no files, no database.
It answers and is then deleted. Its answer is web content, so numbers taken
from it aren't counted as traced.

## Knowledge base

The lab's shared notes about IHS data: what tables and columns mean, device
quirks, QC rules, cohort definitions and checked queries. It's kept in the
`ihs-knowledge` repository on GitHub, and agents read it as they work.

It must never hold participant-level data: no IDs, no per-person dates, no
pasted rows. Anyone in the lab can improve it through reviewed
[proposals](#proposal).

## Outputs

The files the agent saves in `outputs/` for you: reports, figures and tables.
They're listed in the **Outputs** tab beside the chat, and they're what you
can export. Scripts, SQL and notebooks are in the **Code** tab instead;
scripts in `scripts/` can be exported too, if you tick them.

## Safety check

Live tests of DataLab's safety promises, run on your own computer when you
press **Run safety check** in Settings & Safety → Safety. It starts sealed
test sessions and tries to break out of them.

The section says in one line how the last check went and when it ran; the
full report, check by check, is under **Details**. Run it again after an
update. See [What DataLab will and won't do](safety.md).

## U-M GPT key

Your U-M GPT (Toolkit) API key, which DataLab uses to reach the approved
models. It's kept in your computer's keychain, never shown again, and never
given to the agent.

Save or replace it, and test it, in **Settings & Safety → Connections**. The
practice DataLab uses the key the real DataLab saved.
