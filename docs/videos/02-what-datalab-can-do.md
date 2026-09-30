# Video 2: What DataLab can do

Status: **draft for the lab's review.** Narration and animation are made.
The app footage isn't recorded yet, so the app panel shows a placeholder.

| | |
|---|---|
| Length | 2:46 (about 540 words of narration) |
| For | Every user, and collaborators thinking about what they'd like DataLab to do |
| Chapters | This video covers · The toolkit · The lab's knowledge · Workflows · Asking for more |
| Voice | Amazon Polly, generative engine, Ruth |
| Recorded on | The practice profile (synthetic data), on Mac |

## The idea

Video 1 was about safety. This one is about what DataLab can *do*, and the
point it builds to is that DataLab grows. Everything it does is made from
the same five pieces: **tools, skills, knowledge, connections and
workflows**. The picture is one shelf with a row for each piece. The rows
fill in as the video goes, and in the last chapter, requests a collaborator
might make (Stata, a new data source, email) drop into the row where they'd
fit. The message: ask for what your work needs, and it's added inside the
same safety rules.

## Rules for this video

The rules from video 1 apply (synthetic data only, no internal names,
"sealed environment", no "U-M" in the narration). Also:

- **Built today, or said as a possibility.** The toolkit, skills, SQL
  Playground and the workflow runner (Run again, Replay) are built. The
  knowledge base works behind the scenes, but its screens (the Knowledge tab
  and the proposed-edit card) aren't built yet, so chapter 3 is animation
  only. See open item 1. Every extension in chapter 5 is an example of what
  *could* be added, never a promise.
- **Extensions keep the safety model.** Each example is described inside
  video 1's rules: new data sources are read-only and logged; email is
  drafted by the AI and sent by you.
- **No illustration passes as the app.** Where the app panel shows an
  animation instead of footage (the knowledge edit, the requests), it's
  drawn as a diagram, not as a lookalike of a screen.

## The picture

Left, the shelf: five rows, each with a label and the things on it. Right,
the **app panel**: a 16:9 window that shows footage of the app, or an
illustration, for the row being talked about, with a leader line to that
row.

```
 WHAT THE AI WORKS WITH                                  ┌──── IN THE APP ────┐
 TOOLS        [Python] [R] [SQL]  pandas · lme4 · …      │                    │
 SKILLS       ▭ Statistical review ▭ Figures ▭ …         │   footage, or an   │
 KNOWLEDGE    ▭ Tables ▭ Device quirks ▭ Cohorts … ⇄ GitHub │   illustration     │
 CONNECTIONS  Study database (read-only) · Research helper│                    │
 WORKFLOWS    Weekly export ▸ Daily metrics ▸   no AI     └────────────────────┘
```

In chapter 5, request cards appear in the app panel's place and drop onto
their row, marked in the "you" colour so they read as requests, not as
things already there.

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** "This video covers" and four numbered lines, each set as it's
  spoken: 1 The toolkit · 2 The lab's knowledge · 3 Workflows · 4 Asking for
  more. They fade, and the shelf's heading and row labels draw in.
- **Narration:**
  > This video is about what you can do in DataLab, and how it grows.
  > First, the toolkit the AI works with. Then, how it learns the lab's
  > knowledge, and how work becomes a repeatable workflow. And last, how to
  > ask for something new.

---

### Chapter 2: The toolkit

**Shot 2.1**

- **Visual:** The TOOLS row fills: Python and R tiles, then package names
  under each as they're spoken. **App panel (footage):** "How this answer
  was made" opened on a step where the AI ran its own Python code.
- **Narration:**
  > Inside its sealed environment, the AI has a full data science toolkit.
  > It writes and runs Python and R: pandas, statsmodels and scikit-learn,
  > the tidyverse, mixed models and survival analysis. It makes the charts,
  > tables and reports, and you can read every line of code it ran.

**Shot 2.2**

- **Visual:** A SQL tile joins the row. The CONNECTIONS row draws in with
  "Study database · read-only". **App panel (footage):** the SQL Playground:
  a query typed, run, and the catalog beside it.
- **Narration:**
  > For the study database, it writes SQL and runs it through DataLab,
  > read-only. And when you'd rather write a query yourself, the SQL
  > Playground has an editor, with the catalog of every table beside it.

**Shot 2.3**

- **Visual:** The SKILLS row fills with cards: Statistical review · Figures
  for papers · Reproducible reports · Data extraction. One card lifts as it
  would be "loaded". **App panel (footage):** an answer's "How this answer
  was made", zooming to "4 lab guides read".
- **Narration:**
  > It also follows skills: short written guides to how the lab does
  > things, from checking a statistical model to preparing a figure for a
  > paper. A skill is loaded only when it's needed, and each answer shows
  > which guides it read.

---

### Chapter 3: The lab's knowledge

**Shot 3.1**

- **Visual:** The KNOWLEDGE row fills with page tiles: Tables · Device
  quirks · Cleaning rules · Cohorts · Checked queries, and a link out to
  "The lab's GitHub". A page tile slides toward the TOOLS row's AI as it's
  "read". **App panel (illustration):** a single knowledge page drawn as a
  diagram: a title, a few lines, a "reviewed" tag.
- **Narration:**
  > Then there's the knowledge base: the lab's shared notes about the study
  > data. What each table means, device quirks, cleaning rules, cohort
  > definitions, and queries the lab has already checked. It's a set of
  > plain pages kept in the lab's GitHub, and the AI reads them before it
  > relies on a table.

**Shot 3.2**

- **Visual:** **App panel (illustration):** a proposed edit: a page with one
  line struck through and one added (in green), a tick for "no participant
  data", then a "Save & share" arrow up to GitHub. A new tile joins the
  KNOWLEDGE row.
- **Narration:**
  > When the AI learns something worth keeping, it proposes an edit. You see
  > the exact change, and nothing is shared until you save it. A check makes
  > sure no participant data goes in. So each conversation leaves the next
  > one a little better informed.

---

### Chapter 4: Workflows

**Shot 4.1**

- **Visual:** The WORKFLOWS row draws in: "Weekly export ▸ Daily metrics",
  with a "no AI" badge; a run moves along it and lands as a file in a
  folder. **App panel (footage):** the Workflows tab: a workflow's page, its
  runs, "Run again" and "Replay".
- **Narration:**
  > Some work has to be done the same way every time, like a regular export
  > or a set of daily metrics. That becomes a workflow. It's written with
  > the AI's help and reviewed by a person, and then DataLab runs it with no
  > AI involved. Every run keeps a record, so you can run it again on
  > today's data, or replay the original exactly.

---

### Chapter 5: Asking for more

**Shot 5.1**

- **Visual:** The app panel steps aside. The five row labels light up in
  turn: tools, skills, knowledge, connections, workflows.
- **Narration:**
  > Everything you've seen is built from the same five pieces: tools,
  > skills, knowledge, connections and workflows. That makes DataLab easy to
  > extend, so the lab can ask for more.

**Shot 5.2**

- **Visual:** Request cards appear on the right and drop onto their rows, in
  the "you" colour: "Stata or SAS" → TOOLS · "Our figure style" → SKILLS ·
  "What 'enrolled' means" → KNOWLEDGE · "Another data source" → CONNECTIONS,
  with a small "read-only · logged" tag · "Email" → CONNECTIONS, with a
  "you send it" tag.
- **Narration:**
  > A statistics package like Stata or SAS could join the toolkit. A lab
  > habit, like your figure style, becomes a skill, and a definition becomes
  > a knowledge page, with no code at all. A new data source, like a survey
  > platform or another database, becomes a connection: read-only and
  > logged, like the study database. Even email could fit: the AI drafts,
  > and you review and send.

**Shot 5.3**

- **Visual:** The whole shelf, with the new requests on it. A line under it:
  "Added inside the same safety rules". Then "More in Help".
- **Narration:**
  > Whatever it is, a new piece is added inside the same safety rules, and
  > tested the same way. If your work needs something DataLab doesn't do
  > yet, ask the DataLab team.

---

## Production notes

- Built with the same pipeline as video 1 (see [README.md](README.md)):
  `scripts/narrate-video.py`, `animation/02-what-datalab-can-do.html`, and
  `render.mjs`.
- **Footage to film** (practice DataLab, same blur): a code step in "How
  this answer was made" (2.1); the SQL Playground (2.2); the "lab guides
  read" chip (2.3); the Workflows tab with a run, Run again and Replay (4.1).

## Open items for review

1. **The knowledge base's screens aren't built.** Chapter 3 describes the
   proposed-edit flow (see the change, Save & share, the check), which works
   in the backend but has no UI yet. Publish once the Knowledge tab and the
   proposal card ship, or reword chapter 3 to "coming soon"?
2. **Workflow authoring.** "Written with the AI's help and reviewed by a
   person" describes the design; today the Workflows tab is read-only and
   workflows are added through the lab's pipelines repo. Same choice.
3. **The extension examples.** Stata/SAS (licences), a survey platform or
   another database, and email are examples of requests, not plans. Is the
   lab comfortable showing them, and is "ask the DataLab team" the right
   ask? (A contact or a Help link could go on the end card.)
4. **Email.** Framed as "the AI drafts, you send", so nothing leaves without
   you, in line with video 1. Anything more automatic would need its own
   safety review.
