---
title: Export results
summary: Choose files and a folder, add the conversation as a report, and what the exported folder holds.
order: 13
keywords: export, report, download, share, folder, dropbox, destination, manifest, outputs, study data
---

# Export results

[Export](glossary.md#export) is the only way results leave DataLab, and only
you can do it. The agent can prepare files in `outputs/`, but it can't export
them.

## Before your first export

Add a folder to export to in **Settings & Safety → Export folders → Add a
folder**: give it a name if you like (such as "Lab Dropbox"), then press
**Choose folder…** and choose it in your computer's own file window.

**Dropbox.** DataLab uses the Dropbox folder on your computer that the
Dropbox app keeps in sync; there's no Dropbox account or key to set up. If
DataLab finds your Dropbox (or OneDrive, Box, Google Drive) folder, it offers
**Choose in Dropbox…**, which opens the file window there. Choose a folder
inside it, such as `IHS exports`.

Then press **Test folder**. DataLab saves a small synthetic test file there,
reads it back and removes it, and says **Saved locally ✓**. That shows
DataLab can save there. It doesn't show that Dropbox has uploaded anything:
the Dropbox app uploads files when it's running and signed in, and DataLab
can't see whether it has. In a shared Dropbox folder, the people it's shared
with may briefly see the test file appear and disappear; it holds no study
data. After every export DataLab says "Saved to *your
folder's name* (on this computer)", and for a Dropbox folder adds that
Dropbox will upload it.

A folder that can't be used says why: it isn't there (Dropbox not installed
or signed in, the folder moved, or a separate drive unplugged), your account
can't save in it, or it's somewhere
DataLab never writes (system folders, a whole home folder, DataLab's own
data folder, or a link that leads out of your Dropbox). A folder that may be
online-only in your sync app gets a warning rather than a refusal: saving to
it usually works, and **Test folder** tells you. **Rename** changes
the name exports and workflows show; **Remove** only makes DataLab forget
the folder, and never deletes it or anything in it. Untick **Offer for
exports and workflow deliveries** to keep a folder without anything being
saved there.

The practice DataLab exports only to its own practice folder, which you can
test too; Dropbox setup is available on the real DataLab.

## Export from a conversation

1. Press **Export** in the conversation's header, or **Export…** in the
   **Outputs** tab.
2. Tick the files you want. Nothing is ticked for you.
3. If you like, tick **The conversation, as one self-contained web page**:
   the questions and answers, charts, and the SQL that was run. It can also
   include the agent's reasoning and the commands it ran, but never their
   output. A report from a data session carries a "contains study data"
   banner.
4. Choose the folder, and export.

The files exported are the versions listed when you opened the dialog. If
the agent saves newer ones meanwhile, the dialog says so and lets you show
them.

## What you get

Each export goes into its own dated folder, named for the date, the
conversation's title and its ID. DataLab removes anything in the name that
looks like a participant ID, an email address or a date. Inside:

- a manifest saying what the export is, when it was made and which
  conversation made it;
- the report, if you chose it;
- the agent's files, under `files/`.

Nothing already in the folder is overwritten. So that exported files can't
act on your computer by themselves:

- files that run something when double-clicked (`.bat`, `.command`, and so
  on) get `.txt` added to their name;
- web pages are exported as inert copies unless you tick **Keep web pages
  exactly as the agent made them**, whose scripts could send data when
  opened;
- every file is marked as downloaded, as your computer does for files from
  the internet.

Each export is recorded in DataLab's audit log: when, where to, how many
files, and whether it may contain study data. The log holds no contents.

## After you export

Exported files are yours to look after. From a data session they may hold
study data, so keep them on approved storage.

## Other ways out

- **SQL Playground:** export a query's full result, with a manifest of the
  query that made it. See [The SQL Playground](sql-playground.md).
- **Workflows:** a workflow saved to the lab's repository after review
  delivers, when a person presses **Run**, to the destination set for it,
  once every check has passed. See
  [Run a workflow](running-a-workflow.md).
