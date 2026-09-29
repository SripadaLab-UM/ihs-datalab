---
title: Knowledge proposals
summary: Edit knowledge-base pages yourself or with the agent, review proposed edits and suggested updates, then Save & share them or discard them.
order: 21
screens: /knowledge, /knowledge/*
keywords: knowledge, knowledge base, proposal, edit, edit page, edit with agent, draft, suggested knowledge update, propose, page, skill, save and share, participant data, check, github, conflict
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

Only people review. The agent can't change a page's status (draft or
reviewed): its version keeps the status the page had, `draft` for a new page,
and the card says if it tried. Only your own edit on the card changes it.
DataLab fills in who reviewed a page, and when, from the person saving it.

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
in folding sections (a data source's table pages sit under it), with their facts (status, evidence, cohorts), and the recent
changes. Its chat, in Knowledge writing mode, can help write or tidy a page
or skill; its edits become proposals like any other. It's a data session
that can read the catalog (tables and columns) but can't run queries, and
nothing can be attached to it. The page open in the tab can go with your
message.

The sections start folded; opening a page unfolds what holds it, and
DataLab remembers what you fold and unfold. Search looks inside folded
sections too. With the keyboard, the arrow keys move through the list,
Right and Left unfold and fold, and Enter opens a page.

The practice DataLab doesn't get a copy of the knowledge base and never
signs in to GitHub, so nothing from practice can reach it. The actions below
say "Available on the real DataLab" there.

## Editing a page yourself

A page or lab skill has two actions over it:

- **Edit page** opens it in an editor, with no AI involved: its Markdown and
  front matter, a **Preview** as the page reads, and **Review changes**
  against the version you started from. The check runs as you type, and
  marks what it finds in place. You may change a page's status (to
  `reviewed`, say); DataLab fills in `reviewed_by` and `reviewed_on` from
  you when you save, whatever the text says.
- **Edit with agent** opens the Knowledge assistant beside the page, with
  the page ticked to go with your message and the cursor in the box:
  describe the change. Nothing is sent until you send it. The agent's edit
  comes back as a proposal card, as above.

Two ways to save, and they're different:

- **Keep as a draft on this computer** keeps it in DataLab, on this
  computer only: "Saved on this computer (not shared)". It's there after a
  reload, in another browser, and when you open the page again ("Continue
  editing"). Even what you typed but didn't keep comes back in the same
  browser. Nobody else sees a draft.
- **Save & share** runs the check again, commits it as you, and pushes it
  to the lab's knowledge base on GitHub: "Shared to GitHub · commit …".

If someone changed the page on GitHub since you started, DataLab never
overwrites it. It shows **your edit**, **the version now on GitHub**, and
**where you started**, and you choose: **reapply your edit on the new
version** (changes that don't overlap are combined; where they do, you
write the text to keep), or **open both side by side**.

Some pages can't be edited here, and say where they come from instead:
`index.md` is written by the check from the pages' front matter, and
`generated/` comes from the database catalog (`datalab catalog`). To change
what they say, edit the pages they list, or a table's own page in
`tables/`.

## Suggested Knowledge updates

In an Analysis, Data extraction or Data engineering conversation, when the
agent confirms something durable about the data (a quirk a query showed,
what a column really holds, a caveat), it may add a **Suggested Knowledge
update** under its answer. It shows as a short row: the page it's for, its
title and its status, with **Accept as proposal** beside it. Press the row
(or **+**) to open the full card: the text, why it's worth keeping, and the
queries in this conversation that show it. Once you've accepted or dismissed
it, it folds back to a row saying what happened (with **Review in
Knowledge →** after an accept), and you can open it again at any time. At most one or
two an answer, and never for a one-off result. DataLab refuses one whose
evidence isn't a query that ran here, or whose text looks like participant
data. A suggestion never changes the knowledge base by itself:

- **Accept as proposal** makes it a draft edit of that page, on this
  computer, to review and Save & share in the Knowledge tab;
- **Edit first** opens that editor straight away;
- **Dismiss** sets it aside.

You can make one yourself too: **Propose a Knowledge update** over the
conversation opens a short form (the page, the text, why, and which of this
conversation's queries show it). It's checked the same way, and becomes a
draft edit for you to review and share.
