# Walkthrough: Workflows

Status: **draft for review.**

| | |
|---|---|
| Subtitle | Recipes the lab has approved, changed with the assistant, run with no AI |
| Length | About 2:00 |
| Leads with | The Workflow assistant: changing a workflow from a plain-language request |
| Help guide | [Run a workflow](../guide/running-a-workflow.md) |
| Filmed on | The showcase practice DataLab (synthetic data), 1920×1080 |

How the spotlight cues work: see [12-sql-playground.md](12-sql-playground.md).

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** The title and agenda.
  - agenda · Change one with the assistant · "changing a workflow with the assistant"
  - agenda · Run a workflow · "running a workflow"
  - agenda · The run record · "the record"
  - agenda · Run again, or Replay · "run it again"
- **Narration:**
  > This video walks through the Workflows tab. First, changing a workflow
  > with the assistant. Then, running a workflow, and the
  > record every run keeps. And last, two ways to run it again.

---

### Chapter 2: Change one with the assistant

**Shot 2.1**

- **Visual:** The Workflows tab.
  - `list` · "the lab's workflows" · The lab's workflows
  - `chat` · "the Workflow assistant" · The Workflow assistant
  - `none` · "change them"
- **Narration:**
  > The Workflows tab holds the lab's workflows: recipes a person has
  > approved, which DataLab runs the same way every time. On the right, the
  > Workflow assistant helps you create them and change them.

**Shot 2.2**

- **Visual:** A workflow opened (the 2025 daily mood export). "Send with
  message" ticked; a request typed and sent; the assistant works (sped up)
  and reports its checked change.
  - `sendwith` · "tick Send with your message" · The file goes with it
  - `ask` · "ask for a change" · Your request
  - `chat` · "checks it with DataLab's" · Its change, checked
  - `none` · "never saves"
- **Narration:**
  > Open a workflow, tick Send with your message so the assistant sees the
  > file, and ask for a change. Here, a check that there's only one row per
  > participant per day. The assistant edits its own copy, checks it with
  > DataLab's workflow check, and hands the change back to you to review.
  > It never saves anything itself.

---

### Chapter 3: Run a workflow

**Shot 3.1**

- **Visual:** The parameters form; Run pressed.
  - `form` · "fill in its parameters" · Parameters
  - `run` · "press Run" · Run
  - `none` · "exactly as written"
- **Narration:**
  > To run a workflow, fill in its parameters and press Run. No AI is
  > involved: each step runs exactly as written.

**Shot 3.2**

- **Visual:** The steps, done, with their times.
  - `steps` · "Each step" · The steps
  - `none` · "every check passes"
- **Narration:**
  > Each step shows as it runs: the SQL extract, read-only through DataLab,
  > the R step, in a container with no network, and the checks. Files are
  > delivered only if every check passes.

---

### Chapter 4: The run record

**Shot 4.1**

- **Visual:** The run opened from Runs: its delivery, and what it pinned.
  - `delivery` · "Where its files went" · Delivery
  - `pinned` · "everything it pinned" · What the run pinned
  - `none` · "the data it extracted"
- **Narration:**
  > Every run keeps a record. Where its files went, with their checksums,
  > and everything it pinned: the workflow file, the parameters, the
  > software, and the data it extracted.

---

### Chapter 5: Run again, or Replay

**Shot 5.1**

- **Visual:** Repeat this run: Run again and Replay; Replay's dialog opens.
  - `again` · "Run again" · Run again
  - `replay` · "Replay reruns" · Replay
  - `dialog` · "reproduce its results" · Exactly as before
- **Narration:**
  > There are two ways to repeat a run. Run again extracts fresh from
  > today's database, so its results can differ. Replay reruns the
  > processing on the data this run extracted, to reproduce its results
  > exactly.

**Shot 5.2**

- **Visual:** The end card.
- **Narration:**
  > That's the Workflows tab. There's more in Help, under Run a workflow.
