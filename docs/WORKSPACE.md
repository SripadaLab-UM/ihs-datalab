# Scientific workspace

Status: **implemented**, except where a feature is marked **planned** or
**not built**. "Built" means implemented and covered by automated tests, not
accepted: readiness is tracked in [PRODUCT.md](../PRODUCT.md), "v1
readiness", which also has the modes' policy matrix.

The workspace is where researchers work with the agent. It is built on top of
the safety platform ([SAFETY.md](SAFETY.md)) and the lab knowledge base
([KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)).

## Modes

A mode is a **starting point**, not a restriction. It sets the agent's
instructions and the suggested prompts. You pick a mode when you start a
conversation.

| Mode | Session | For | The agent's priorities |
|---|---|---|---|
| **Analysis** (default) | 🔒 Data | PIs and researchers asking scientific questions | Make the question, unit of analysis, and estimand explicit. Run a small pilot before any large run and ask before scaling up. Check data quality before modeling. Report effect sizes with uncertainty. Never overclaim causality. |
| **Data extraction** | 🔒 Data | Getting a clean, documented dataset out of Oracle | Find tables through the catalog and knowledge base, and check columns before relying on them. Keep exploratory queries separate from the final extraction. Always show the exact SQL that was run. |
| **Data engineering** | 🔒 Data | Yu and others maintaining the lab's R pipelines (`ihsDataR`) | Work in a copy of the package, follow its conventions, run its tests, and hand back a reviewable change. |
| **Research** | 🌐 Research | Literature, methods, packages, ideas | Cite sources. Produce notes and code that can be brought into a data session. |

Analysis, Data extraction and Data engineering also have `suggest_kb_update`:
a durable, evidence-backed finding (confirmed by a query in the
conversation) becomes a **Suggested Knowledge update** card under the
answer, which the person accepts as a draft edit, edits first, or dismisses.
At most one or two an answer; it never writes the knowledge base. The person
can make one themselves with **Propose a Knowledge update** over the
conversation. See [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md), "Suggested
Knowledge updates".

Four more modes open only from the tab that docks them, not from **New
conversation** (built):

| Mode | Session | Docked in | The agent's priorities |
|---|---|---|---|
| **SQL drafting** | 🔒 Data | SQL Playground | Find the tables and check every column, run only small profiling queries, and propose one query into the editor with `propose_sql` (its binds, assumptions, and the tables and pages it used). It never runs the final query; the person does. No attachments. |
| **Pipelines** | 🔒 Data | Pipelines tab | Data engineering's rules, for the code the tab shows: explain, edit and test it in its copy of the pipelines repo (`/work/pipelines`). Its changes become a Pipelines proposal. The catalog, `query`, `check_workflow` and the research helper; no analysis plans, no attachments. |
| **Workflow authoring** | 🔒 Data | Workflows tab | Draft or change workflow files in its copy of the pipelines repo (`/work/pipelines/workflows/`), following [WORKFLOWS.md](WORKFLOWS.md): declared `reads:`, QC with `small_cells` on every delivered CSV, destination keys. It checks each draft with `check_workflow`, DataLab's own workflow check. Its changes become a Pipelines proposal, which a person reviews, tests, and saves there; the agent never saves. The database tools as in Data extraction, read-only. |
| **Knowledge writing** | 🔒 Data | Knowledge tab | Write or tidy a page or lab skill in `/work/kb`, in the layout and page format of [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md), as proposed-edit cards. Never sets `reviewed_by`, `reviewed_on`, or a page's status. The catalog tools only (metadata): its session token allows just `search_catalog`, `describe_table`, `join_paths` and `find_concept`, DataLab refuses every other data tool, and Codex doesn't list them. Nothing can be attached (a file could hold study data); the page open in the tab can go with a message. |

## How the agent is instructed

Everything uses native Codex mechanisms, arranged in four layers:

1. **Base instructions**, the same for every session. An `AGENTS.md` in the
   session's Codex home folder, about 100 lines, covering:
   - where things are in the container (next section);
   - the safety rules in plain terms;
   - how to present results: inline charts, offline HTML reports, and
     deliverables in `outputs/`;
   - that the knowledge base exists and how to use it;
   - what the data tool and research helper are for.
2. **Mode instructions**, set as Codex developer instructions when the
   conversation starts. They persist across turns and restarts. The prototype
   pasted them into the first user message, where they were lost on restart.
3. **Skills**, loaded only when relevant. There are two sets:
   - **App skills**, shipped in the agent image and tied to how DataLab
     works: outputs, reports, the data tool, and the research helper.
   - **Lab skills**, kept in the knowledge base repo so the lab can improve
     them collectively through reviewed edits. Examples: statistical review,
     figure standards, feature porting, and how the lab handles specific
     devices.
4. **Project instructions.** Repositories the agent works in carry their own
   `AGENTS.md`, which Codex loads automatically. For example, the `ihsDataR`
   repo explains its own layout and conventions. That replaces the
   prototype's 2.8k-line package server.

### Skills carried over from the prototype

| Prototype skill | v1 | Notes |
|---|---|---|
| `statistical-review` | Lab skill | Good content; keep it. |
| `academic-figures` | Lab skill | Good content; keep it. |
| `data-analysis` | App skill | Update the paths. |
| `reproducible-report` | App skill | Merge with `artifact-contract`. |
| `artifact-contract` | App skill | Merged into `reproducible-report` and the base `AGENTS.md`. |
| `ihs-feature-factory` | Lab skill, rewritten | Remove the dependencies on the Spine and the custom servers. |
| (new) `kb-use`, `kb-propose`, `kb-maintain` | App skills | They describe DataLab's own flow for the knowledge base, so they ship in the image. The lab's own skills load from `/work/kb/skills`. See [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md). |
| (new) `sql-extraction` | App skill | The good parts of the prototype's SQL Playground mode: catalog first, check columns, profiling separate from delivery, show the SQL. |
| (new) `research-helper` | App skill | When to ask, how to phrase a question that contains no data, and to ask sparingly. |

`statistical-review` and `academic-figures` still ship in the agent image as
app skills (`images/agent/skills`), adjusted for v1's folders and for
previews with scripts off. Moving them to the knowledge base, so the lab can
edit them, is planned.

The prototype's mode prompts contain careful scientific guidance worth keeping
nearly word for word: pilot-first, estimands, within-person versus
between-person effects, and no causal overclaiming. Their tool-by-tool recipes
for the prototype's servers are dropped.

## What's inside the container

The container has deliberately few locations:

| Path | What | Agent can write? |
|---|---|---|
| `/work` | The conversation's workspace, checkpointed after every turn | Yes |
| `/work/outputs` | Deliverables shown in the Outputs panel and available to export | Yes |
| `/work/kb` | Fresh copy of the knowledge base; edits become proposals. Not on the practice DataLab (the agent is told so) | Yes |
| `/inputs` | Files and folders you attached | No |
| `/data/oracle` | Query results written by DataLab's data service | No |

## Scientific rigor

Evidence from 2026 shapes these features:

- A *PNAS* study ran about 5,000 AI analyst runs on one dataset. Its
  conclusions ranged from negative to positive effects, depending largely on
  how the agent was steered.
- A study of Codex itself on 11 real datasets found that 6 had conclusions the
  evidence didn't support, and that Codex's confidence didn't track the
  stability of its results.

On observational health data, the value is in making the agent's work
**planned, traceable, and checked**, not in making it more autonomous.

### Analysis plans (built)

What this guarantees: the plan the person approved is recorded, frozen, and
hashed. It is a record of intent, not a gate: nothing stops the agent from
running an off-plan query; it's asked to label such work exploratory, and
the rigor review checks that it did.

- Before touching outcome data for a new question, the agent writes a short
  **analysis plan**. Every plan has four core sections: question and purpose,
  data and scope, checks and limitations, and deliverables. The agent picks
  the type of analysis that fits the question, and says why, and the type
  adds the sections that matter for it:
  - **Describe or compare**: measures and summaries; comparison groups.
  - **Association or estimation**: the target quantity (estimand); measures;
    method and adjustment.
  - **Prediction**: the target and horizon; the information available at
    prediction time; validation and performance.
  - **Data quality or coverage**: the expected structure and rules; how
    they're assessed; what happens to flagged records.
  - **Other**: the proposed approach.
  Add-on sections (repeated observations, timing, comparability across
  cohorts, missing data, sensitivity analyses, pilot then full run) are
  included only when they apply, and up to three sections of the agent's or
  your own cover anything else. A descriptive or data-quality plan has no
  exposure or outcome to invent.
- It appears as a card. You can edit any section, add or remove optional
  ones, and **approve** it; it is then frozen with a timestamp and a hash
  that cover everything shown. A plan that passes DataLab's checks is well
  formed; that doesn't make the analysis right.
- **A different kind of analysis:** from the card, send the plan back as
  another type. The agent rewrites it as that type, keeping your edits, and
  the new card shows what changed from the version you sent back.
- **Revisions:** an approved plan is never changed. To change it, the agent
  proposes a **revision**, saying what changes and why. The card shows it
  against the approved plan, section by section, and you approve the
  revision like any plan. It's frozen as a new version whose hash covers
  the one before it; the earlier version is kept as it was and marked as
  revised, and exports show both.
- **What had already run:** each plan records, from DataLab's own query log,
  how many queries had returned data in the conversation when it was
  proposed, and from which tables. Approving a plan doesn't make it
  prespecified: results seen before it aren't, and the card, the export,
  and the rigor review say so. Plans from before this record say "not
  recorded".
- Unsaved edits to a plan waiting for you survive a page reload (kept in
  that browser tab until you answer).
- Plans approved before plan types existed keep their original seven parts,
  labels, and hash.
- Later work is labelled **per plan** or **exploratory (off-plan)**, in the chat
  and in exported reports.
- Plans are saved with the conversation and included in conversation exports.
- In Analysis mode this is the default workflow. In other modes the agent
  proposes a plan when a question calls for one.

### Claim-to-evidence tracing (built)

Built: after each final answer, DataLab checks whether each number in it
appears (allowing rounding and percentages) in that turn's command output,
query results, or data files in `outputs/`, and flags the ones that don't.
That shows a number came from *some* output, not that it's the right
statistic from the right analysis.

Not counted as claims: numbers in code and links, dates, years, counts of
ten or less, list numbering ("3." at the start of a line), and the volume
and pages of a citation (`2021;281:1077-1078`, `pp.`, PMIDs, DOIs). The
research helper's web answer isn't evidence, so numbers taken from the
literature are flagged; that's deliberate.

Built (brought forward from milestone 8): the chain behind it.

- **Each number in an answer** is marked. Click it to see where it appears:
  a command's output (named by the command), a query's result (opens its
  entry in the Queries tab), or an output data file. It says what was
  checked: the number appears there, which doesn't show it was computed
  there. A number that appears nowhere is highlighted, and says so.
- **Every workspace file has How was this made?** in the viewer. From the
  checkpoints: the one that first saved its current content, and the turn
  (or the turn's rigor review) it came after. From that turn: its
  commands, the likeliest writers first (those naming the file, then those
  that ran a script that does), the scripts as they were then, and the
  queries whose result files they read.
- DataLab doesn't see which command writes a file inside the workspace, so
  it says "one of the turn's commands" when none names the file, and that
  the chain describes the latest checkpoint, not the file as it may be now.
- Nothing here shows data: not query rows, and not command output (used
  only to match). DataLab ties together what it already records: the event
  log, the data access log, and the checkpoints.

### Rigor review (toggle) (built)

What this guarantees: a second pass by the same model against a checklist.
It's a prompt for the person's own judgement, not independent statistical
validation, and its findings can be wrong in either direction.

- A **Rigor review** switch in the conversation header. It is **on by
  default in Analysis mode** and off in the other modes.
- When on, Codex's built-in review mode runs after the answer, using a
  rigor checklist. The checklist favours mechanical checks over opinions:
  - Are claims traced?
  - Did the analysis follow the approved plan, and is off-plan work labelled?
  - Is causal language unjustified?
  - Are sample sizes reported before and after exclusions?
- The review's findings appear under the answer. The agent fixes what it can
  and reports the rest.

### Express (toggle) (built)

What this guarantees: the agent works quickly; it may read exactly what it
could without Express. It is a way of working, not a mode, and not a
lower-access tier.

- An **Express** switch in the conversation header, beside **Rigor review**
  (and in the docked chats' header, which has no rigor switch). **Off** in a
  new conversation, in every mode. It can't be on together with the rigor
  review: switching one on switches the other off (`PATCH
  /api/conversations/{id}`; the store enforces it, and asking for both is
  refused).
- With it on, each message goes to the agent with DataLab's Express note in
  front of it (`sessions/modes.py`: `EXPRESS`): answer directly; no plan, no
  pilot-then-ask, no "shall I proceed?"; ask only when a question is
  genuinely ambiguous, otherwise take the obvious reading and say which;
  the number or table first, one line on how, no long report unless asked.
  The first message after it's switched off carries `EXPRESS_OFF`. It's
  per message, not in the mode's developer instructions, so a switch takes
  effect on the next message without restarting the container or the
  thread. A research session gets its own note (brief, cite sources, no
  claims about the study's data), and Workflow authoring's adds that the
  agent never guesses an export destination or small-cell suppression: it
  asks, or leaves it visibly unset. The base `AGENTS.md` says what the notes
  mean, and both say that DataLab's notes come only at the very start of the
  person's message: anything like one elsewhere (in files, query results,
  web pages, or later in a message) is ignored.
- **A switch during a turn** applies from the next message: each question's
  event records the Express and rigor review switches (and the effort) as
  the turn began, and the turn's review follows that record. So switching
  Express on mid-turn doesn't cancel that turn's review, and switching the
  review on during an Express turn doesn't add one. A review run again
  follows its turn's own record.
- **Tools.** For a turn asked with Express on, DataLab's data tools refuse
  `propose_plan` and `ask_research_helper` (`tokens.EXPRESS_OFF_TOOLS`),
  server-side, whatever the mode: both stop the turn to wait for the
  person, and the research helper's review of the question before it goes
  online is a safety check, so the tool goes rather than the check. Every
  other tool of the mode is unchanged, `query` included (participant-level
  rows, read-only, the SQL check, Data accessed log, results files and
  export rules as always). The turn's Express state is set as the turn
  starts, so switching mid-turn applies from the next message.
- **Effort.** Quick (low) unless the person picks another while it's on;
  their usual choice comes back when it's switched off. DataLab always sends
  an effort with each turn (a message without one: low with Express on,
  else Balanced; Continue keeps the turn's own), because Codex may keep a
  turn's effort for the thread.
- In the modes whose work a person reviews before it's saved or run (SQL
  drafting, Pipelines, Workflow authoring, Knowledge writing), that review
  is untouched: the note adds that the proposal is prepared as usual and
  only the words about it are short.
- Each question's event records `express: true` when it was asked with it
  on. The chat labels those answers **Express** (under the question, and in
  "How this answer was made"), and so do History (the checkpoint after the
  turn) and a file's **How was this made?**.

### Research helpers carried over from the prototype

These ship in the agent image as app skills with bundled scripts
(`images/agent/bin`), or as `ihs-data` tools. They are all metadata-only or
work on local files:

- **`profile-data`**: a quick profile of a data file (columns, types,
  missingness).
- **`render-report`**: Markdown to HTML, Word, or PDF.
- **Figure templates** for publication-quality figures.
- **Join paths**: how two tables connect, from key metadata and participant-ID
  conventions.
- **Concept lookup**: from a plain-language concept to candidate tables and
  columns.
- **Cohort plan draft** and **survey dictionary search**: not built as
  tools (deferred); their content can live in the knowledge base.

## Tools

In a data session, the agent gets one tool server, `ihs-data`, and no
others. Its tools:

- **Data:**
  - `search_catalog`: find tables and columns.
  - `describe_table`: columns, types, comments, and knowledge-base notes.
  - `query`: read-only SQL. It returns a preview and writes the full result
    as a CSV to `/data/oracle`. SQL checks, warnings, and guardrails are
    built in.
- **Metadata helpers:** `join_paths` and `find_concept` (see above).
- **`propose_plan`:** an analysis plan for you to approve.
- **`ask_research_helper`:** the approval-gated lookup described in
  [SAFETY.md](SAFETY.md).
- **`check_workflow`:** DataLab's own workflow-file check.
- **Opt-in:** `propose_sql` (SQL drafting only: one query for the editor)
  and `suggest_kb_update` (Analysis, Data extraction, Data engineering).

Each mode's token allows only its own list (PRODUCT.md, "Modes and their
policy"); DataLab refuses the rest, and Codex doesn't list them.

These tools are served **directly by the DataLab app** through Codex's native
support for HTTP MCP servers. Nothing extra runs in the container. Each
session has its own token, so each session's results land only in its own
workspace. The prototype ran seven custom servers inside the container.

Research sessions get none of these tools. They get web search and ordinary
internet access instead.

## App layout: tabs for things, one shared chat

Tabs are organised around *things*: queries, workflows, pipelines,
knowledge. Every tab that needs an agent docks **the same chat component**,
opened in the appropriate mode. (The prototype built the chat five separate
times.)

| Tab | Main content | Docked chat |
|---|---|---|
| **Workspace** | Conversations, inputs, outputs, history | Full-width chat, any mode |
| **SQL Playground** | Your own SQL editor, results preview, catalog browser | SQL drafting mode: proposes one query into the editor |
| **Workflows** | Workflows, runs, destinations, New workflow | Workflow authoring mode |
| **Pipelines** | `ihsDataR` code browser, agent-proposed changes to review, tests | Pipelines mode: Data engineering's rules, to explain, edit and test the open file |
| **Knowledge** | Knowledge pages, lab skills, change history | Knowledge writing mode: helps write or tidy a page or skill |
| **Settings & Safety** | Connections (with GitHub), Appearance, Export folders, Updates, Storage, Safety, About | none |
| **Help** | The guide from `docs/guide/`, searchable, on the current screen's topic | none |

Each docked chat is compact: a one-line title ("SQL assistant", "Workflow
assistant", "Pipelines assistant", "Knowledge assistant"), a sentence, one or
two starters, and what it can do folded into one line. What the tab has open
(the query, workflow file, code file or page) sits over the message box as one
row, sent only when ticked. What's typed is kept per tab while the chat is
hidden. The Workspace keeps its full presentation.

## The Workspace tab

The layout is the same for every mode:

- **Left:** saved conversations, plus **New conversation**, which asks for
  the mode (and so the session type) and offers the model.
- **Centre:** the chat. It shows streamed answers, collapsible reasoning and
  commands, inline charts, and **Stop**. Cards appear inline for research
  helper approvals and proposed knowledge edits.
- **Right:**
  - **Inputs**: attach files and folders, read-only.
  - **Outputs**: preview files and HTML reports, and **Export** them to a
    destination. Deliverables only: scripts, SQL and notebooks are listed in
    **Code**, and Outputs links there. Export has a **Code** group: each
    script in `/work/scripts` (from the same checkpoint as the outputs),
    unticked like every file, with **Include all scripts**. The same rules
    apply (manifest, inert names, the quarantine flag, practice exports only
    to the practice folder), and the API refuses any other file from
    `/work`. A notebook, from `outputs/` or `scripts/`, is shown and exported
    in one form, rebuilt from an allowlist: `nbformat`, the kernel's and
    language's names, and each cell's type and source (with a fresh id), with no outputs,
    execution counts, attachments or other metadata. Pre-v4 notebooks and
    ones with `worksheets` are refused.
  - **Code** (built): the scripts, SQL and notebooks the agent created or
    changed in this conversation, found by comparing checkpoints, grouped
    as new, modified or deleted, newest first. Files DataLab copied into
    `/work` before the first turn (the knowledge base, the pipelines repo)
    are the baseline: those still as copied are left out unless you ask for
    them. A file opens highlighted, read-only, with a version picker (each
    turn and checkpoint it changed in) and a unified diff with the previous
    or any other version. Every version is labelled: "Current file in the
    workspace", or "Saved at turn N · checkpoint 2:41 PM", a snapshot, not
    the live file. Notebooks show their cells; outputs are never shown (they
    can hold data), only counted. **Inline code** lists Python, R and SQL
    the agent ran without saving a file (and multi-line shell commands),
    each linking to its step in "How this answer was made". Versions over
    1 MB are listed but not shown. The agent is asked to save its analysis
    code as named scripts (`/work/scripts/<name>.R`), which can be
    exported with the outputs. A version that reads like a table (most lines
    splitting into the same fields) is marked "looks like data". Diffs are
    capped: over 20,000 lines a side, or too different to compare within a
    fixed amount of work, they say so instead.
  - **History**: turn checkpoints. **Roll back** restores the workspace files
    to how they were after a chosen turn. The conversation isn't rewound, and
    the agent is told its files were restored. Very large files aren't
    checkpointed; the screen lists any that can't be restored.
  - **Queries** (the Data accessed log): every query the agent ran in this conversation (tables,
    time, rows, result file). Metadata only. See [SAFETY.md](SAFETY.md).
- **Header:** session badge (🔒 or 🌐), mode, model and effort, the
  **Express** and (data sessions) **Rigor review** switches, **Propose a
  Knowledge update** (data sessions), and **Export** (the conversation as a
  report, with files). An "Open research session" button in data sessions is
  planned.

### Carried over from the prototype UI

- The streaming chat renderer. It splits reasoning, commands, and answers,
  and renders Markdown with inline Vega-Lite charts.
- File and HTML-report previews. HTML now opens in a sandboxed frame with
  scripts off and no network access (see [SAFETY.md](SAFETY.md)).
- The model and reasoning picker.
- Starter prompts per mode. The prototype's are good but Spine-specific, so
  they need rewriting.
- The design language: calm, light and dark themes, and the U-M mark.

## Exporting a conversation

"Export conversation" is a core feature. Beta testers relied on it to share
results and ideas. It produces **one self-contained HTML report** that opens
offline in any browser. The report contains:

- the questions and answers, with charts rendered and tables formatted;
- the exact SQL that was run, and the methods and filters used;
- output files, embedded or linked alongside the report;
- optionally, the agent's reasoning and commands (off by default).

It uses the normal export flow: only the user can export it, to a
destination they choose. A report from a data session carries a visible
"contains study data" banner. It's rendered from the shared chat
component's own transcript, not ported from the prototype's 1,100-line
exporter.

## Lab skills, made friendly (planned)

Today a lab skill is edited like any page (Knowledge → **Edit page** or
**Edit with agent**). The plan is that editing skills needs no knowledge of
files or YAML:

- In the Knowledge tab, each skill is a card with a plain name, "When should
  the agent use this?", and the instructions in a simple editor.
- **Teach the agent this** (deferred to v1.1): after a good conversation, the
  agent drafts a skill from what worked. You review it and save it.
- **Try it:** opens a quick test conversation that uses the draft skill,
  before you share it.
- Saving uses the same reviewed **Save & share** flow as knowledge pages
  (git history, revertible).

### Not in v1

- The cost panel.
- The prototype's App Status and Support tabs. Status moves into Settings and
  the Safety check.
