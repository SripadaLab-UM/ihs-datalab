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
DataLab, take the tour, try a question before asking it for real, or give a
demo.

You're in the practice DataLab when **practice · synthetic data** shows at
the top right of every screen.

To keep it apart from anything real, the practice DataLab:

- only ever queries the synthetic database on your own computer. It checks
  for a marker that exists only there, and refuses to run a query without it;
- can't attach files from your computer. It offers a few made-up sample files
  instead;
- exports and delivers only to its own practice folder;
- never signs in to GitHub, and its conversations get no copy of the
  knowledge base, so nothing from practice reaches the lab's repositories.

Other AI tools, such as Claude, may connect to the practice DataLab to use
and test it. They aren't approved for study data, which is why they're
allowed only there.

## The real DataLab

The real DataLab connects to the IHS study database, read-only, over the
Michigan Medicine VPN. Everything in [What DataLab will and won't do](safety.md)
applies. It doesn't accept outside AI tools at all.

## Opening the practice DataLab

The practice DataLab runs as a separate DataLab on your computer, with the
synthetic database running in Docker. Ask the DataLab maintainer to set it
up if it isn't already. The first time it opens, the
[tour](tour.md) starts; you can take it again at any time from Help.
