---
title: Knowledge proposals
summary: Review the agent's suggested knowledge-base edits, then Save & share them or discard them.
order: 21
screens: /knowledge, /knowledge/*
keywords: knowledge, knowledge base, proposal, edit, page, skill, save and share, participant data, check, github, conflict
---

# Knowledge proposals

The [knowledge base](glossary.md#knowledge-base) is the lab's shared notes
about IHS data, kept in the `ihs-knowledge` repository on GitHub. Each
conversation gets its own copy of it, which the agent reads and may edit.
Its edits stay in that conversation until a person shares them.

## The proposal card

When a turn changed the copy, a card appears under the answer: "Knowledge
edits proposed", with the files it changed. It's never folded away. For each
file you see the exact change as a diff, and you can:

- **Edit** the text, then **Keep my edit**;
- **Leave out** a file, so it isn't shared;
- read what was refused, and why (files outside the knowledge base's
  folders, for example, or edits to who reviewed a page).

A change to a page's status (draft or reviewed) is highlighted, so it can't
slip through unnoticed. Only people review: DataLab fills in who reviewed a
page, and when, from the person saving it.

## The check

DataLab checks the change before it can be shared: that pages are well
formed and their links and tables exist, and whether anything looks like
participant data, such as study IDs, dates next to IDs, or pasted tables.
Each possible participant-data finding must be ticked off, one by one,
before you can save. The scan helps, but it misses things (names, for
example), so your own reading is what counts. The knowledge base must never
hold participant-level data.

## Save & share

**Save & share** commits the change under your GitHub name and pushes it to
the lab's `main`. You need to be [signed in to GitHub](github-sign-in.md).
DataLab says how it ended:

- **Saved**, with the commit;
- **someone else changed this page**: GitHub's version is shown against
  yours, so you can resolve it and try again;
- **the check stopped it**, with the findings;
- **it failed**, and nothing was shared.

What's shared is exactly what you saw. The same check runs again in GitHub
Actions on `ihs-knowledge` after each push. **Discard** throws a proposal
away; nothing is shared.

## Who sees a change

Other conversations, and the rest of the lab, see a change only once it's
saved and pushed. That's also what keeps material from a data session out of
research sessions until a person has reviewed it.

## The Knowledge tab

The **Knowledge** tab shows the pages and lab skills as they are on GitHub,
by folder, with their facts (status, evidence, cohorts), and the recent
changes. Its chat can help write or tidy a page; its edits become proposals
like any other.

The practice DataLab doesn't get a copy of the knowledge base and never
signs in to GitHub, so nothing from practice can reach it.
