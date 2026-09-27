---
title: What DataLab will and won't do
summary: The safety boundaries DataLab enforces, and the things it doesn't promise.
order: 1
keywords: safety, promises, privacy, PHI, study data, internet, boundary, safety check, sealed, container
---

# What DataLab will and won't do

These are the boundaries DataLab enforces. The first three (the rest of your
computer, the database, and where data can go) are tested live by the
**Safety check** in Settings & Safety when you press **Run safety check**.
Until you do, it says "Not run yet".

## What DataLab enforces

- **The agent can't touch the rest of your computer.** Each conversation
  runs in its own sealed container. The agent sees only its own workspace and
  the files or folders you attach, and it can't change what you attach.
- **The agent can't change the study database.** It reaches the database
  only through DataLab, which switches on only read-only access. Only
  `SELECT` queries run. The agent never sees the database password, and
  DataLab limits how heavy its queries can be.
- **Study data goes only to approved places.** A
  [data session](glossary.md#data-session) can reach U-M GPT and DataLab,
  and nothing else. A [research session](glossary.md#research-session) has
  the web but no database connection. A question from a data session to the
  [research helper](glossary.md#research-helper) goes out only after you
  approve its exact text.
- **Your work isn't lost.** Conversations, results and workflow runs are
  saved. The workspace is [checkpointed](glossary.md#checkpoint) after every
  turn, so you can put files back. Nothing is deleted unless you delete it.
- **Results leave only when you export them.** The agent can prepare files,
  but only you can [export](exporting.md) them, to a folder you chose. The one
  exception is a [workflow](glossary.md#workflow): a file saved to the lab's
  `ihs-pipelines` repository after review with Save & share. When a person
  presses **Run**, it delivers to the destination set for it on this computer.
- **The shared knowledge base gets nothing without your review.** Agents can
  suggest edits, but you see the exact change before it's shared. See
  [Knowledge proposals](knowledge-proposals.md).
- **You can see everything the agent read from the database.** Each data
  session's **Queries** tab lists every query: tables, time, rows and the
  exact SQL. See [Data accessed](glossary.md#data-accessed).

## What DataLab doesn't promise

- **The agent sees the data you give it.** That's the point. U-M GPT is the
  approved destination for that data.
- **The agent can be wrong.** Check its work as you would a colleague's.
  The [trace](glossary.md#trace-and-provenance) and the
  [rigor review](glossary.md#rigor-review) help, but neither shows that an
  answer is right.
- **Plans, pilots and the rigor review guide the agent; they don't bind it.**
  A plan is a record of what you agreed, not a lock; pilots come from the
  agent's instructions; the review is the same model checking itself. Your
  own judgement is the control.
- **The small-cells check has limits.** It reads only the counts a workflow
  declares, and works out hidden counts only from the totals it's told about.
  A person still reviews what's delivered. See
  [small cells](glossary.md#small-cells).
- **Exported files are yours to look after.** Once they're in your own
  folder, DataLab no longer controls them. Keep them on approved storage.
- **Anything you attach to a research session may reach the internet**, and
  so may anything you approve in a research-helper question. DataLab can't
  know what a file contains, so read before you attach or approve.
- **It can't protect a compromised computer.** Keep your operating system
  and Docker Desktop up to date.

## Outside AI tools

Other AI tools, such as Claude, aren't approved for study data, so they're
used only with the [practice](glossary.md#practice) DataLab, which holds
made-up data. See [Practice and real data](practice-and-real.md).
