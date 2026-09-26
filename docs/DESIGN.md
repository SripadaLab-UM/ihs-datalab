# Design: the Narrator

DataLab is used by scientists and analysts who need to trust what the agent
did. The interface's job is to make that easy: every turn reads as a short
story of what the agent looked at and did, in plain language, with the
evidence one click away.

The redesign compared three directions: Clippings, Evidence map, and
Narrator. We chose the Narrator, with the Clippings idea for detail views.
The Evidence map is a possible future "How was this made?" view.

## Principles

1. **Say what happened, not what was called.** "Read what's in
   FITBITDAILYDATA (2025)", not `describe_table`. Tool and command names
   appear only inside the detail view.
2. **Show the evidence, friendly first.** Opening a step shows what the agent
   saw: the lab guide as formatted text, a table's columns, the SQL and its
   row count, the files it read. Raw output is the last resort.
3. **The agent's own words are narration.** Commentary between steps is shown
   as plain prose on the timeline, so the story explains itself.
4. **Fold the repetitive, never the important.** Three or more finished steps
   of one kind fold into one row ("Searched 4 times"). A failed, running, or
   heads-up step is never folded away.
5. **One live line.** While the agent works, one card says what it's doing
   now, or that it's waiting for you, with Stop always next to it.
6. **The answer stands apart.** The final answer is its own card, with how it
   was made (the trace) and the review (the agent checking its own work)
   below it.
7. **Safety is always visible.** The session badge (data: no internet;
   research: no study data) is in the header of every conversation, and the
   colours match: emerald for data, amber for research.

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

- **Ground and ink:** `canvas`, `surface`, `sunken`, `line`; `ink`, `muted`,
  `faint`. Neutrals lean slightly cool.
- **Accent:** `accent` (emerald), `accent-soft`, `accent-ink` (text on accent).
- **Meaning:** `data` (emerald), `research` and `attn` (amber), `you` (blue,
  the person's own messages and choices), `danger`.
- **Type:** Schibsted Grotesk for text, IBM Plex Mono for data, paths and
  code. Both are bundled (fontsource): the browser may only contact DataLab.
- **Motion:** `dl-breathe` (live), `dl-slide` / `dl-in` (arrivals). All are
  off under `prefers-reduced-motion`.

## Components

- `components/ui`: `Button` (pill), `Chip` (tones good / attn / bad / you),
  `SessionBadge`, `Panel`, `Modal`, `Tabs`, `Icon` (one stroke style),
  `FileGlyph` (file kind at a glance), `EmptyNote` (what will appear here,
  and when).
- `components/chat/Story.tsx`: the timeline (`StepRow`, `GroupRow`,
  `SayRow`, `NowCard`, `DetailView`).
- `components/chat/Chat.tsx`: turns, the answer card, trace and review, the
  composer. The chat follows new steps only while you're at the bottom;
  scroll up and it stays put, with "Jump to latest".

## Writing

Short, plain sentences, in the second person to the scientist ("You can
attach once the agent has finished"). Say what will happen and when. Empty
states explain what will appear there. No jargon where a plain word works:
"Queries", not "Data accessed events".
