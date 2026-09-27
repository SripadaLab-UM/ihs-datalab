# DataLab v1 — product definition

Status: **draft**, being worked out section by section.

DataLab is a local app that lets Sen Lab / Intern Health Study researchers
use an AI agent on sensitive IHS data, safely. It replaces the
`um-gpt-local-proxy` prototype.

## Layers

1. **Safety platform** (the base promise). See [docs/SAFETY.md](docs/SAFETY.md).
2. **Scientific workspace.** Codex with research-specific modes, skills, and a
   shared lab knowledge base. See [docs/WORKSPACE.md](docs/WORKSPACE.md) and
   [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md).
3. **Custom workflows.** Repeatable, human-approved recipes (SQL → R → QC →
   export) that run with no LLM involved. Yu's preprocessing and routines are
   the first examples. See [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

## Decisions so far

| Decision | Notes |
|---|---|
| Codex only, OpenAI models on U-M GPT | No Claude, no Anthropic translation |
| No custom LLM proxy | A stock gateway container enforces the network allowlist. A small model relay in the host app adds the key (see below), so no container holds it |
| Docker Desktop is required | Acceptable to all users |
| Safety promises are internal | Written for colleagues and future maintainers: short and plain |
| "No data loss" covers both researchers' work and source data | Local files untouchable unless attached; the Oracle database is read-only and protected from load |
| Oracle access goes through DataLab's own data service | The agent never holds database credentials |
| Two session types: 🔒 Data (default) and 🌐 Research | Data sessions have the database (read-only) and reach only U-M GPT and DataLab; research sessions have the web but no database connection. Chosen at start and fixed. Files move research → data only |
| Data sessions can use a research helper | The agent asks a question and the user approves or edits it. A temporary no-data research container answers it. No auto-approval in v1 |
| Knowledge base is collective and tracked in GitHub | Every user can edit, and git provides history, attribution, and reverts. DataLab handles pull, commit, and push behind a "Save & share" button, since most users don't use git directly. Agents propose edits; a person reviews the diff and shares it |
| Knowledge base lives in its own GitHub repo | Separate from app code and releases. DataLab signs users in through GitHub's browser (device) flow. The token lives in the OS keychain and is used only by the host app, never in containers. Access comes from an org team (`datalab-users`) with write access; you add people. DataLab detects missing access and says whom to ask |
| Knowledge base: Markdown pages plus native Codex `AGENTS.md`/skills | No custom retrieval server. Agents edit a workspace copy; the person reviews the diff and DataLab commits it directly to `main`. The repo is `ihs-knowledge` in `SripadaLab-UM`. See [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) |
| Drive Codex via `codex app-server` with a pinned, current CLI | Provides native interrupt, resume, and streaming, plus approval requests the UI can answer, which the research helper needs. The prototype's 0.139.0 is 18 versions behind the current 0.157.1 |
| Four modes: Analysis, Data extraction, Data engineering, Research | A mode is a starting point that sets instructions and suggested prompts; it is not a restriction |
| Tabs for things, one shared chat | Workspace, SQL Playground, Workflows, Pipelines, Knowledge, Settings & Safety. Each tab docks the same chat component in the right mode |
| Conversation export is a core v1 feature | A self-contained offline HTML report. It goes through the user-only export flow and carries a "contains study data" banner. The cost panel is dropped |
| Lab skills are editable by anyone | They live in the knowledge base repo and are edited through friendly cards, "Teach the agent this", and "Try it", with the same Save & share flow |
| All cohort years, not just 2025 | Agents can query every IHS cohort schema the read-only account can see (IHS_2017, IHS_2021–IHS_2026). The knowledge base is year-aware, with a generated cross-year drift report. Legacy code, such as Yu's 2024 scripts, is kept as reference for agents. `ihsDataR` remains the 2025 build |
| Workflows and pipelines live in one repo, `ihs-pipelines` | Workflows are YAML files and pipelines are the `ihsDataR` package. Changes are saved directly after review and tests, with no PRs. Runs are on-demand; there is no compare step in v1 |
| Mac and Windows are both in v1 | Install is one pasted command, then a launcher. Pinned images are pulled, not built. Updates happen in the app and can be rolled back. See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) |
| The app repo and images are public; the lab repos are private | No environment-specific details in public code. GitHub sign-in goes through an org GitHub App scoped to the two private repos |
| One complete v1 release for colleagues | We build in internal milestones and test them ourselves. Colleagues keep using the prototype until v1 is ready |
| Outside AI tools can connect (MCP server + CLI) | v1: **practice instance only** (synthetic data), with full access. Real-instance access (metadata only) is deferred. The app enforces this per token. Used to build, test, and automate DataLab |
| Resource guardrails are in v1 | End-to-end query deadline with real cancellation, extraction size caps (bigger needs an explicit OK), a disk-space check, caps on concurrent queries and containers, and stopping idle containers. Performance extras wait for v1.1 |
| Model traffic goes through a validating relay in the host app | The real U-M key never leaves DataLab's process. Containers use per-session tokens. The relay refuses hosted tools and provider-side URL fetches in data sessions. The gateway is a secret-free router. This came from the spike and the external review |
| Science features in v1: analysis plans, claim tracing, rigor review toggle, and prototype research helpers | Evidence shows agents' main risk on observational data is analytic flexibility and overclaiming. v1 invests in planned, traceable, reviewable work rather than autonomy. Cohort-confirmation runs, the robustness battery, and IHS rigor skills are deferred |
| Export is a user action only | The one exception is a workflow the user approved, which writes to its configured destination |
| PHI is allowed on local disks | Users' machines are PHI-approved. Cleanup of internal data exists for tidiness, not as a safety control |
| Exports go to user-chosen local folders | Dated subfolders with a manifest, so no mess is left behind |
| Dropbox is a local synced folder, not an API | This makes it just another export destination. To double-check later: Dropbox client setup, online-only files, and path differences on Mac and Windows |

## To work through

1. ~~Safety charter~~ (drafted in docs/SAFETY.md)
2. ~~Workspace and knowledge base~~ (drafted in docs/WORKSPACE.md and docs/KNOWLEDGE_BASE.md)
3. Workflows: Yu's routines and preprocessing, and the general pattern (drafted in docs/WORKFLOWS.md; scheduling is deferred)
4. ~~Distribution: install and update on Mac and Windows, and where data lives on disk~~ (drafted in docs/DISTRIBUTION.md)
5. ~~v1 scope cut and definition of done~~ (drafted below; awaiting review)

## v1 scope (draft)

### In v1

**Safety platform**
- One sealed container per conversation, with two session types: 🔒 Data and
  🌐 Research.
- The gateway, which enforces the network allowlist, and the model relay, which
  validates requests and adds the U-M GPT key. No container holds a key.
- Read-only attachments, turn checkpoints, and rollback.
- The research helper, with an approval card for every question.
- The Safety check screen.
- The Data accessed panel for each conversation, backed by a local
  metadata-only audit log.
- Resource guardrails:
  - query deadlines with real cancellation;
  - extraction caps;
  - a disk-space check;
  - caps on concurrent queries and containers, with idle containers stopped.

**Workspace**
- **Workspace tab**:
  - the shared chat and its four modes;
  - inputs, outputs with preview, and export;
  - history and rollback;
  - model and reasoning picker, Stop, and saved conversations;
  - "Open research session".
- **Export conversation** as a self-contained HTML report.
- **Scientific rigor** (see [docs/WORKSPACE.md](docs/WORKSPACE.md)):
  - analysis plans, approved and frozen before outcome analysis;
  - claim-to-evidence tracing, including "How was this made?";
  - a Rigor review toggle, on by default in Analysis mode.
- **Research helpers from the prototype:**
  - file profiling, report rendering, and figure templates;
  - join paths, concept lookup, and cohort plan drafts;
  - survey dictionary search.
- **SQL Playground**: SQL editor, results preview, catalog browser for all
  cohorts, and docked chat.
- **Knowledge tab**:
  - browse and edit knowledge pages and lab skills with friendly cards;
  - "Try it" for skills;
  - proposed-edit cards;
  - Save & share, history, and revert.
- **Initial knowledge content**:
  - the Spine's 157 entries converted to pages;
  - schema metadata for every cohort, plus the drift report;
  - lab paper summaries;
  - the legacy 2024 code as reference.

**Workflows**
- **Workflows tab**:
  - run a workflow with parameters, or run a set;
  - run records and delivery to destinations;
  - form and YAML editor;
  - "Save as workflow" from SQL Playground;
  - "Turn this into a workflow" from a conversation.
- **Pipelines tab**: browse `ihsDataR`, docked Data engineering chat, review
  diffs, run tests, and save.
- **Starting content**: the 8 default routines as workflows, and the
  `daily_metrics_2025` pipeline.

**Platform**
- Installers for Mac and Windows, a launcher, in-app updates with rollback,
  and an uninstaller.
- Connections (keychain), GitHub App sign-in, and export destinations,
  including auto-detected Dropbox.
- The Storage view and "Copy diagnostics".
- CI, pinned dependencies and images, and automated releases.

**Synthetic IHS dataset** (built together with the maintainer)
- A fake database with the same shape as IHS: a few cohorts, the device
  tables, realistic quirks, and made-up participants.
- DataLab's data service can use it in place of Oracle.
- It enables end-to-end development and CI without the VPN.
- The adversarial safety test runs automatically on it.
- It holds a small **evaluation set** of representative tasks, re-run
  whenever prompts, skills, or the Codex version change.
- It powers a **practice mode** for new colleagues and PHI-free demos.

**Outside AI connectors**
- An MCP server and a `datalab` CLI, over the same API as the UI.
- Scoped tokens, enforced on every route. v1 enables connectors on the
  practice profile only.
- A practice profile with its own data folder and the synthetic backend.

**Docs**
- README (install and run), USER_GUIDE, SAFETY, and ARCHITECTURE.

### Later (v1.1 and beyond)

- "Teach the agent this", which drafts a skill from a conversation.
- Scheduled workflow runs.
- A compare step for checking parity against legacy outputs.
- Auto-approved research-helper lookups from fixed templates, if approvals
  prove tedious.
- Pipelines that take a cohort parameter, instead of 2025 being built in.
- Local search for the knowledge base, if it outgrows `index.md` and grep.
- A usage or cost display, and personal notes or memory.
- Large-data performance extras: Parquet results, DuckDB in the agent
  image, and size warnings before very large extracts.
- Outside-AI connector access to a real-data instance (metadata scope).
- Signed native installers or a desktop-app wrapper.
- Considered for science and not in v1:
  - explore-on-some-cohorts / confirm-on-another runs;
  - an automated robustness battery (specification curves, stability checks,
    negative controls);
  - IHS-specific rigor skills;
  - independent re-implementations of a result;
  - notebook-style provenance (marimo);
  - formal multiple-testing control.
- New research pipelines such as SensorKit preprocessing. These are content
  built *with* v1, not app features.

### Definition of done

v1 ships when all of these are true on a fresh **Mac** and a fresh
**Windows** machine:

1. **Install.** A non-technical colleague installs DataLab by following the
   README alone, in under 30 minutes.
2. **Safety.** Every Safety check passes. A scripted adversarial test also
   fails as it should: in it, the agent is told to reach the internet from a
   data session, read the home folder, write to Oracle, and find a key, and
   none of these succeed. The test runs automatically against the synthetic
   dataset.
3. **Yu's work.** Yu runs the default exports to her Dropbox folder and runs
   `daily_metrics_2025`. She has the agent change a pipeline, reviews the
   change, and saves it.
4. **A PI's work.** A PI asks an analysis question that spans two cohorts,
   gets charts and a clear answer, and exports the conversation as a report.
5. **Shared knowledge.** Two people edit the knowledge base on the same day.
   Both changes are saved, attributed, and revertible, and the check is green.
6. **Updates.** Updating from 1.0.0 to 1.0.1 works, and so does rolling back.
7. **Quality.** CI is green: lint, type checks, Python, frontend, and R tests.
8. **Parity with the prototype.** The 8 default workflows and
   `daily_metrics_2025` give the same outputs in v1 as in the prototype, on
   the same inputs.

## Planned: UI redesign (after milestone 3)

The current web UI is a functional skeleton. After milestone 3, when the
Workspace has all its real parts, the redesign runs in four steps:

1. Direction from the user: what feels wrong, and reference apps.
2. Two or three clickable mockup directions, then refine one.
3. A small design system: type, spacing, colour tokens, components, light and
   dark themes, and accessibility.
4. Rebuild the Workspace on it. Later tabs (SQL Playground, Knowledge,
   Workflows, Pipelines) are then built on the new design.

Until then, UI logic (event stream, transcript, API calls) is kept separate
from presentation, so the redesign replaces the look without touching the
behaviour.

**Status (2026-09-26):** steps 1 to 4 done for the Workspace with the
"Narrator" structure (docs/DESIGN.md). The lab then chose a quieter visual
language, "paper", after understand.cap-study.com: serif for reading, hairline
rules instead of cards, small uppercase labels, and colour only where it means
something (data session, research session, attention, errors). Data, SQL and
paths stay in a monospace font. The Narrator structure stays; only the
treatment changes. The paper treatment was in place for the Workspace by
2026-09-27, and the later tabs are built on it.

## Planned: help and onboarding (after the paper reskin)

New users should understand, within a few minutes and without a person
showing them, what DataLab is, what it will and won't do with study data,
and how to get a good answer out of it. Screenshots and videos wait for the
final look.

1. **One source of words.** A glossary (data session, research session, plan,
   pilot, rigor review, trace, checkpoint, read-only, practice) and the
   how-to guides live as Markdown in the repo (`docs/guide/`). The tooltips,
   the in-app Help, and the website are all built from it, so they never
   disagree.
2. **Tooltips** on DataLab's own terms and safety signals: the session badge,
   the rigor switch, plan approval, the trace chip, checkpoints, Export. They
   are short, with a "Learn more" link into Help.
3. **A first-run tour** of about five steps, on the practice profile:
   - ask a question;
   - watch the steps;
   - open one to see what the agent read;
   - read the answer with its trace and review;
   - find the outputs and export them.

   It can be skipped, and replayed from Help.
4. **In-app Help.** It is searchable, opens on the topic of the current
   screen, and is bundled with the app: the browser may only contact
   DataLab, so it can't load anything from the web.
5. **Short videos** of about 1–2 minutes, one task each, recorded only on the
   practice profile (synthetic data). They are hosted with the website and
   linked from Help, since the app can't embed outside video.
6. **The website:** the same guides as a static site with the paper look,
   including screenshots made by a script from the practice profile so they
   stay current.

Decided (2026-09-26), to pick up later:
- The docs site is public. It may be hosted on the AWS setup the lab already
  uses for cap-study.
- Claude generates the videos in full: scripted walkthroughs recorded on the
  practice profile, narrated with AWS Polly. Narration scripts and footage
  show synthetic data only, since both leave the laptop.

## Open decisions (waiting on the lab, noted 2026-09-26)

- [ ] **Attached folders that contain credentials files** (`.env`,
      `auth.json`, …): today DataLab attaches them and lists what it found
      as a warning. Refuse such folders instead?
- [x] **Full claim-to-evidence provenance** (each number linked to its
      query, script, and output; the "How was this made?" view): brought
      forward from milestone 8 (decided 2026-09-27); built alongside
      milestones 4–7.
- [x] **The knowledge base in a session** (decided 2026-09-27): an editable
      copy at `/work/kb` that DataLab diffs after each turn into
      proposed-edit cards, not a read-only mount.
- [ ] **The enrolment rule on the real database.** In the synthetic data a
      NULL study ID (`SECONDARYIDENTIFIER`, `STUDY_PARTICIPANT_ID`) means
      screened, never enrolled. Is that true of the real IHS data? It goes
      into the lab knowledge base (milestone 5) once confirmed.
- [ ] **What "the 2025 cohort" means in an analysis.** The evaluation
      graders read it as enrolled participants only (screened-but-not-
      enrolled people excluded). Confirm, or the mood task needs regrading.
- [ ] **Accepting milestone 3.** Demonstrated end to end on 2026-09-26
      (docs/acceptance/2026-09-26-milestone-3.md); acceptance is the lab's.

## To-dos before or during the build

- [x] **Confirm the cohort schemas** (2026-09-26).
      - `SVC_IHS_AGENT` can read IHS_2017 and IHS_2021 through IHS_2026.
        IHS_2018 is visible, but has no readable tables.
      - Catalog metadata for all seven was exported to
        `srijan-knowledge-graph/metadata/db_exports/ihs_oracle_metadata_20260926T060331Z`.
        That location is outside this repo, and the export is metadata only.
- [ ] **Ask the DBA to remove write access from `SVC_IHS_AGENT`.**
      - The account holds `IHS_2026_ROLE`, which grants UPDATE, DELETE, and
        ALTER on all 161 IHS_2026 tables, plus CREATE TABLE, PROCEDURE, and
        DATABASE LINK.
      - The request: keep `IHS_2025_RO`, `IHS_2026_RO`, and the direct SELECT
        grants on the older cohorts, and remove `IHS_2026_ROLE`. The two
        IHS_2026 tables reachable only through that role would then need
        adding to `IHS_2026_RO`.
      - Until then, DataLab enables only the read-only roles on each
        connection, and the Safety check verifies it.
      - This also affects the prototype, which is still in use and doesn't
        restrict roles.
- [x] **Before making this repo public,** decide whether the synthetic IHS
      schema stays here. It is derived from real table and column names,
      which are internal metadata. Decided 2026-09-27: it stays, and the repo
      is public. The history was scanned first: no keys, tokens, passwords, or
      database hosts.
- [ ] **Per-person Oracle accounts (future).** Check with team members
      whether they have, or can get, individual read-only Oracle accounts. If
      so, DataLab should use each person's own account, so access follows
      their Oracle grants and database logs show who ran what. The shared
      service account would remain a fallback. v1 continues with the shared
      account.
- [x] **Early technical spike** (done 2026-09-26, on Mac).
      - Confirmed: Codex 0.157.1 over `app-server`, the gateway, the DNS
        lockdown, approval requests (including decline and stop), resume
        after a restart, and the Squid research proxy.
      - Found, with fixes designed:
        - the hosted-tool bypass, fixed by the model relay in the host app;
        - Stop doesn't kill running commands, fixed by the adapter killing
          them itself.
      - Windows is still to test, in milestone 2.
      - The prototype's capability audit had already exercised `app-server`
        compaction; the spike added the rest.
- [ ] **Keep git history when splitting repos.** Move `ihsDataR` and the
      Spine content into their new repos with their commit history intact.

