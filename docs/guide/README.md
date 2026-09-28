---
title: Start here
summary: What DataLab is, what it does with study data, and where each guide is.
order: 0
keywords: help, contents, introduction, new, getting started, overview
---

# Start here

DataLab lets you ask an AI agent questions about Intern Health Study data.
The agent is OpenAI Codex, running on U-M GPT. It works in a sealed workspace
of its own. It can read the study database but never change it. You see each
step it takes and everything it reads from the database, and results leave
DataLab only when you export them.

This guide is the same text as DataLab's own Help, its tooltips and the
first-run tour. It's written for researchers who are new to DataLab.

## If you're new

1. Read [What DataLab will and won't do](safety.md). It takes two minutes.
2. Open the practice DataLab, which has only made-up data, and take the
   tour. See [Practice and real data](practice-and-real.md).
3. [Ask your first question](first-question.md), then learn to
   [read an answer](reading-an-answer.md).

## Guides

**Working with the agent**

- [Ask your first question](first-question.md)
- [Read an answer, its trace, and "How was this made?"](reading-an-answer.md)
- [Analysis plans and pilots](plans.md)
- [Export results](exporting.md)

**The other tabs**

- [The SQL Playground](sql-playground.md)
- [Knowledge proposals](knowledge-proposals.md)
- [Run a workflow](running-a-workflow.md)
- [Pipelines](pipelines.md)

**Setting up**

- [Sign in to GitHub](github-sign-in.md)
- [Settings & Safety](settings.md)
- [Practice and real data](practice-and-real.md)

**Reference**

- [What DataLab will and won't do](safety.md)
- [Glossary](glossary.md): what DataLab's own words mean.
- [The tour](tour.md): the five steps of the first-run tour.

## More detail

The exact rules behind each safety promise, and how each one is enforced and
tested, are in `docs/SAFETY.md` in the DataLab repository on GitHub. The
**Safety check** in Settings & Safety tests the first three promises (the
rest of your computer, the database, and where data can go) live on your own
computer, when you press **Run safety check**.
