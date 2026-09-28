---
title: Pipelines
summary: Browse the lab's R package, ask the Pipelines assistant to explain or change it, review it, test it, and save it.
order: 23
screens: /pipelines, /pipelines/*
keywords: pipelines, ihsDataR, R, package, tests, proposal, diff, data engineering, pipelines assistant, save and share, code
---

# Pipelines

A [pipeline](glossary.md#pipeline) is reviewed, tested R code in the lab's
`ihsDataR` package, which lives in the `ihs-pipelines` repository on GitHub
with the workflow files.

## Browse the package

The **Pipelines** tab shows the package's files as they are on GitHub's
`main`, read-only: its functions, pipelines and tests. Press **Sync** if
DataLab says GitHub has newer commits.

## Ask for a change

The chat beside the files is the **Pipelines assistant**, a
[data session](glossary.md#data-session) in its own Pipelines mode: the Data
engineering rules, for the code this tab shows. It explains, edits and tests
the code. Its agent gets its own copy of the repository, works there, and can
run the package's tests in its container. Nothing is attached to it: it
doesn't need to be, and the file you're reading can go with a message if you
tick **Send with message** over the message box. You can't edit the code in
DataLab yourself: ask the assistant.

After each turn, whatever the agent changed in `ihsDataR/` and `workflows/`
becomes one [proposal](glossary.md#proposal), shown in the Pipelines tab.
Changes anywhere else in its copy are listed as not proposed. The Workflows
tab's Workflow authoring chat works the same way, and its proposals are
reviewed and saved here too.

## Review it

- **The diff:** every changed file, line by line.
- **The tests:** the package's tests run on the proposal's exact files, in a
  container with no network. Their results are a quality check, not a safety
  check: code under test could report whatever it likes.
- **The check:** DataLab asks you to confirm, one by one, anything that looks
  like participant data (data files, identifier-like column names, numbers
  next to dates) and any code that would run outside the test container once
  shared (such as `.Rprofile` or a package's load hooks). Changes outside
  `ihsDataR/` and `workflows/` can't be shared at all, and a changed
  workflow file must pass the workflow check, with the real DataLab's
  small-cells rule.

What keeps a change safe is the sandbox and your reading of the diff.

## Save it

**Save & share** runs the check, needs the tests to have passed on the
change, commits it under your GitHub name, and pushes it to `main`. If
others' changes came in meanwhile, the tests run again on the combined code
first. If someone else changed the same lines, nothing is shared: discard
the change and ask the agent to make it again. **Discard** throws a proposal
away.

You need to be [signed in to GitHub](github-sign-in.md).
