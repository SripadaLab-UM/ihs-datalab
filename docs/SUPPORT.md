# Support reports

The toolbar's **More → Send feedback…** makes a support report: a bug report
or a suggestion, packaged as one ZIP the maintainer can read. The code is
`backend/src/datalab/support.py` (collecting, the bundle, saving and sending),
`backend/src/datalab/api/support.py` (the routes) and
`frontend/src/features/toolbar/FeedbackDialog.tsx`.

## What the person does

1. Chooses **Bug** or **Suggestion**, writes what happened and what they
   expected (for a bug), and optionally the steps. The dialog says not to
   include participant data.
2. Optionally adds files: a screenshot, say. Each is listed with its name and
   size (images with a preview) and can be removed. The dialog says they're
   responsible for what's in them. DataLab never takes a screenshot or picks a
   file itself. At most 5 files, 5 MB each, 10 MB together.
3. **Preview report** shows everything that would be in the ZIP: the list of
   files with their sizes and hashes, `summary.md`, `diagnostics.json` and
   `manifest.json`, plus warnings for lines that look as if they hold an ID, a
   date or an email address. Nothing is saved yet.
4. **Save report** saves exactly what was shown, under the data folder.
5. Sends it, in one of two ways (below). **Saved reports** lists every saved
   report, to reopen, download (**Download ZIP**, to the browser's downloads
   folder), send, or delete.

## The bundle

```
DL-20260928-7F3K.zip
  summary.md          what the person wrote, and the basics, to read
  diagnostics.json    what DataLab collected (below), structured
  attachments/…       the files the person chose, if any
  manifest.json       each other file's path, size and sha256; the report ID,
                      when it was made, the type, DataLab's version and the
                      schema version
```

The report ID is `DL-<date>-<4 characters>`, the characters from Crockford's
base 32 (no I, L, O or U), so it can be read out over the phone.

The ZIP is built from the saved report alone, with fixed timestamps, order,
permissions and compression: building it again gives the same bytes and the
same sha256. Saved reports live in `<data_dir>/support/<report-id>/`:
`report.json` (what the ZIP is built from), `attachments/`, the ZIP, and
`status.json` (where it has been saved or sent).

## What's collected

An allowlist, field by field (`support.collect`):

- DataLab's version and profile (real or practice);
- the operating system and its version, the machine type, Python and Docker;
- the browser's user agent, with anything shaped like an email address or a
  long number taken out;
- the tab that was open, from the page's path only: the tab's name, and a
  conversation's ID or a Settings or Help section; never the query string,
  the fragment, or anything else in the path;
- recent operation IDs: the open conversation, queries, workflow runs,
  knowledge and pipeline proposals, pipeline tests, and model request IDs;
- the database layout (how many migrations, the latest) and the last Safety
  check (when, whether it passed, and each check's id and status);
- a trail of the last 24 hours, at most 50 entries: times, event types,
  statuses, error classes and codes, from queries, workflow runs and failed
  steps, proposals, pipeline tests, repository syncs, the open conversation's
  status events, DataLab's own logged warnings and errors (their message
  templates, never the values filled in, and scrubbed of anything shaped like
  an ID, a date or an email address), and the browser's failed requests (the
  method, the status, and the path with every part that isn't a fixed word of
  DataLab's API replaced by `{…}`) and page errors (the error's class only).

Never collected: SQL text, bind values, query results or rows, conversation
or message content, titles, workflow names, who started a run, file contents
or names from a workspace, paths, keys, tokens, passwords, the database's
server or account, or environment values. `tests/test_support.py` plants
canaries in every source the collector reads and checks none reaches any file
of the bundle.

## Sending it to the maintainer

### Save to a folder, then email it

**Save to <folder>** copies the ZIP into one of the export folders (Settings →
Export folders), which may be a Dropbox or other sync folder, as
`<report-id>.zip`. It uses the export folders' own checks and writes through
the folder opened when it was checked. Practice saves only to its own
practice folder.

The report then says **Saved to <folder> (on this computer)**, and for a sync
folder that the sync app will upload it but DataLab can't confirm the upload.
It never says delivered: nobody has it until it's emailed. The next step
names whom to email (`[repos] access_contact`), gives the file's full path to
copy, and **Write the email** opens the mail app with the subject "DataLab
report <ID>", the report ID and summary, and a reminder to attach the file
(an email link can't attach it). The diagnostics stay in the file, never in
the email link.

Saving again is harmless: an identical `<report-id>.zip` already there counts
as saved and nothing is written. A different file with that name is left
alone, and the person is told. The ZIP is written to a hidden partial file
first and then linked into place, so a save that's interrupted never leaves a
half-written `<report-id>.zip`; the partial file is removed at the next save.

### Send to the lab (one click)

On real DataLab, when the lab's settings name a support repository, **Send to
the lab** puts the report in it through GitHub's Contents API, as the
person's GitHub account (the sign-in DataLab already has; no other token):

```
reports/<YYYY>/<MM>/<report-id>.zip
reports/<YYYY>/<MM>/<report-id>.md     the summary, to read on GitHub
```

each with the commit message "Report <ID> (bug)" or "(suggestion)".

- The destination is shown first ("SripadaLab-UM/ihs-support (private)").
  DataLab asks GitHub whether the repository is private, and won't send to a
  public one.
- **Delivered** only when GitHub answers with the commit; the report records
  the commit and the file's link.
- **Pending retry** when it can't be sent now: offline, not signed in, no
  access, or GitHub limiting requests, each said plainly. **Retry** tries
  again, and DataLab tries each pending report once when it starts. The
  report is kept whatever happens.
- Never a duplicate: before writing, DataLab looks at the path. A file with
  the same git blob hash as the report's counts as sent, with no second
  commit. A different file there is left alone and the send is refused, for
  the maintainer to look at. Files are only ever created, never replaced.

Without a support repository, without the GitHub App, or before the person
signs in to GitHub, only the folder is offered, with a note saying why.
Practice never uses GitHub. Nothing is ever posted to the public app
repository.

## Setting up the support repository (maintainer)

1. Create a **private** repository in the lab's GitHub organization, e.g.
   `SripadaLab-UM/ihs-support`. A README is enough; DataLab adds `reports/`.
2. Add it to the **IHS DataLab** GitHub App's installation (organization
   settings → GitHub Apps → IHS DataLab → Repository access), with
   **Contents: read and write**, as for the knowledge and pipelines repos.
3. Give the people who send reports write access to it (e.g. the
   `datalab-users` team). The app acts as the person, so they need access
   themselves.
4. Add it to the lab's `settings.toml`:

   ```toml
   [repos]
   client_id = "…"                        # the GitHub App, already there
   access_contact = "Ali <ali@umich.edu>" # whom to email, already there
   support = "SripadaLab-UM/ihs-support"
   ```

To read reports, open the repository on GitHub, or clone it and unzip a
report. Each ZIP's `manifest.json` lists its files and their sha256.
