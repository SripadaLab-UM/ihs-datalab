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
| Two session types: 🔒 Data (default) and 🌐 Research | Data sessions have the database but no internet; research sessions have the internet but no database. Chosen at start and fixed. Files move research → data only |
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

## To-dos before or during the build

- [ ] **Confirm the cohort schemas.** Behind the VPN, check which `IHS_*`
      schemas the read-only account can read today. The July 2026 note lists
      IHS_2017 and IHS_2021 through IHS_2026. Then re-export catalog metadata
      for all of them. This is the first content for `generated/schema/`.
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

