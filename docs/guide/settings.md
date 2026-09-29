---
title: "Settings & Safety"
summary: Save or replace the U-M GPT key, test the connections, sign in to GitHub, pick light or dark, check for updates, tidy disk use, and run the Safety check.
order: 31
screens: /settings, /settings/*
keywords: settings, connections, password, key, toolkit key, api key, keychain, database, oracle, vpn, u-m gpt, test connection, github, account, appearance, theme, dark, light, dark mode, updates, update available, check now, every hour, hourly, last checked, storage, disk, space, backups, remove, cleanup, safety check, diagnostics
---

# Settings & Safety

**Settings & Safety** lists its sections down the left (along the top on a
narrow window): **Connections**, **Appearance**, **Export folders**,
**Updates**, **Storage**, **Safety** and **About**. Each has its own address,
such as `/settings/updates`, and Settings opens on the section you used last,
or on Connections the first time.

The shortcuts at the top right of every screen, beside **Help**, lead here
too:

- **Database** and **U-M GPT key** say how each connection stands, with a
  link to [Connections](#connections).
- **GitHub** shows your GitHub account, or that you need to sign in, and
  opens [Connections](#connections). The practice DataLab never signs in to
  GitHub, so it doesn't show this. **Export folders** opens your
  [export folders](#export-folders). On a narrower window these two are in
  the **More** menu.
- **Update available** appears when a newer DataLab is out (DataLab checks
  when it starts and about once an hour while it's open), and opens
  [Updates](#updates).
- **More** also holds the appearance and **Send feedback…**, which makes a
  bug report or suggestion for the maintainer. DataLab adds its own
  diagnostics (no study data, keys or passwords), and you see everything in
  the report before it's saved or sent. Don't put participant data in it.
- **Session** has **End session…**, which signs every DataLab window in this
  browser out. To get back in, quit DataLab and start it again.

The practice DataLab can't change some settings. They are still shown, marked
**Fixed on the practice DataLab**, so you can see what the real DataLab would
let you do.

## Connections

The three things DataLab connects to, each with how it stands and what you can
do about it.

- **U-M GPT**: whether your U-M GPT (Toolkit) API key is **Saved in
  keychain** or **Not saved**, with **Save key** or **Replace key**, and
  **Test connection**.
- **The study database**: the database, the account, the
  [read-only](glossary.md#read-only) roles DataLab switches on and the cohort
  schemas it may read, whether the password is saved, **Replace password**,
  and **Test connection**. The database needs the Michigan Medicine VPN; if
  it can't be reached, check that first.
- **GitHub**: who is signed in, and each lab repository's state (in sync,
  behind, no access...), with **Sign in with GitHub**, **Switch account** and
  **Sign out**. See [Sign in to GitHub](github-sign-in.md).

The key and the password are kept in your computer's keychain. You can save
or replace them here, but DataLab never shows them again, and the agent never
sees them. **Test connection** checks both the database and U-M GPT.

The installer from the lab's install page sets up the database connection
for you, with the lab's settings included. If this section says no database
is set up, the setup didn't finish: run the installer again, the same way (it
carries on from where it stopped, and keeps what's already done). If it still
says so, send the maintainer **Copy diagnostics** from **About** (it has no
data, keys or passwords). If you installed by hand from GitHub's release files
instead, the lab's settings file is needed:
`datalab setup --settings <file>`, from the maintainer. On the practice DataLab the database is the synthetic one on
your computer, whose settings are fixed, and it uses the U-M GPT key the real
DataLab saved.

## Appearance

**Light**, **Dark** or **System** (the default), which follows your
computer's own setting and changes when it does. The choice is kept in this
browser only; if the browser won't keep it (a private window, say), DataLab
follows your computer again next time it opens.

## Export folders

The folders results can be exported to, such as one in your Dropbox, with a
name for each, **Test folder** (saves and removes a synthetic file),
**Rename** and **Remove** (which only forgets the folder), and **Workflow destinations**: which of your folders each workflow's
destination key means on this computer. See [Export results](exporting.md)
and [Run a workflow](running-a-workflow.md). The practice DataLab exports only
to its own practice folder.

## Updates

What the last check for a newer DataLab found, and how long ago ("Last
checked 12 min ago"), with **Check now**. When there is a newer version, its
release notes and **Install update** (always confirmed) are here too, with
the installed version, recent updates and the database backups taken before
them.

DataLab checks by itself when it starts and, while **Check GitHub for
updates every hour while DataLab is open** is ticked (it is unless you
untick it), about once an hour after that. When a check finds a new
version, **Update available** appears at the top right within a few
minutes, and the release is shown here. Checking only looks: nothing is
installed until you press **Install update** and confirm. Untick it to check
only at start and when you press **Check now**; the choice is kept for this
computer (the practice DataLab keeps its own).

## Storage

How much space DataLab's data folder uses, and what uses it: conversations,
SQL Playground results, workflow runs, backups and logs.

Nothing is deleted automatically. You can remove a few kinds of thing, one
at a time, each after confirming:

- a **Playground result** (the query stays in its history);
- a finished **workflow run's files** (its record stays, but it can't be
  replayed afterwards);
- a database **backup**, except one an update relies on. A backup taken
  before a rollback asks you to confirm twice, since it may hold the only
  copy of what the rollback dropped.

DataLab never removes its database, the audit log, or your exports.
Conversations are deleted from the Workspace list.

## Safety

One line says how the last [Safety check](glossary.md#safety-check) went and
when: **Every check passed**, a number of problems, or **Not run yet**.
**Run safety check** tests the isolation, database and network promises live
on this computer (about 20 seconds). **Details** opens the full report, check
by check; it opens by itself when a check finds a problem. See
[What DataLab will and won't do](safety.md).

## About

Which DataLab this is (practice or real), its version, and the catalog, with
**Copy diagnostics**: a report for the maintainer with versions and check
results, and no data, keys or passwords.
