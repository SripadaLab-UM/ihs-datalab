# Scientific workspace

Status: **draft** for v1. This is a proposal under discussion and has not been
implemented.

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

Workflow authoring (Yu's routines) is designed with workflows, in step 3. It
may be a mode, or it may be a button on the Workflows screen.

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
| (new) `kb-use`, `kb-propose`, `kb-maintain` | Lab skills | See [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md). |
| (new) `sql-extraction` | App skill | The good parts of the prototype's SQL Playground mode: catalog first, check columns, profiling separate from delivery, show the SQL. |
| (new) `research-helper` | App skill | When to ask, how to phrase a question that contains no data, and to ask sparingly. |

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
| `/work/kb` | Fresh copy of the knowledge base; edits become proposals | Yes |
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

### Analysis plans

- Before touching outcome data for a new question, the agent writes a short
  **analysis plan**:
  - the question and estimand;
  - exposure, outcome, and covariates;
  - cohorts and exclusions;
  - the decision points it expects, such as how to handle missing days.
- It appears as a card. You can edit and **approve** it, and it is then
  frozen with a timestamp.
- Later work is labelled **per plan** or **exploratory (off-plan)**, in the chat
  and in exported reports.
- Plans are saved with the conversation and included in conversation exports.
- In Analysis mode this is the default workflow. In other modes the agent
  proposes a plan when a question calls for one.

### Claim-to-evidence tracing

- Every number, table, and figure in an answer links to what produced it:
  the query in the Data accessed log, the script, and the command output.
- Every file in Outputs has a **How was this made?** view showing the same
  chain.
- DataLab already records every piece of this: the event log, the data access
  log, and the checkpoints. The feature ties them together.
- After each final answer, DataLab flags numbers that don't trace to any
  query or output.

### Rigor review (toggle)

- A **Rigor review** switch in the conversation header. It is **on by
  default in Analysis mode** and off in the other modes.
- When on, Codex's built-in review mode runs before the final answer, using a
  rigor checklist. The checklist favours mechanical checks over opinions:
  - Are claims traced?
  - Did the analysis follow the approved plan, and is off-plan work labelled?
  - Is causal language unjustified?
  - Are sample sizes reported before and after exclusions?
- The review's findings appear under the answer. The agent fixes what it can
  and reports the rest.

### Research helpers carried over from the prototype

These ship in the agent image as app skills with bundled scripts, or as
extra `ihs-data` tools. They are all metadata-only or work on local files:

- **`profile-data`**: a quick profile of a data file (columns, types,
  missingness).
- **`render-report`**: Markdown to HTML, Word, or PDF.
- **Figure templates** for publication-quality figures.
- **Join paths**: how two tables connect, from key metadata and participant-ID
  conventions.
- **Concept lookup**: from a plain-language concept to candidate tables and
  columns.
- **Cohort plan draft**: criteria, tables, and open ambiguities for a cohort
  request, before any query runs.
- **Survey dictionary search**: the wording of survey questions and their
  answer codes, from the public data dictionaries. The dictionaries are also
  added to the knowledge base's `generated/` folder.

## Tools

In a data session, the agent gets one tool server, `ihs-data`, and no
others. Its tools:

- **Data:**
  - `search_catalog`: find tables and columns.
  - `describe_table`: columns, types, comments, and knowledge-base notes.
  - `query`: read-only SQL. It returns a preview and writes the full result
    as a CSV to `/data/oracle`. SQL checks, warnings, and guardrails are
    built in.
- **Metadata helpers:** join paths, concept lookup, cohort plan draft, and
  survey dictionary search (see above).
- **`propose_plan`:** an analysis plan for you to approve.
- **`ask_research_helper`:** the approval-gated lookup described in
  [SAFETY.md](SAFETY.md).

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
| **SQL Playground** | Your own SQL editor, results preview, catalog browser | Data extraction mode, seeded with the current query |
| **Workflows** | Routines, runs, batches, destinations | Workflow authoring (designed in step 3) |
| **Pipelines** | `ihsDataR` code browser, agent-proposed changes to review, tests | Data engineering mode |
| **Knowledge** | Knowledge pages, lab skills, change history | Helps write or tidy a page or skill |
| **Settings & Safety** | Connections, GitHub sign-in, export destinations, Safety check | none |

## The Workspace tab

The layout is the same for every mode:

- **Left:** conversations (saved and searchable), plus a **New** button that
  asks for mode and session type.
- **Centre:** the chat. It shows streamed answers, collapsible reasoning and
  commands, inline charts, and **Stop**. Cards appear inline for research
  helper approvals and proposed knowledge edits.
- **Right:**
  - **Inputs**: attach files and folders, read-only.
  - **Outputs**: preview files and HTML reports, and **Export** them to a
    destination.
  - **History**: turn checkpoints. **Roll back** restores the workspace files
    to how they were after a chosen turn. The conversation isn't rewound, and
    the agent is told its files were restored. Very large files aren't
    checkpointed; the screen lists any that can't be restored.
  - **Data accessed**: every query the agent ran in this conversation (tables,
    time, rows, result file). Metadata only. See [SAFETY.md](SAFETY.md).
- **Header:** session badge (🔒 or 🌐), mode, model and reasoning level,
  **Export conversation**, and an "Open research session" button in data
  sessions.

### Carried over from the prototype UI

- The streaming chat renderer. It splits reasoning, commands, and answers,
  and renders Markdown with inline Vega-Lite charts.
- File and HTML-report previews. HTML now opens in a sandboxed frame with no
  network access (see [SAFETY.md](SAFETY.md)).
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
"contains study data" banner. It will be rebuilt on the shared chat
component, not ported from the prototype's 1,100-line exporter.

## Lab skills, made friendly

Editing skills needs no knowledge of files or YAML:

- In the Knowledge tab, each skill is a card with a plain name, "When should
  the agent use this?", and the instructions in a simple editor.
- **Teach the agent this:** after a good conversation, the agent drafts a
  skill from what worked. You review it and save it.
- **Try it:** opens a quick test conversation that uses the draft skill,
  before you share it.
- Saving uses the same reviewed **Save & share** flow as knowledge pages
  (git history, revertible).

### Not in v1

- The cost panel.
- The prototype's App Status and Support tabs. Status moves into Settings and
  the Safety check.
