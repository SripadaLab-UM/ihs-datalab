---
title: "Settings & Safety"
summary: Save or replace the U-M GPT key, test the connections, sign in to GitHub, pick light or dark, check for updates, tidy disk use, and run the Safety check.
order: 31
screens: /settings, /settings/*
keywords: settings, connections, password, key, toolkit key, api key, keychain, database, oracle, vpn, u-m gpt, test connection, github, account, appearance, theme, dark, light, dark mode, updates, update available, check now, storage, disk, space, backups, remove, cleanup, safety check, diagnostics
---

# Settings & Safety

**Settings & Safety** lists its sections down the left (along the top on a
narrow window): **Connections**, **Appearance**, **Export folders**,
**Updates**, **Storage**, **Safety** and **About**. Each has its own address,
such as `/settings/updates`, and Settings opens on the section you used last,
or on Connections the first time.

Two entries at the top right of every screen lead here too:

- **Update available** appears when a newer DataLab is out, and opens
  [Updates](#updates).
- **GitHub** shows your GitHub account, or **Sign in**, and opens
  [Connections](#connections). The practice DataLab never signs in to GitHub,
  so it doesn't show this.

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

If no database is set up yet, ask the DataLab maintainer for the lab's
settings file. On the practice DataLab the database is the synthetic one on
your computer, whose settings are fixed, and it uses the U-M GPT key the real
DataLab saved.

## Appearance

**Light**, **Dark** or **System** (the default), which follows your
computer's own setting and changes when it does. The choice is kept in this
browser only; if the browser won't keep it (a private window, say), DataLab
follows your computer again next time it opens.

## Export folders

The folders results can be exported to, with **Add a folder** and **Remove**,
and **Workflow destinations**: which of your folders each workflow's
destination key means on this computer. See [Export results](exporting.md)
and [Run a workflow](running-a-workflow.md). The practice DataLab exports only
to its own practice folder.

## Updates

What the last check for a newer DataLab found, and when, with **Check now**.
When there is one, its release notes and **Install update** (always
confirmed) are here too, with the installed version, recent updates and the
database backups taken before them.

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
