---
title: Use the lab's processed data
summary: Connect Dropbox once, then let a conversation work from the lab's cleaned files, the database, or both.
order: 14
keywords: dropbox, processed data, files, cleaned, folder, data sources, database, both, connect, load, fetch, csv, rds, sas
---

# Use the lab's processed data

The lab keeps cleaned, processed study data in a shared Dropbox folder, made
from the study database by the lab's pipelines. Analysts usually start
there. A DataLab conversation can use those files as well as, or instead of,
the database.

## Connect Dropbox (once)

1. Open **Settings → Connections**, and find **Processed data (Dropbox)**.
2. Click **Connect Dropbox**. Dropbox opens: sign in with your U-M account if
   it asks, and click **Allow**. DataLab can only read files, never change
   them.
3. Dropbox shows a code. Paste it into **Code from Dropbox**, and click
   **Connect**.
4. Find the lab's folder in the list (click a folder's name to open it, and
   the path above the list to go back up), and click **Use this folder**
   beside it. DataLab lists the folder's files, names and sizes only, without
   downloading any.

Your sign-in is kept in this computer's keychain. **Refresh list** picks up
files added since; DataLab also refreshes the list before a conversation
uses it, if it's more than an hour old. **Disconnect** revokes DataLab's
access on Dropbox and forgets the folder, its list and the files it
downloaded for conversations.

The practice DataLab never connects to Dropbox. It has a small synthetic
folder shaped like the lab's instead.

## Choose a conversation's data

When you start an **Analysis**, **Data extraction** or **Data engineering**
conversation, choose under **Data**:

- **Study database**: the raw study data, with SQL. This is the default.
- **Processed data**: the lab's cleaned files only. The agent has no
  database: no queries or catalog.
- **Both**: the files first, with the database to check them. When the two
  disagree, the agent says so.

You can change it later from the **Data:** menu in the conversation's header,
when the agent isn't working. DataLab restarts the agent's sandbox, and tells
the agent on your next message.

## What the agent does with the files

The agent sees the folder as **datasets**: files that differ only by a date,
year, number or ID are one dataset (for example `fitbit/{year}/daily_{id}.csv`).
It fetches only the files it needs. DataLab downloads each one on this
computer, then puts it in the agent's sandbox, where the agent can read it
but not change it.

- Every file it fetches, or asks for and is refused, is listed in the
  conversation's **Queries** tab, beside its queries.
- A file larger than DataLab fetches for an agent (2 GB unless the lab set
  another limit) is refused; ask for a smaller extract.
- Files downloaded once are kept on this computer for later conversations,
  up to a limit, and are removed when you disconnect.
- The files never leave DataLab unless you export results, as with
  database results.
