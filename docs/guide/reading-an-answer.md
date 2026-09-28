---
title: Read an answer, its trace, and "How was this made?"
summary: What the answer card, the trace, the rigor review and a file's provenance tell you, and what they don't.
order: 11
screens: /workspace/*
keywords: answer, trace, numbers, matched, provenance, how was this made, review, rigor, queries, outputs, evidence
---

# Read an answer, its trace, and "How was this made?"

## The answer

The answer sits under a single dark rule, labelled **Answer**. Once it's
finished, the work behind it folds into one row, **How this answer was
made**, which counts the steps, queries, lab guides read and files changed,
and names any steps that failed along the way, with whether each was retried
successfully. Open it to read the whole story again. **Made in this turn**
lists the output files the turn wrote, to open straight away. Files the
answer names show as buttons that say what they are (**Open report**,
**Open table: flow (CSV)**); hover to see the full path. Query ids and SQL
stay in the **Queries** tab.

## The trace

Under the answer, **Checks on this answer** lists what was checked, and by
whom. DataLab's own check comes first: how many of the answer's numbers it
found in that turn's query results, command output, or data files in
`outputs/`:

- "Numbers checked against this turn's query results and outputs: all 12
  matched", or
- "… 10 of 12 matched (2 not found, listed below: check them)", followed by
  those numbers in amber.

Then the rigor review's own view of the numbers, when it ran, and how many
steps failed along the way: "all were retried successfully", or which remain
unresolved. The rigor review is the agent checking its own work, so it can
disagree with DataLab's check; when they seem to, a line says why. Go by
DataLab's check.

A match means the number appears in something the turn produced. It doesn't
show that it's the right statistic from the right analysis. An unmatched
number isn't necessarily wrong either (it may be worked out in the text), but
check it.

Some numbers are left out of the count altogether, so "all 12 numbers" means
all 12 that were checked:

- whole numbers from -10 to 10 ("two tables", "step 3");
- whole numbers from 1900 to 2100, which are treated as years, even when
  they're a count;
- dates, list numbering, and numbers in code and in link addresses (a link's
  visible text is still checked);
- citation details: volume and pages, PMIDs, arXiv numbers, and DOIs such as
  `10.1038/s41746-021-00400-z`, even though a DOI has a decimal point.

Otherwise, a number with a decimal point, a thousands comma or a % sign is
always checked. Numbers from the research helper's web answer are flagged on
purpose: web content isn't evidence.

## Where a number came from

Each number in the answer is marked. Click it to see where it appears: a
command's output (named by the command), a query's result (which opens that
query in the **Queries** tab), or an output file. A number that appears
nowhere is highlighted and says so.

## The rigor review

If the **Rigor review** switch is on, a review follows the answer: the same
model checking its own work against a checklist. It opens with its first
line; open it to read it all. **Ask the agent to address these** sends the
findings back as your next message. If the review didn't finish, **Run the
review again** tries once more.

The review is a second opinion, not proof, and it can be wrong either way.
See [Rigor review](glossary.md#rigor-review).

## How was this made?

Open any file in the viewer (from **Outputs**, or a link in the answer) and
choose **How was this made?** It shows:

- the [checkpoint](glossary.md#checkpoint) that first saved the file's
  current content, and the turn it came after;
- that turn's commands, the likeliest writers first;
- the scripts as they were then;
- the queries whose result files those commands read.

DataLab can't see which command writes a file, so when none names it, it
says "one of the turn's commands". The chain describes the latest
checkpoint, not the file as it may be now. Nothing here shows data: no query
rows and no command output.

## The Queries tab

In a data session, **Queries** beside the chat is the
[Data accessed](glossary.md#data-accessed) record: every query the agent
ran, with its tables, time, row count and exact SQL, and a link to open the
result.

## Your judgement

The trace, the review and the provenance make the agent's work easier to
check. They don't check it for you. Read an answer as you would a
colleague's analysis before you rely on it.
