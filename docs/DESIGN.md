# Design: the Narrator, on paper

DataLab is used by scientists and analysts who need to trust what the agent
did. The interface's job is to make that easy: every turn reads as a short
story of what the agent looked at and did, in plain language, with the
evidence one click away.

Two decisions shape it:

- **Structure: the Narrator.** The redesign compared three directions:
  Clippings, Evidence map, and Narrator. We chose the Narrator, with the
  Clippings idea for detail views. The Evidence map is a possible future
  "How was this made?" view.
- **Look: paper.** A quiet language modelled on
  [understand.cap-study.com](https://understand.cap-study.com/). It reads
  like a well-set page, not a dashboard: a serif for reading, hairline rules
  instead of cards, small uppercase labels, and almost no colour.

## Principles

1. **Say what happened, not what was called.** "Read what's in
   FITBITDAILYDATA (2025)", not `describe_table`. Tool and command names
   appear only inside the detail view.
2. **Show the evidence, friendly first.** Opening a step shows what the agent
   saw: the lab guide as formatted text, a table's columns, the SQL and its
   row count, the files it read. Raw output is the last resort.
3. **The agent's own words are narration.** Commentary between steps is set
   as serif prose between the steps, so the story explains itself.
4. **Fold the repetitive, never the important.** Three or more finished steps
   of one kind fold into one row ("Searched 4 times"). A failed, running, or
   heads-up step is never folded away.
5. **One live line.** While the agent works, one line says what it's doing
   now, or that it's waiting for you, with Stop always next to it.
6. **The answer stands apart.** The final answer sits under a single ink
   rule, with how it was made (the trace) and the review (the agent checking
   its own work) below it.
7. **Colour means something, or it isn't there.** Green is the data session
   and things that checked out; amber is the research session and heads-ups;
   blue is waiting for you; red is an error. Everything else is ink on paper
   (the live "working" dot too), so a coloured word is always worth reading.
8. **Safety is always visible, and exact.** The session line is in the header
   of every conversation, and each conversation in the list says which it is.
   It says only what DataLab enforces:
   - data: "database access, web blocked", with the model on U-M's approved
     GPT service;
   - research: "web access, no database connection". DataLab can't know what
     an attached file contains, so the wording makes no claim about it.
   The trace says numbers were "matched to this turn's outputs", never that
   they are right.

## What a step shows

Everything a step displays comes from event metadata (`tool_call` summaries
built in `backend/src/datalab/sessions/runtime.py`): table names, column
names, row counts, result file names. Never rows. Summaries and command
output reach the browser as untrusted text: they are rendered as text, or
through the hardened Markdown renderer (no raw HTML, no outside images or
links without asking), never as HTML. A command only counts as reading a lab
guide when it is a plain read of the guide file. Exported reports include the
commands the agent ran but never their output, which can show rows.

| Activity | Sentence | Detail view |
| --- | --- | --- |
| Lab guide read | Read the lab's guide on SQL extraction | The guide, as Markdown |
| `search_catalog` / `find_concept` | Looked for tables about "…" | Matching tables with their description |
| `describe_table` | Read what's in X (2025) | Columns, with the other years it's in |
| `join_paths` | Checked how X links to Y | Shared columns and notes |
| `query` | Queried X (2025) | SQL, row count, columns, open the result |
| Script / snippet | Ran analyze.py | The command and its output |
| Files written | Wrote steps.png | Links that open each file |

The mapping lives in `frontend/src/components/chat/activity.ts`, with tests
beside it. Add a case there, not in the components.

## Tokens

Defined once in `frontend/src/styles/index.css` (`@theme`), with a dark
palette that follows the system setting.

- **Paper and ink:** `canvas` and `surface` (the page, white), `sunken`
  (code and SQL), `line` (hairlines); `ink` (warm near-black), `muted`,
  `faint`. Neutrals lean warm.
- **Accent:** the ink itself (`accent`, `accent-soft`, `accent-ink`): primary
  buttons, focus, the rule over the answer. There is no brand colour.
- **Meaning:** `data` (green), `research` and `attn` (amber), `you` (blue,
  waiting for your decision), `danger` (red).
- **Shape:** square corners (radius tokens of 2–6px), hairline borders, no
  shadows except under a dialog.
- **Type:**
  - A book serif for reading: questions, narration, answers, guides, titles.
    It uses the system's Iowan Old Style or Palatino (macOS and Windows both
    have one), so nothing is downloaded.
  - Schibsted Grotesk for controls and step sentences.
  - IBM Plex Mono for data: SQL, table and column names, row counts, paths.
  - `.dl-label` for the small uppercase labels ("THE ANSWER",
    "CONVERSATIONS").
  - The two bundled faces come from fontsource: the browser may only contact
    DataLab.
- **Motion:** `dl-breathe` (the live dot) and `dl-in` (arrivals). Both are
  off under `prefers-reduced-motion`.

## Components

- `components/ui`: `Button` (square: ink, outline or text), `Chip` (a mono
  tag with a hairline border; tones good / attn / bad / you),
  `SessionBadge`, `Panel`, `Modal`, `Tabs`, `Icon` (one stroke style),
  `FileGlyph` (file kind at a glance), `EmptyNote` (what will appear here,
  and when).
- `components/chat/Story.tsx`: the story (`StepRow` and `GroupRow` as ruled
  rows that open with "+", `SayRow` narration, the live `NowCard` line,
  `DetailView`).
- `components/chat/Chat.tsx`: turns, the answer card, trace and review, the
  composer. The chat follows new steps only while you're at the bottom;
  scroll up and it stays put, with "Jump to latest".
- `components/chat/DockedChat.tsx`: the same chat, docked beside a tab's own
  content. It opens a conversation, or starts one of a given mode with the
  first message, and can send context from the tab (the SQL being edited)
  with each message.
- `components/editor`: `CodeEditor` (CodeMirror 6: SQL, YAML, Markdown, R or
  text, with problems marked by line and column) and `DiffView`. Both use the
  paper tokens, so they follow light and dark, and load as their own chunk.
  Syntax is weight and italics, not colour; only problems are coloured.

## Writing

Short, plain sentences, in the second person to the scientist ("You can
attach once the agent has finished"). Say what will happen and when. Empty
states explain what will appear there. No jargon where a plain word works:
"Queries", not "Data accessed events".
