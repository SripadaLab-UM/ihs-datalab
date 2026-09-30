# Walkthrough: The SQL Playground

Status: **draft for review** (the first walkthrough, to settle the format).

| | |
|---|---|
| Subtitle | Ask for the data in plain words, then check, run and refine the query |
| Length | About 2:30 |
| Leads with | The SQL assistant: writing and revising queries from plain-language requests |
| Help guide | [The SQL Playground](../guide/sql-playground.md) |
| Filmed on | The showcase practice DataLab (synthetic data), 1920×1080 |

## How a walkthrough works

The real screen, full frame, filmed shot by shot in one browser session
(`walkthroughs/12-sql-playground.mjs`). While a take runs, DataLab's own
elements are measured, and a spotlight dims the rest of the screen around
the part being explained, with a small label. The narration follows the
Help guide's words.

In each shot, a **spot** line points the spotlight at a target when a phrase
is spoken: `` - `target` · "phrase" · Label``. `none` clears it. The
opening's agenda lines are `- agenda · Line · "phrase"`.

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** The title and agenda.
  - agenda · Ask for a query · "asking for a query"
  - agenda · Check and run it · "checking and running it"
  - agenda · Refine it · "refining it"
  - agenda · Export or save it · "exporting the result"
- **Narration:**
  > This video shows how to get data out of the study database with the
  > SQL assistant. First, asking for a query in plain words. Then,
  > checking and running it, and refining it, with the assistant or by
  > hand. And last, exporting the result, or saving the query as a
  > workflow.

---

### Chapter 2: Ask for a query

**Shot 2.1**

- **Visual:** The empty Playground.
  - `assistant` · "the SQL assistant" · The SQL assistant
  - `editor` · "straight into the editor" · Your editor
  - `tables` · "The catalog" · The catalog
  - `none` · "is on the left"
- **Narration:**
  > On the right is the SQL assistant. Describe the data you want in plain
  > words, and it writes the query for you, straight into the editor in the
  > middle. The catalog of every table is on the left.

**Shot 2.2**

- **Visual:** A request typed to the assistant and sent. The assistant
  works (sped up) and a query appears in the editor.
  - `ask` · "Let's ask" · Your request, in plain words
  - `editor` · "writes a query" · Its query
  - `none` · "you can read"
- **Narration:**
  > Let's ask for the number of 2025 participants with Fitbit data, and
  > their average daily steps, by month. The assistant finds the right
  > tables, with the same tools and rules as any data session, and writes a
  > query you can read.

---

### Chapter 3: Check and run it

**Shot 3.1**

- **Visual:** The check line under the editor.
  - `check` · "DataLab checks" · The SQL check
  - `none` · "never run"
- **Narration:**
  > Before anything runs, DataLab checks the query. Under the editor, it
  > says whether it passes, and which tables it reads. A query that could
  > change the database would fail, and never run.

**Shot 3.2**

- **Visual:** Run pressed; the grid fills.
  - `run` · "Press Run" · Run
  - `grid` · "The grid shows" · The first rows
  - `meta` · "how many rows" · Rows and time
  - `none` · "until you export it"
- **Narration:**
  > Press Run. The grid shows the first rows, with how many rows there are
  > and how long the query took. The whole result stays in DataLab, out of
  > the agent's reach, until you export it.

---

### Chapter 4: Refine it

**Shot 4.1**

- **Visual:** "Send with message" ticked; "Show it by week instead of by
  month." sent. The assistant revises the query (sped up); Run again.
  - `sendwith` · "Tick Send with your message" · The query goes with it
  - `ask` · "say what you'd like" · What to change
  - `editor` · "revises the query" · The revised query
  - `run` · "run it again" · Run it again
- **Narration:**
  > To change it, just ask. Tick Send with your message, so the assistant
  > sees the query in the editor, and say what you'd like. Here, by week
  > instead of by month. It revises the query in place, and you run it
  > again.

**Shot 4.2**

- **Visual:** A line typed at the end of the query; the check stays green;
  a search in the catalog.
  - `editor` · "edit it yourself" · Edit by hand
  - `check` · "The check follows" · Checked as you type
  - `search` · "the catalog" · Any table or column
- **Narration:**
  > You can always edit it yourself, too. Here, keeping just the first ten
  > rows. The check follows every change, and the catalog on the left puts
  > any table or column name into your query.

---

### Chapter 5: Export or save it

**Shot 5.1**

- **Visual:** The History tab; then Export and its dialog.
  - `history` · "History keeps" · History
  - `export` · "Export copies" · Export
  - `dialog` · "a note of the query" · Where it goes
- **Narration:**
  > History keeps every query you've run. Export copies the full result to
  > one of your export folders, with a note of the query that made it.

**Shot 5.2**

- **Visual:** Save as workflow and its dialog.
  - `saveflow` · "Save as workflow" · Save as workflow
  - `dialog` · "You review the draft" · The workflow draft
- **Narration:**
  > And Save as workflow turns the query into a workflow, for DataLab to
  > run the same way every time, with no AI involved. You review the draft
  > before anything is saved.

**Shot 5.3**

- **Visual:** The end card.
- **Narration:**
  > That's the SQL Playground, and its assistant. There's more in Help,
  > under The SQL Playground.
