---
title: Practice and real data
summary: The practice DataLab holds only made-up data. What it can and can't do, and how to tell which one you're in.
order: 32
keywords: practice, synthetic, fake, made-up, demo, real, profile, training, learning, outside tools
---

# Practice and real data

There are two kinds of DataLab. They look the same, and each has its own data
folder, so nothing is shared between them.

## The practice DataLab

The [practice](glossary.md#practice) DataLab uses a made-up (synthetic)
database with the same shape as the IHS data: a few cohorts, the device
tables, realistic quirks, and invented participants. Use it to learn
DataLab, try a question before asking it for real, or give a
demo.

You're in the practice DataLab when the header at the top of every screen
says **practice** (**practice · synthetic data** on a wide window).

To keep it apart from anything real, the practice DataLab:

- only ever queries the synthetic database on your own computer. It checks
  for a marker that exists only there, and refuses to run a query without it;
- can't attach files from your computer. It offers a few made-up sample files
  instead;
- exports and delivers only to its own practice folder;
- never signs in to GitHub, and its conversations get no copy of the
  knowledge base, so nothing from practice reaches the lab's repositories.
  The agent is told there's none, so it works from the catalog and the data
  instead of trying to read the knowledge base.

Other AI tools, such as Claude, aren't approved for study data, so they're
used only with the practice DataLab, to try and test DataLab.

## The real DataLab

The real DataLab connects to the IHS study database, read-only, over the
Michigan Medicine VPN. Everything in [What DataLab will and won't do](safety.md)
applies. Outside AI tools must not be used with it. Nothing technical stops
a tool that controls your terminal or browser, so this is a rule for people.

## What practice needs

The practice DataLab needs Docker Desktop (on a Mac the installer offers to
install it if it's missing), and nothing from the lab: no
database password, no VPN and no GitHub account. Its installer sets up the
synthetic database for you (a few minutes, the first time), and the database
runs in Docker on your computer only, where nothing else can reach it.

A U-M GPT (Toolkit) API key is optional:

| Needs the key | Works without it |
|---|---|
| Conversations with the agent, in the Workspace | The SQL Playground |
| Drafting a workflow with the agent | Save as workflow, and running workflows |
| (In the real DataLab) Knowledge's Edit with agent, and the agent's suggested updates | Exports and Settings |

To add a key later, run `datalab --profile practice setup --update`.

## Opening the practice DataLab

Open **DataLab (practice)** from the Desktop, the Start menu or Applications.
If the synthetic database isn't running, the practice DataLab starts it: the
first screen says so while it starts, and Settings → Connections shows how
it's going. If it can't (Docker Desktop isn't running, say), Settings →
Connections says why, with **Try again**.

The made-up data is kept when DataLab is updated or installed again. To start
over from scratch, use **Reset practice data…** in Settings → Connections (it
asks first), or run `datalab --profile practice practice-db reset`. Your
practice conversations and exports aren't touched. Uninstalling DataLab asks
whether to delete the practice database too.

A first-run [tour](tour.md) is planned for the practice DataLab; it's
switched off for now. Start with [Ask your first question](first-question.md)
instead.
