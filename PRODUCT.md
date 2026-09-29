# DataLab v1: product contract and readiness

Status: **current contract**, updated in place. It describes what DataLab
does as of the released **0.3.0b1** (tag `v0.3.0-beta.1`) and `main`
(heading to 0.3.0b2), on 2026-09-29, and what still stands between it and
v1.0.0. Every user-facing change updates its line here and its Help page
(`docs/guide/`).

Status words: **implemented** (built and covered by automated tests; not
the same as accepted), **partly**, **planned** (in v1, not built yet),
**deferred** (after v1).

DataLab is a local app that lets Sen Lab / Intern Health Study researchers
use an AI agent (OpenAI Codex on U-M GPT) on sensitive IHS data, safely. It
replaces the `um-gpt-local-proxy` prototype.

## Layers

1. **Safety platform** (the base promise). See [docs/SAFETY.md](docs/SAFETY.md).
2. **Scientific workspace.** Codex with research-specific modes, skills, and a
   shared lab knowledge base. See [docs/WORKSPACE.md](docs/WORKSPACE.md) and
   [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md).
3. **Custom workflows.** Repeatable, human-approved recipes (SQL → R → QC →
   export) that run with no LLM involved. Yu's routines are the first
   examples. See [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

How it's built: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Installing and
updating: [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

## Decisions

| Decision | Notes | Status |
|---|---|---|
| Codex only, OpenAI models on U-M GPT | No Claude, no Anthropic translation. Default model `gpt-5.5`; GPT-5.6 Sol didn't pass acceptance ([docs/acceptance/2026-09-27-gpt-5.6-sol.md](docs/acceptance/2026-09-27-gpt-5.6-sol.md)). The relay allows only approved models | implemented |
| No custom LLM proxy | A stock gateway container (nginx) routes a session's traffic; a small model relay in the host app adds the key, so no container holds it | implemented |
| Docker Desktop is required | The Mac installer finds, installs and starts it; the Windows installer sets up WSL and Docker Desktop | implemented |
| Safety promises are internal | Written for colleagues and future maintainers: short and plain | implemented |
| "No data loss" covers researchers' work and source data | Local files are untouchable unless attached (then read-only); the Oracle database is read-only and protected from load | implemented |
| Oracle access goes through DataLab's own data service | The agent never holds database credentials | implemented |
| Two session types: 🔒 Data (default) and 🌐 Research | Chosen at start (by the mode) and fixed. Files move research → data only | implemented |
| Data sessions can use a research helper | The agent asks a question; the person approves, edits or declines it; a temporary no-data research container answers. No auto-approval in v1 | implemented |
| The knowledge base is collective and tracked in GitHub (`ihs-knowledge`) | Markdown pages plus native Codex `AGENTS.md`/skills, no retrieval server. Each conversation gets an editable copy at `/work/kb`, diffed after each turn into proposed-edit cards; a person reviews it and **Save & share** commits to `main`. People can also edit a page directly, or with the agent | implemented |
| GitHub sign-in through an org GitHub App | Device flow; the token is in the OS keychain, used only by the host app, never in containers. Access comes from the `datalab-users` team; DataLab says whom to ask | implemented |
| Drive Codex through `codex app-server`, pinned | Codex 0.157.1 (`images/agent/Dockerfile`): interrupt, resume, streaming, and approval requests the UI answers | implemented |
| Modes are starting points, not restrictions | Four in the Workspace (Analysis, Data extraction, Data engineering, Research) and four docked in tabs (SQL drafting, Pipelines, Workflow authoring, Knowledge writing). A mode sets the instructions, starters and which `ihs-data` tools the session's token allows. See the policy matrix below | implemented |
| The Express switch | Quick answers in any mode: low effort, no plans, no pilot-then-ask, no research helper; the same data access and checks. Can't be on together with the rigor review | implemented |
| Tabs for things, one shared chat | Workspace, SQL Playground, Workflows, Pipelines, Knowledge, Settings & Safety, and Help. Each tab docks the same chat component in its own mode | implemented |
| Conversation export is a core v1 feature | A self-contained offline HTML report, through the user-only export flow, with a "contains study data" banner. No cost panel | implemented |
| All cohort years, not just 2025 | Agents can query every cohort schema the read-only account can see (IHS_2017, IHS_2021–IHS_2026). The catalog covers every cohort; the knowledge base has a generated cross-year drift report. `ihsDataR` remains the 2025 build | implemented (content in the private repo) |
| Workflows and pipelines live in one repo, `ihs-pipelines` | Workflows are YAML files; pipelines are the `ihsDataR` package. Changes are saved directly after review and tests, with no PRs. Runs are on demand | implemented |
| Mac and Windows are both in v1 | One pasted command from the lab's install page, then a launcher. Pinned images are pulled, not built. Updates in the app; going back with `datalab versions --use` and `datalab rollback` | partly: Windows needs a re-test |
| The app repo and images are public; the lab repos are private | No environment-specific details in public code; the lab's settings come with the install page's installer, or from a settings file | implemented |
| One complete v1 release for colleagues | Built in internal milestones and pre-releases (`v0.2.0-beta.1` … `v0.3.0-beta.1`); v1.0.0 is the release promised to colleagues | in progress |
| Outside AI tools connect through an MCP server and CLI | Planned for the practice instance only, with per-token scopes. Not built: until then Claude and other outside tools may be used only with the practice DataLab, as a rule for people | planned (connectors); the rule is in force |
| Resource guardrails | An end-to-end query deadline with real cancellation; row and byte caps per result (set in `settings.toml` `[limits]`, never by the agent); a disk-space check; at most 2 queries and 3 working conversations at once; idle containers stopped after 30 minutes. No in-app "allow a bigger result" yet | implemented |
| Model traffic goes through a validating relay in the host app | The real key never leaves DataLab's process; containers use per-session tokens; hosted tools and provider-side URL fetches are refused in data sessions; U-M GPT's busy answers are retried by the relay | implemented |
| Science features: plans, claim tracing, provenance, rigor review | Typed analysis plans (frozen, hashed, revisable), numbers traced to the turn's outputs, "How was this made?", and a rigor review switch (on by default in Analysis). Cohort-confirmation runs, a robustness battery and IHS rigor skills are deferred | implemented |
| Export is a user action only | The one exception is a workflow a person saved after review and runs, which delivers to its configured destination | implemented |
| PHI is allowed on local disks | Users' machines are PHI-approved. Clean-up exists for tidiness, not as a safety control | implemented |
| Exports go to user-chosen local folders | Dated subfolders with a manifest. Dropbox is just a local synced folder (no API), found automatically | implemented |
| The practice DataLab is a complete install | Its own profile, data folder and launcher; it sets up and starts its synthetic Oracle database itself; the 8 routines ship as read-only built-in workflows; no GitHub, no host files, practice-only exports | implemented |
| Help is one source of words | `docs/guide/*.md` is bundled as the in-app Help and its tooltips; `docs/USER_GUIDE.md` only lists it | implemented |

## Modes and their policy

Derived from `backend/src/datalab/sessions/modes.py` and
`backend/src/datalab/sessions/tokens.py`. The session's token carries the
tool list, and DataLab's data tools refuse any other
(`backend/src/datalab/data/agent_tools.py`).

Tool groups: **catalog** is `search_catalog`, `describe_table`, `join_paths`
and `find_concept` (metadata only). **All** is catalog plus `query`,
`check_workflow`, `propose_plan` and `ask_research_helper`: every tool but
the opt-in `propose_sql` and `suggest_kb_update`.

| Mode | Session | Opened from | `ihs-data` tools | Attach files | Default effort | Rigor review | What Express changes | A person reviews before it's saved or run |
|---|---|---|---|---|---|---|---|---|
| Analysis | data | Workspace (the default) | all + `suggest_kb_update` | yes | Balanced | **on** | no plan, no pilot-then-ask, no research helper (both refused server-side); a brief answer; Quick effort; the rigor review off | `/work/kb` edits and Knowledge suggestions |
| Data extraction | data | Workspace | all + `suggest_kb_update` | yes | Balanced | off | as Analysis | as Analysis |
| Data engineering | data | Workspace | all + `suggest_kb_update` | yes | Balanced | off | as Analysis | `/work/pipelines` changes become a Pipelines proposal; `/work/kb` edits |
| SQL drafting | data | SQL Playground's chat | catalog + `query`, `propose_sql`, `ask_research_helper` | no | Balanced | off (docked chats have no switch) | brief; no research helper; the proposal is prepared as usual | the person uses, edits and runs the proposed query |
| Pipelines | data | Pipelines' chat | catalog + `query`, `check_workflow`, `ask_research_helper` | no | Balanced | off | brief; no research helper; the proposal as usual | the proposal is reviewed, tested and saved in Pipelines |
| Workflow authoring | data | Workflows' chat, New workflow | all | yes (from the Workspace) | Balanced | off | as Pipelines, and it never guesses a destination or small-cell rule | a Pipelines proposal, or New workflow's review, test and Save |
| Knowledge writing | data | Knowledge's chat, Edit with agent | catalog only | no | Balanced | off | brief; the proposal as usual | proposed-edit cards, then Save & share |
| Research | research | Workspace | none: web search and the research proxy instead | yes (may reach the internet) | Balanced | n/a | its own note: brief, cite sources, no claims about the study's data | nothing is shared from it |

Every mode gets the knowledge base copy at `/work/kb`, and every data mode
the same read-only database rules (the SQL check, the Data accessed log,
results files, export rules). Effort is Quick, Balanced or Thorough (`low`,
`medium`, `high`), and DataLab sends one with every turn.

## v1 scope, as it stands

**Safety platform**
- implemented: one sealed container per conversation, with Data and
  Research session types; the gateway and the model relay (no container
  holds a key); read-only attachments, turn checkpoints and rollback; the
  research helper, with an approval card for every question; the Safety
  check (Settings → Safety, `datalab safety-check`, CI); the Data accessed
  record (the Queries tab), backed by a metadata-only audit log; resource
  guardrails (above); query failures explained in DataLab's own words.
- planned: connectors for outside AI tools (practice only).

**Workspace**
- implemented: the shared chat and its modes; Express; inputs, outputs with
  preview, the Code tab, History and rollback, Queries; model and effort
  pickers, Stop, Continue, conversations named from the first question and
  renamed; Export conversation as one HTML report; analysis plans, claim
  tracing, provenance ("How was this made?") and the rigor review; the
  research helper; app skills (`images/agent/skills`) and the `profile-data`
  and `render-report` scripts; the `join_paths` and `find_concept` helpers;
  Suggested Knowledge updates and Propose a Knowledge update.
- planned: "Open research session" from a data session.
- deferred: cohort plan draft and survey dictionary search as tools (their
  content can live in the knowledge base instead).

**SQL Playground** (implemented): the editor with the live SQL check, bind
values, the results grid, Stop, history, the catalog browser for every
cohort, exporting a result, Save as workflow, and the SQL assistant, which
proposes one query into the editor ("Use this query", "Replace current
draft", "How this SQL was created").

**Knowledge**
- implemented: pages and lab skills as a folding tree, Recent changes,
  proposed-edit cards, Edit page (drafts on this computer, the live check,
  conflicts with GitHub), Edit with agent, Save & share, and a GitHub
  Actions check template for `ihs-knowledge`. Content: the Spine converter
  (`scripts/convert-spine`, tested in CI), the schema catalog and the drift
  report, kept in the private repo.
- planned: friendly skill cards and "Try it".
- deferred: reverting from the app (git history remains), and "Teach the
  agent this".

**Workflows and Pipelines**
- implemented: running with parameters, run records, Run again and Replay,
  delivery to destinations, workflow destinations chosen per computer; New
  workflow (describe it, the agent asks and drafts, three editable stages, a
  test run on practice, Save); Save as workflow and Turn this into a
  workflow; the Pipelines tab (browse `ihsDataR`, the Pipelines assistant,
  Edit manually and New file, diffs, tests in a no-network container, Save &
  share). The 8 routines ship as practice built-ins.
- planned: running a set of workflows at once.

**Platform** (implemented): Mac and Windows installers, launchers with
profile icons, and the uninstallers; in-app updates of signed releases
(`SHA256SUMS.sig`), checked at start and about once an hour; database
backups, `datalab rollback` and `datalab versions --use`; Connections
(keychain), GitHub App sign-in, export folders with Dropbox detection;
Storage; Copy diagnostics; Send feedback (support reports,
[docs/SUPPORT.md](docs/SUPPORT.md)); CI, pinned dependencies and images, and
automated releases.

**Synthetic IHS dataset and practice** (implemented): a synthetic Oracle
(Oracle Database Free) with the IHS shape, its quirks and made-up
participants, used for development, CI, the adversarial safety test, the
evaluation set (`evals/`) and the practice DataLab.

**Help and docs**
- implemented: `docs/guide` as in-app Help and tooltips; README, SAFETY,
  ARCHITECTURE and the area docs; `AGENTS.md` for coding agents.
- partly: the first-run tour is built but switched off.
- planned: the guides as a public website with screenshots, and short videos
  from the practice profile.

### Later (v1.1 and beyond)

- "Teach the agent this", which drafts a skill from a conversation.
- Scheduled workflow runs, and a compare step against legacy outputs.
- Auto-approved research-helper lookups from fixed templates, if approvals
  prove tedious.
- Pipelines that take a cohort parameter, instead of 2025 being built in.
- Local search for the knowledge base, if it outgrows `index.md` and grep.
- A usage or cost display, and personal notes or memory.
- Large-data extras: Parquet results, DuckDB in the agent image, size
  warnings before very large extracts, and pruning old checkpoints.
- Outside-AI connector access to a real-data instance (metadata scope).
- Signed native installers or a desktop-app wrapper.
- Science: explore-on-some-cohorts / confirm-on-another runs; a robustness
  battery; IHS-specific rigor skills; independent re-implementations;
  notebook-style provenance; formal multiple-testing control.
- New research pipelines such as SensorKit preprocessing (content built
  *with* v1, not app features).

## v1 readiness

v1 ships when the definition of done holds on a fresh **Mac** and a fresh
**Windows** machine. Where each item stands on 2026-09-29:

| # | Definition of done | Status | Evidence so far | Next step |
|---|---|---|---|---|
| 1 | **Install.** A non-technical colleague installs DataLab from the install page alone, in under 30 minutes | partly | Installers for both platforms (`installer/`); unit-tested (`test_installer_macos.py`, `test_windows_installer.py`); CI job `windows-installer` (the scripts parse under PowerShell 5.1, package selection, the pinned uv's checksum and signature); CI job `frontend` installs the release package and starts it. The packaged app was installed for testing on a Mac, and an earlier installer on one managed Windows laptop | A colleague's timed install on a fresh Mac and a fresh Windows machine; a Windows re-test of the current installer |
| 2 | **Safety.** Every Safety check passes, and a scripted adversarial test (reach the internet from a data session, read the home folder, write to Oracle, find a key) fails as it should, automatically, against the synthetic data | partly | CI job `oracle`: the adversarial Safety check against the synthetic Oracle, with the DNS leak watch (`scripts/dns-leak-test.sh`), and a check that it catches a deliberately broken sandbox; `test_safety.py`, `test_relay.py`, `test_containers.py`. CI runs on Linux | Run the Safety check on the fresh Mac and Windows installs; the DBA change below |
| 3 | **Yu's work.** Yu runs the default exports to her Dropbox folder, runs `daily_metrics_2025`, has the agent change a pipeline, reviews it, and saves it | partly | Every piece is implemented and unit-tested: export folders with Dropbox, workflows and delivery, Pipelines proposals, tests and Save & share (`test_workflow_runner.py`, `test_pipelines.py`, `test_pipeline_edits.py`) | Yu does it herself, on the real DataLab |
| 4 | **A PI's work.** A PI asks an analysis question that spans two cohorts, gets charts and a clear answer, and exports the conversation as a report | partly | The milestone-3 journey on the practice profile ([docs/acceptance/2026-09-26-milestone-3.md](docs/acceptance/2026-09-26-milestone-3.md)); agent evals on synthetic data, including `cross_cohort` and the correctness tasks (the latest runs all passed: `evals/results/2026-09-29-*`); report export tests (`test_exports.py`) | A PI does it on the real DataLab; confirm what "the 2025 cohort" means |
| 5 | **Shared knowledge.** Two people edit the knowledge base on the same day; both changes are saved, attributed and revertible, and the check is green | partly | Save & share rebases onto the latest `main` and checks again; conflicts are shown three ways (`test_knowledge.py`, `test_kb_edits.py`, `test_kb_check.py`); commits carry each person's GitHub name, and git keeps the history. No revert in the app | Two people on the real repo, on the same day; decide whether reverting through git is enough |
| 6 | **Updates.** Updating from 1.0.0 to 1.0.1 works, and so does rolling back | partly | CI job `upgrade` (upgrades a data folder from the previous release, then rolls back: `scripts/upgrade-rollback-test.py`); `test_updater.py`, `test_updates.py`, `test_releases.py`, `test_backups.py`, `test_rollback.py`; in-app updates between pre-releases tried on a Mac, and once on Windows (0.2.0b3 → 0.2.0b4, practice) | The same from 1.0.0 to 1.0.1, on both platforms |
| 7 | **Quality.** CI is green: lint, type checks, Python, frontend, and R tests | partly | Green on `main`: CI jobs `backend` (ruff, pyright, pytest, eval graders, Spine conversion, the parity comparison logic), `backend-windows`, `frontend` (API types, `tsc --noEmit`, Vitest, build), `oracle`, `upgrade` and `windows-installer`. `ihsDataR`'s R tests run in DataLab's Pipelines tab, not in this repo's CI | Put the R tests in `ihs-pipelines`' CI and make them required |
| 8 | **Parity with the prototype.** The 8 default workflows and `daily_metrics_2025` give the same outputs in v1 as in the prototype, on the same inputs | partly | [docs/acceptance/2026-09-27-parity.md](docs/acceptance/2026-09-27-parity.md), on synthetic data: the same rows, columns and QC outcomes; the values agree; dates are written differently, for known reasons (`scripts/parity/`) | Yu answers the Garmin HRV date question; format the dates in `ihs-pipelines` |

### Open decisions and blockers

- [ ] **The DBA removes `IHS_2026_ROLE` from `SVC_IHS_AGENT`.** The account
      holds it (UPDATE, DELETE and ALTER on all IHS_2026 tables, plus
      CREATE TABLE, PROCEDURE and DATABASE LINK). Keep `IHS_2025_RO`,
      `IHS_2026_RO` and the direct SELECT grants on the older cohorts, and
      add the two IHS_2026 tables reachable only through the role to
      `IHS_2026_RO`. Until then DataLab enables only the read-only roles on
      each connection, and the Safety check verifies it. It affects the
      prototype too.
- [ ] **The enrolment rule on the real database.** In the synthetic data a
      NULL study ID (`SECONDARYIDENTIFIER`, `STUDY_PARTICIPANT_ID`) means
      screened, never enrolled. Is that true of the real IHS data? It goes
      into the knowledge base once confirmed.
- [ ] **What "the 2025 cohort" means in an analysis.** The evaluation
      graders read it as enrolled participants only. Confirm, or the mood
      task needs regrading.
- [ ] **Accepting milestone 3.** Demonstrated end to end on 2026-09-26
      ([docs/acceptance/2026-09-26-milestone-3.md](docs/acceptance/2026-09-26-milestone-3.md));
      acceptance is the lab's, and it waits on the parity question for Yu.
- [ ] **Attached folders that contain credentials files** (`.env`,
      `auth.json`, …): today DataLab attaches them and lists what it found
      as a warning. Refuse such folders instead?
- [ ] **Windows re-tests** of the current installer and updater on a managed
      machine (the first installer was re-tested on one: see
      [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md), "Windows specifics").
- [ ] **Per-person Oracle accounts (future).** If team members can get
      individual read-only accounts, DataLab should use each person's own,
      so access follows their grants and the database logs show who ran
      what. v1 continues with the shared service account.
- [ ] **Keep git history when splitting repos.** `ihsDataR` and the Spine
      content move to their repos with their commit history.

Decided and done: the cohort schemas were confirmed (2026-09-26; IHS_2018
is visible but has no readable tables); the synthetic schema stays in this
public repo (2026-09-27, after scanning the history); full claim-to-evidence
provenance was brought forward and built; the knowledge base in a session is
an editable copy at `/work/kb`; the early technical spike passed
(2026-09-26, [spikes/2026-09-26-codex-gateway](spikes/2026-09-26-codex-gateway/README.md)).

## Design and onboarding

- **The look**: the "Narrator" structure with the "paper" treatment
  ([docs/DESIGN.md](docs/DESIGN.md)), on every tab. implemented.
- **Help**: one source of words in `docs/guide/`: the glossary, how-to
  pages, tooltips with "Learn more" into Help, and in-app Help that opens on
  the current screen's topic and is bundled with the app. implemented.
- **A first-run tour** of five steps on the practice profile: built, and
  switched off while it's reworked.
- **Website, screenshots and videos**: the guides as a public static site,
  with screenshots scripted from the practice profile, and 1–2 minute videos
  recorded on the practice profile only (synthetic data), narrated with AWS
  Polly. planned. The install page is maintained outside this repo.
