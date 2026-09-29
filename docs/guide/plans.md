---
title: Analysis plans and pilots
summary: Edit and approve the agent's plan, what approval records, revisions, and pilots before a full run.
order: 12
keywords: plan, approve, frozen, hash, revision, exploratory, off-plan, pilot, prespecified, analysis type
---

# Analysis plans and pilots

## When a plan appears

In Analysis mode, before it looks at outcome data for a new question, the
agent proposes an [analysis plan](glossary.md#plan) and waits for you. In
other modes it proposes one when a question calls for it. Small follow-ups
and quick questions about the data itself don't need one. With
[Express](glossary.md#express) on, the agent doesn't propose plans at all.

## Read and edit it

The plan appears as a card in the chat. It says which kind of analysis the
agent chose and why (describe or compare, association, prediction, data
quality, or other), and has four core sections: question and purpose, data
and scope, checks and limitations, and deliverables. The kind of analysis
adds the sections that matter for it.

- Edit any section in place.
- **Add a section** offers the optional ones that apply (missing data,
  sensitivity analyses, pilot then full run, and so on), or one of your own.
  **Remove** takes an optional section out.
- **A different kind of analysis?** sends the plan back as another type. The
  agent rewrites it, keeps your edits, and shows you what changed.
- "When it was proposed" says how many queries had already returned data in
  this conversation, and from which tables.

Your unsaved edits survive a page reload in the same browser tab.

## Approve it

**Approve plan** is available once every required section has text. The
plan is then frozen, with the time and a fingerprint (a hash) covering
everything shown. **Not yet** asks the agent what to change.

What approval does:

- It records what you agreed, exactly. The agent is asked to say which
  later results follow the plan and to label anything else "exploratory
  (off-plan)", in its answers and reports.
- It doesn't lock anything. The agent can still run an off-plan query; it's
  asked to label it, and the [rigor review](glossary.md#rigor-review) checks
  that it did.
- It doesn't make earlier results prespecified. Results seen before the
  plan are said to be so on the card, in exports and in the review.

## Revisions

An approved plan is never changed. To change course, the agent proposes a
**revision**, saying what changes and why. The card shows it against the
approved plan, section by section, and you approve it like any plan. The
earlier version is kept and marked as revised, and exports show both.

## Pilots

In Analysis mode the agent runs a [pilot](glossary.md#pilot) first: the
smallest meaningful run on part of the data. It labels the results
preliminary and reports what it covered, how many participants and rows were
kept at each step, any data-quality problems, how long it took and how big
the full run would be. Then it stops and asks whether to go ahead.

This is how the agent is instructed, not a rule DataLab enforces. You can
ask for a full run straight away, and the decision to scale up is yours.

## Rather explore?

Say so. The agent goes ahead without a plan and labels all of that work
exploratory.
