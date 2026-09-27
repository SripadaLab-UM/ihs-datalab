---
title: The SQL Playground
summary: Write and run your own read-only SQL, browse the catalog, export a result, and ask the docked chat.
order: 20
screens: /sql, /sql/*
keywords: sql, query, playground, catalog, tables, columns, bind, history, results, export, save as workflow, extraction
---

# The SQL Playground

The SQL Playground is your own SQL editor for the study database, with the
catalog beside it and a Data extraction chat to help.

## Write a query

- Write one `SELECT` query (a `WITH` before it is fine). Name each table with
  its cohort, as in `IHS_2025.VFITBITDAILYDATA`.
- **Tables** on the left searches the catalog of every cohort. Click a name
  to put it in your query.
- DataLab checks the query as you type. Under the editor it says **Passes
  the SQL check** and which tables it reads, or what's wrong and where. Only
  read-only queries using Oracle's built-in functions pass.
- For a value that changes, use a bind variable such as `:start_date`.
  DataLab asks for its value when you run.

## Run it

Press **Run** (or the keys shown beside it). **Stop** cancels the query in
the database itself. The grid shows the first rows of the result; the whole
result stays in DataLab until you export it. DataLab also limits how long a
query may run and how large a result may be.

**History** on the left lists your earlier queries, to open again.

## Export the result

**Export the result** copies the full result to one of your export folders,
with a manifest of the query that made it. It may contain study data: keep it
on approved storage. See [Export results](exporting.md).

## Save as workflow

**Save as workflow** turns a query that passes the check into a
[workflow](glossary.md#workflow) draft, written by DataLab itself from the
SQL, with no AI and no data. Each bind becomes a parameter, and a query that
counts gets a [small cells](glossary.md#small-cells) check. You review and
edit the file before anything is saved. See
[Run a workflow](running-a-workflow.md#make-a-workflow).

## The docked chat

The chat beside the editor is a Data extraction conversation, a
[data session](glossary.md#data-session). Tick **Send with your message**
to include the query in the editor; you see exactly what will be sent. Its
queries are listed in its **Queries** record like any conversation's.
