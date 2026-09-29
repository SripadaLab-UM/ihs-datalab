---
title: Ask your first question
summary: Start a conversation, choose a mode, ask, and watch the agent work.
order: 10
screens: /workspace
keywords: start, new conversation, ask, question, mode, starter, stop, attach, inputs, effort, model
---

# Ask your first question

## Start a conversation

1. In the **Workspace** tab, press **New conversation**.
2. Choose what you'd like to do. This is the conversation's
   [mode](glossary.md#mode):
   - **Analysis**: scientific questions. The agent plans first, pilots, and
     asks before a large run (unless [Express](glossary.md#express) is on).
   - **Data extraction**: a clean, documented dataset out of the database.
   - **Data engineering**: working on the lab's R pipelines.
   - **Research**: literature, methods and packages, with the web.

   The note under the modes says which kind of session you'll get: a
   [data session](glossary.md#data-session) (database, no web) or a
   [research session](glossary.md#research-session) (web, no database). It
   can't be changed later, so start a new conversation if you need the other
   kind.
3. The model is the recommended one. **Change** offers only models approved
   for study data.
4. Press **Start**.

## Ask

Pick one of the suggested questions, or type your own and press Enter
(Shift+Enter starts a new line). The menu by the model name sets how hard
the agent thinks about your next message: Quick, Balanced or Thorough.

A good first question says:

- which cohort or year ("the 2025 interns");
- what you want to measure, and per what ("mean daily steps per
  participant");
- what to compare, if anything ("before and after the start of internship");
- what the answer is for ("a figure for a lab meeting").

You don't need to know the table names. The agent finds them in the
catalog and the lab's [knowledge base](glossary.md#knowledge-base).

## Watch it work

Each step appears as a line in plain words, such as "Read what's in
FITBITDAILYDATA (2025)". Press **+** on a line to see what the agent saw: a
table's columns, the SQL it ran and how many rows came back, a lab guide, or
the files it wrote. Three or more similar steps fold into one line; a failed
step is never folded away.

One live line says what the agent is doing now, with **Stop** beside it. If
the line says it's waiting for you, the agent has paused for a decision:

- an [analysis plan](plans.md) to approve, or
- a question for the [research helper](glossary.md#research-helper) to
  send, edit or decline.

With the [Express](glossary.md#express) switch on (in the conversation's
header, beside **Rigor review**), the agent skips these: no plan, no pause
after a pilot, no research helper. It answers directly and quickly, with the
same data access and checks. It's off in a new conversation; turn it on for
quick questions, and off again for work that needs a plan.

A stopped turn keeps everything it saved: see **History**. If a turn stopped
part-way, **Continue** picks it up without sending anything twice.

## Attach files

Use the **Inputs** tab beside the chat. Only you can attach, by choosing in
your computer's own file window, and everything you attach is
[read-only](glossary.md#read-only) to the agent. An attached folder is shared
whole, including files added to it later, and DataLab lists any credentials
files it finds inside. Anything you attach to a research session may reach
the internet. The practice DataLab can't attach your own files; it offers
made-up sample files instead.

## Keep track

DataLab names the conversation from your first question; click the title to
rename it. Every conversation is saved in the list on the left, with a lock
for a data session and a globe for a research session.

Next: [Read an answer](reading-an-answer.md).
