---
title: "Settings: Connections and Storage"
summary: Save the database password and U-M GPT key, test the connections, and see and tidy disk use.
order: 31
screens: /settings
keywords: settings, connections, password, key, keychain, database, oracle, vpn, u-m gpt, test connection, storage, disk, space, backups, remove, cleanup
---

# Settings: Connections and Storage

## Connections

**Settings & Safety → Connections** shows the two things DataLab connects
to: the study database and U-M GPT.

- **The database password and the U-M GPT key** are kept in your computer's
  keychain. You can save or replace them here, but DataLab never shows them
  again, and the agent never sees them.
- **Read-only roles** are the only database roles DataLab switches on for
  each connection, so a session can read and nothing else.
- **Cohorts** lists the cohort schemas DataLab may read.
- **Test connection** checks both. The database needs the Michigan Medicine
  VPN; if it can't be reached, check that first.

If no database is set up yet, ask the DataLab maintainer for the lab's
settings file. The practice DataLab shows only its synthetic database, whose
password is fixed.

## Storage

**Settings & Safety → Storage** shows how much space DataLab's data folder
uses, and what uses it: conversations, SQL Playground results, workflow
runs, backups and logs.

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

## Also in Settings & Safety

- **Safety check** tests the isolation, database and network promises live
  on this computer when you press **Run safety check**. See
  [What DataLab will and won't do](safety.md).
- **GitHub**: see [Sign in to GitHub](github-sign-in.md).
- **Export folders** and **Workflow destinations**: see
  [Export results](exporting.md) and [Run a workflow](running-a-workflow.md).
- **Copy diagnostics** makes a report for the maintainer with versions and
  check results, and no data, keys or passwords.
