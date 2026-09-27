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
and names any errors along the way. Open it to read the whole story again.
**Made in this turn** lists the output files the turn wrote, to open
straight away.

## The trace

Under the answer, a chip says how many of its numbers DataLab found in that
turn's query results, command output, or data files in `outputs/`:

- "all 12 numbers matched to this turn's outputs", or
- "2 of 12 numbers not matched to this turn's outputs", followed by those
  numbers in amber.

A match means the number appears in something the turn produced. It doesn't
show that it's the right statistic from the right analysis. An unmatched
number isn't necessarily wrong either (it may be worked out in the text), but
check it. Dates, years, counts of ten or less, list numbers and citation
details aren't counted. Numbers from the research helper's web answer are
flagged on purpose: web content isn't evidence.

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
