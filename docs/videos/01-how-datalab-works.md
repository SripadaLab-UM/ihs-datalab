# Video 1: How DataLab works

Status: **draft for the lab's review.** Narration and animation are made.
The app footage isn't recorded yet, so each footage window shows a
placeholder.

| | |
|---|---|
| Length | About 3:50 (about 650 words of narration) |
| For | Every new user, PIs, and anyone reviewing DataLab's safety |
| Chapters | This video covers · The question goes in · The answer comes back · How the AI is tested · Good habits |
| Voice | Amazon Polly, generative engine, Ruth |
| Recorded on | The practice profile (synthetic data), on Mac |

## The idea

One question, followed from start to finish on one map. Part 1 follows the
question in and shows what keeps study data safe on the way. Part 2 follows
the answer back out, and every check on the answer is a stop on that trip,
in the order it happens in a real conversation. Part 3 is a slide of its
own: how the AI itself is tested. Part 4 puts each habit on its spot on the
map. The app footage appears in small windows pinned to the part of the map
they belong to.

## Rules for this video

- **Synthetic data only.** The narration goes to Polly and the video is
  public, so every frame and every word comes from the practice profile.
- **Claims match the code.** Each safety claim below is built and covered by
  tests or the Safety check. Anything that isn't built yet (the knowledge base,
  outside connectors, Windows) is left out. The video is re-rendered when
  those arrive.
- **Careful wording**:
  - "The AI can't change the database", not "the account is read-only".
    Until the DBA removes the write role, DataLab switches on only the
    read-only roles (see PRODUCT.md to-dos).
  - "The University of Michigan's approved GPT service": the approval is
    the university's, not ours. The narration never says "U-M", which Polly
    reads as separate letters; the map can still show "U-M GPT".
  - "Sealed environment", not "box" or "container".
  - Secrets: "locked in your computer's secure keychain" (macOS Keychain,
    Windows Credential Manager), not "DataLab holds the password".
  - The rigor review and the number check are aids to judgement, never
    "validation".
  - No evaluation scores on screen. The video shows how quality is measured.
- **No internal names.** No service account, schema, or real table names in
  the narration. See open item 1 for the footage.
- **No product pitch.** The video explains how a question is handled; it
  doesn't introduce the study or sell the app, and it doesn't point out that
  the footage is practice data (that's for Help).

## The map

It builds up through part 1 and stays on screen to the end, in the paper
look: hairline boxes, serif labels, small uppercase captions. Colour appears
only where it means something: green for the data session and things that
checked out, amber for the research session, red for refusals.

Final state:

```
┌──────────────────────────── YOUR COMPUTER ────────────────────────────┐
│                                                                       │
│  Browser ──▶ ┌───────────────── DataLab ─────────────────┐            │
│              │  Plan          Rigor review │  Relay ──────┼────────────┼──▶ U-M GPT
│              │  Checkpoints   Log          │               │            │
│  Folder ◀────┼─ Export        Safety check │  Data service ┼────────────┼──▶ Study database
│              └─────────────────────────────┴───────────────┘            │
│                              │ two kinds of request only                │
│   ┌──── DATA SESSION ────────┴────┐      ┌──── RESEARCH SESSION ───┐     │
│   │ [gateway]                     │      │ [gateway]      proxy ───┼─────┼──▶ The web
│   │  AI agent                     │      │  AI agent               │     │
│   │  workspace · your files 🔒    │      │  no database            │     │
│   └───────────────────────────────┘      └─────────────────────────┘     │
│                    Research helper (appears, then is deleted)          │
└───────────────────────────────────────────────────────────────────────┘
```

**Moving parts.** Requests move as dots along the wires. A refused request
stops at a boundary with a red ✕. Cards (the plan, the answer, a file) move
the same way.

**Footage windows.** A 16:9 window with a thin leader line to its block.
Until the footage is recorded, it shows what will be filmed. Each is marked
**F** below.

## Script and storyboard

Beats are tied to the words: the animation looks up when each phrase is
spoken in the narration, so editing a line here and re-running the
narration keeps picture and sound in step.

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** On blank paper, the label "This video covers" and four
  numbered lines, each set as it's spoken: 1 The question goes in · 2 The
  answer comes back · 3 How the AI is tested · 4 Good habits. The list fades
  and the YOUR COMPUTER frame draws in.
- **Narration:**
  > This video follows one question through DataLab, from start to finish.
  > First, where the question goes, and what keeps study data safe on the
  > way. Then, how the answer comes back, and what helps you trust it. After
  > that, how the AI itself is tested. And last, a few habits that help.

---

### Chapter 2: The question goes in

**Shot 2.1**

- **Visual:** The Browser box appears with the question in it.
  **F** window pinned to it: the New conversation dialog, Data session
  selected, the question typed and sent.
- **Narration:**
  > Here's our question: did daily steps change during the intern year?

**Shot 2.2**

- **Visual:** The DataLab box draws in, and an arrow joins it to the
  browser inside the frame.
- **Narration:**
  > DataLab runs on your own computer. You use it in your browser, but the
  > page comes from DataLab itself, not from a website.

**Shot 2.3**

- **Visual:** The DATA SESSION frame draws in (green), captioned "A sealed
  environment per conversation". Inside: the AI agent, its workspace, and
  your files with a small lock. The frame's edge thickens as it's sealed; a
  dot heading out stops at the edge with a ✕.
- **Narration:**
  > When you start a conversation, the AI gets its own sealed environment.
  > It sees only its workspace and the files you attach, which it can read
  > but not change. The environment has no route to the outside world.

**Shot 2.4**

- **Visual:** A gateway appears on the environment's top edge, with its wire up to
  DataLab ("two kinds of request only"). Dots labelled "model" and "data"
  pass; "anything else" stops at the gateway with a ✕.
- **Narration:**
  > There's one door. A gateway lets through exactly two kinds of request:
  > calls to the AI model and calls for data. Everything else stops there.

**Shot 2.5**

- **Visual:** A model call reaches the Relay. The relay lights; the
  request leaves for U-M GPT with the key. "The key stays outside" appears
  inside the data session. A web search and a fetch-a-link request are
  refused at the relay.
- **Narration:**
  > Calls to the model go to DataLab's relay. It checks each one, adds the
  > key at the last moment, and sends it on to the University of Michigan's
  > approved GPT service. The key never enters the environment. The relay
  > also refuses anything that could send data somewhere else, like a web
  > search or a link for the provider to fetch.

**Shot 2.6**

- **Visual:** A plan card travels from the agent into DataLab's Plan block
  and on to the browser. **F** window: the plan card (Question, Estimand,
  Exposure, Outcome, Covariates, Cohort), one field edited, "Approve plan".
  The Plan block gets a lock: frozen.
- **Narration:**
  > Before it touches outcome data, the AI drafts an analysis plan: the
  > question, the estimand, the cohort. You edit it and approve it, and it's
  > frozen, so later work is labelled as following the plan, or as
  > exploratory.

**Shot 2.7**

- **Visual:** A data call reaches the Data service. A padlock on it: "Login
  in the keychain". Beside the database, four ticks: one read-only query ·
  checked before it runs · a time limit · a size limit. The query goes out,
  a table comes back into the workspace, and a line is written in the Log.
- **Narration:**
  > Calls for data go to DataLab's data service. The database login stays
  > locked in your computer's secure keychain, out of the AI's reach. The
  > service takes one read-only query at a time, checks it before it runs,
  > and limits how long it can take and how much it can return. The result
  > comes back as a file in the conversation's workspace, and the query is
  > written to a log.

**Shot 2.8**

- **Visual:** The RESEARCH SESSION box draws in (amber) with its own gateway
  and a proxy to the web. A request from it toward the data service is
  refused. Then the data agent drafts a question card. **F** window: the
  approval card, "What will be sent", with "Don't send" and "Send to the
  research helper". The card goes to a Research helper box, an answer comes
  back, and the helper fades away.
- **Narration:**
  > Some questions need the web: a paper, a package, a method. That's a
  > research session, with the internet but no connection to the database.
  > If a data session needs to look something up, you see exactly what will
  > be sent, and you decide. The helper gets only that question, answers,
  > and is deleted.

---

### Chapter 3: The answer comes back

**Shot 3.1**

- **Visual:** New files (a chart, a report) appear in the workspace. The
  Checkpoints block ticks. **F** window: the History tab's checkpoints.
- **Narration:**
  > The AI works on those results inside its environment. After every turn, DataLab
  > saves a checkpoint of its workspace, so a bad step can be undone.

**Shot 3.2**

- **Visual:** An answer card travels from the agent back through DataLab to
  the browser. On arrival, a green chip under the browser: "all numbers
  matched". **F** window: the answer with its number check.
- **Narration:**
  > Then the answer travels back to you. On arrival, DataLab checks every
  > number in it against what the work actually produced, and flags any it
  > can't find.

**Shot 3.3**

- **Visual:** The Rigor review block lights, and a second card goes from it
  to the browser. **F** window: the Rigor review switch, and the review under
  the answer.
- **Narration:**
  > With rigor review on, a second pass checks the work against a checklist:
  > the plan, causal language, sample sizes, uncertainty, privacy. It's a
  > prompt for your judgement, not a replacement for it.

**Shot 3.4**

- **Visual:** The Log lights, with a dashed line to the browser. **F**
  window: a step's exact SQL, then the Queries tab (tables, time, rows).
- **Narration:**
  > Every step shows the exact SQL, and the conversation lists every query
  > the AI ran: which tables, when, and how many rows.

**Shot 3.5**

- **Visual:** The Export block lights; a file, then a report, go to the
  folder you chose. **F** window: the Export dialog, then Export
  conversation.
- **Narration:**
  > Nothing leaves the environment unless you export it, to a folder you chose. The
  > whole conversation can go too, as a report that opens in any browser.

**Shot 3.6**

- **Visual:** The Safety check block lights and replays part 1's refused
  requests on the map: out of the box, past the gateway, a web search at the
  relay, research to the database. Each ✕ turns into a green ✓. **F**
  window: Settings, "Run safety check", "Every check passed" under the three
  promises (sped up, marked so).
- **Narration:**
  > You don't have to take the safety on trust. The Safety check, in
  > Settings, tests it for real: it tries to reach the web from inside a
  > session, looks for keys where they shouldn't be, confirms the database
  > connection can't write, and more.

### Chapter 4: How the AI is tested

**Shot 4.1**

- **Visual:** A slide of its own (the map steps aside): "Questions with
  known answers", on a synthetic database. Five cards turn over one by one,
  each naming a trap: duplicate rows · screened, never enrolled · empty
  tables · small groups · who stops answering. A tick lands under each as
  it's graded. No scores.
- **Narration:**
  > The AI itself is tested, with questions whose answers we already know,
  > on a synthetic database. Each question hides a trap real data sets. The
  > answers are graded independently, and we re-run the tests whenever the
  > model or its instructions change.

---

### Chapter 5: Good habits

**Shot 5.1**

- **Visual:** The map returns. Each habit lights its spot with a numbered
  chip: 1 say what you want, at the browser · 2 read it before you send it, at
  the research helper · 3 plans and rigor review, at those blocks · 4 check
  flagged numbers, at the browser.
- **Narration:**
  > A few habits help. Ask the way you'd ask a colleague: which cohort,
  > what to measure, and what it's for. Read every research question
  > before you send it. Treat the plan as your record of intent, and keep
  > rigor review on. And check flagged numbers, and ask for the SQL.

**Shot 5.2**

- **Visual:** The whole map, quiet, with "More in Help" beneath it.
- **Narration:**
  > That's one question, from start to finish. There's more in Help.

---

## Production notes

- **A replayable take.** The footage windows need the same conversation
  every time, without waiting on the model. The plan is to record one good
  real run on the practice profile and replay its event stream while
  filming. The test stand-in for Codex (`backend/tests/fake_app_server.py`)
  may be a starting point. The Safety check is filmed live.
- **Frame:** 1920×1080 at 30 fps, light theme. Footage windows are 16:9, so
  recordings drop in without cropping.
- **Captions:** burned in, plus an `.srt` file, both from the narration.
- **Chapter markers** on the website player at the starts of chapters 2–5.
- **How it's built:** see [README.md](README.md).

## Open items for review

1. **Real table names in footage.** The synthetic schema reuses real IHS
   table and column names, and the footage windows for the data steps and
   the Queries tab would show some. Options: keep steps collapsed and blur
   the table column, or wait for the decision on the synthetic schema in
   PRODUCT.md's to-dos.
2. **Safety wording.** Is the lab (and, if needed, compliance or the IRB)
   happy with every claim as written? This video is public.
3. **The practice question.** "Did daily steps change during the intern
   year?" matches the milestone 3 journey, so the replay take is known to
   work. Is there a question the lab would rather show?
4. **Out-of-date docs.** SAFETY.md and ARCHITECTURE.md still say "not yet
   implemented". SAFETY.md also says the Safety check runs at startup, but
   today it runs only when you click it. The script only says what the code
   does. Fix the docs, or make the check run at startup?
