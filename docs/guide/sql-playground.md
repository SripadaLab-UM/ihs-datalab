---
title: The SQL Playground
summary: Write and run your own read-only SQL, have the agent draft a query for you, browse the catalog, export a result, and save it as a workflow.
order: 20
screens: /sql, /sql/*
keywords: sql, query, playground, catalog, tables, columns, bind, history, results, export, save as workflow, extraction, generate, propose, agent
---

# The SQL Playground

The SQL Playground is your own SQL editor for the study database, with the
catalog beside it and an agent that can draft a query for you. Your queries
have the same checks and limits as the agent's, and their results stay in
DataLab, out of the agent's reach, until you export them.

## Write a query

- Write one `SELECT` query (a `WITH` before it is fine). Name each table with
  its cohort, as in `IHS_2025.VFITBITDAILYDATA`.
- **Tables** on the left searches the catalog of every cohort. Click a
  table's name, or a column's, to put it in your query.
- DataLab checks the query as you type. Under the editor it says **Passes
  the SQL check** and which tables it reads, or what's wrong and where. Only
  read-only queries using Oracle's built-in functions pass.
- For a value that changes, use a bind variable such as `:start_date`. A box
  for each one appears under the editor, in **Values for the bind
  variables**.

## Run it

Press **Run** (or the keys shown on it). **Stop** cancels the query in the
database itself. The grid shows the first rows of the result; the whole
result stays in DataLab until you export it. DataLab also limits how long a
query may run and how large a result may be. If the SQL check or the
database refuses a query, the results area says so, and why.

**History** on the left lists your earlier queries and how each went. Open
one to see its SQL and result again.

## Have the agent draft it

Press **Generate SQL with the agent**, or open the chat, and describe the
data you want in your own words ("daily Fitbit steps for enrolled 2025
participants in March"). The SQL assistant finds the tables, checks every
column in the catalog, may run a few small counts to test its assumptions,
and then proposes one query. It never runs the final query for you.

When it's ready, a row over the editor says **The agent proposed a query**:

- **Use this query** opens it in the editor and keeps your draft: **Back to
  your earlier draft** brings that back.
- **Replace current draft** puts it in the editor in place of your draft.
- **Dismiss** leaves the editor as it is.

**How this SQL was created** shows what you asked, the assumptions to
check, the bind values, the tables it reads, the knowledge-base pages it
used, and the queries it ran while exploring. Read the assumptions before
you run it.

To change the query, say so in the chat ("only April", "include Garmin").
Tick **Send with message** to send the query in the editor along with your
message; open that row to see exactly what will be sent. The chat is a
[data session](glossary.md#data-session). It's also listed in the
**Workspace**, where its **Queries** tab shows what it ran. **New chat**
starts a fresh one.

## Export the result

**Export** over the grid copies the full result to one of your export
folders, with a manifest of the query that made it. It may contain study
data: keep it on approved storage. See [Export results](exporting.md).

## Save as workflow

**Save as workflow** turns a query that passes the check into a
[workflow](glossary.md#workflow) draft, written by DataLab itself from the
SQL, with no AI and no data. Each bind becomes a parameter, and a query that
counts gets a [small cells](glossary.md#small-cells) check. You review and
edit the file before anything is saved. See
[Run a workflow](running-a-workflow.md#make-a-workflow).
