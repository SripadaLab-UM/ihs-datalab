# Architecture

Status: **draft** for v1. This is the proposed design; it has not been
implemented yet.

This document describes how DataLab is built. For *what* it does and *why*,
see [PRODUCT.md](../PRODUCT.md) and the other docs in this folder.

## Principles

- **One process on the host.** Everything DataLab runs outside Docker is a
  single Python program. There is no separate proxy, BFF, broker, or router.
- **Containers do the untrusted work.** Model-driven work runs only in
  containers, and its network goes through a gateway.
- **Off-the-shelf where it matters.** The gateway, SQL parsing, the MCP
  protocol, git, and the keychain use well-known tools. We don't hand-roll
  them.
- **Small modules with clear owners.** No file does two jobs. There are
  target sizes, not hard limits.
- **One source of truth for types.** Frontend API types are generated from the
  backend's OpenAPI schema.
- **Everything pinned.** That includes the lockfiles, the image digests, and
  the Codex CLI version.

## The picture

```
┌─────────────────────────── User's computer ────────────────────────────┐
│                                                                        │
│  Browser ── http://127.0.0.1:<port> ──┐                                │
│                                       ▼                                │
│  ┌──────────────── DataLab (one Python process) ────────────────────┐  │
│  │  Web UI (static)   REST + SSE API      MCP endpoint (/mcp)       │  │
│  │  Sessions ─ Codex adapter ─ checkpoints     Data service ─ audit │  │
│  │  Workflows runner   Git sync   Exports   Safety check            │  │
│  │  SQLite · keychain (U-M key, Oracle password, GitHub token)      │  │
│  └───────┬──────────────────────────────────────────┬───────────────┘  │
│          │ docker CLI                               │ python-oracledb  │
│          ▼                                          ▼                  │
│  ┌──── Docker Desktop ─────────────────────┐  Oracle (over VPN)        │
│  │ per-session internal network            │                           │
│  │  ┌──────────────┐    ┌────────────────┐ │                           │
│  │  │ Agent        │───▶│ Gateway        │─┼──▶ U-M GPT (adds key)     │
│  │  │ codex        │    │ data: model +  │─┼──▶ DataLab /mcp only      │
│  │  │ app-server   │    │  /mcp only     │ │                           │
│  │  │ /work /inputs│    │ research: +    │─┼──▶ internet (not host/LAN)│
│  │  └──────────────┘    │  internet      │ │                           │
│  │                      └────────────────┘ │                           │
│  │  Workflow R steps: agent image, --network none                      │
│  └─────────────────────────────────────────┘                           │
└────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Host app (`backend/`, Python)

| Choice | Why |
|---|---|
| Python 3.12, FastAPI, uvicorn | Same language as the prototype. The ecosystem covers what we need: `python-oracledb`, the official MCP SDK, `keyring`, `sqlglot` |
| SQLite, with plain numbered `.sql` migrations applied at startup | One file, no server. Migrations let data survive updates. The mechanism is small enough to read in a minute |
| Pydantic models for every API shape | Validation, and the OpenAPI schema the frontend types are generated from |
| Docker driven through the `docker` CLI, as async subprocesses | Transparent and debuggable. It works the same on Mac and Windows, and `docker exec -i` gives clean stdio pipes to `codex app-server` |
| `uv` for dependencies (`uv.lock`) | Exact, reproducible installs, and it's the same tool the installer uses |

Modules:

```
src/datalab/
  main.py           app factory, startup and shutdown, serves the built frontend
  config.py         the ~15 settings; defaults plus the user's settings file
  db/               SQLite connection, migrations/*.sql, small query helpers
  api/              one router per area: conversations, sql, workflows,
                    pipelines, knowledge, exports, settings, safety
  sessions/         container lifecycle, Codex app-server adapter, event log,
                    checkpoints, workspace files
  gateway/          per-session network setup; gateway config templates
  data/             Oracle client, SQL check, catalog, audit log, MCP server,
                    synthetic backend
  helper/           research helper: approval queue, temporary research run
  repos/            git sync for ihs-knowledge and ihs-pipelines; Save & share;
                    GitHub App sign-in
  workflows/        workflow file model and validation, runner, run records
  exports/          destinations, manifests, conversation report renderer
  safety/           the Safety check tests
  connect/          outside-tool connector: tokens, scopes, MCP server
  credentials.py    keychain get/set
```

### 2. Sessions and the Codex adapter

- **One container per conversation.** It is started on the first message and
  stopped when idle. It restarts transparently on the next message.
- **Codex runs as `codex app-server`** inside the container. The host talks to
  it over stdio with JSON-RPC, through `docker exec -i`. That gives us threads,
  turns, streaming items, **interrupt**, **resume**, and approval requests
  the UI can answer.
- **`CODEX_HOME` is per session,** on the host, and mounted into the
  container. Its generated `config.toml` sets:
  - the model provider pointing at the gateway (`http://gateway/v1`, with a
    dummy key);
  - the `ihs-data` MCP server at `http://gateway/mcp`, with a per-session
    token;
  - memories, telemetry, and update checks off;
  - web search, plugins, and browser use off in data sessions.

  Codex's own session files survive container restarts, so conversations can
  resume.
- **Mode instructions** are passed as developer instructions on
  `thread/start`. The base `AGENTS.md` and the app skills are baked into the
  image. Lab skills come from the knowledge base copy through Codex's extra
  skill roots.
- **Event log.** Every event is stored in SQLite with a sequence number:
  answer text, reasoning, commands, tool calls, approvals, and outputs. The
  browser follows over SSE and can **reattach from any point**. Closing a tab
  or switching views never kills a turn, and **Stop** sends a real
  `turn/interrupt`. Both were prototype problems.
- **The adapter is small.** It turns app-server notifications into our event
  types. It is written against a pinned app-server schema, regenerated
  whenever the Codex version changes.

### 3. Workspace and checkpoints

- The host folder `sessions/<id>/work` is mounted at `/work`. Attached items
  are mounted read-only under `/inputs`. Query results go under
  `/data/oracle`, also read-only.
- **Checkpoints.** After each turn, the host commits `/work` into a git
  repository whose `.git` directory lives *outside* the mount, so the agent
  can't touch it. Rollback restores from that commit. Very large files are
  excluded and noted, so checkpoints stay fast.
- **Knowledge edits.** `/work/kb` starts as a copy of the synced knowledge
  base. After each turn the host diffs it, and any change becomes a proposed
  edit card.

### 4. Gateway and networks

- For each session, DataLab creates an **internal Docker network**, which has
  no route out. The agent joins only that network. The session-type gateway
  also joins it.
- **Data gateway.** A stock **nginx** image with a generated config. It does
  exactly two things:
  - reverse-proxies `/v1` to U-M GPT, adding the key;
  - reverse-proxies `/mcp` to DataLab.
  Nothing else answers.
- **Research gateway.** The same nginx for `/v1`, plus an off-the-shelf forward
  proxy (e.g. Squid) for general internet access. The forward proxy denies
  the host, private address ranges, and DataLab's `/mcp`.
- The key reaches the gateway from the host at start-up, through a mounted
  secret file. It is never put in the agent container.
- **DNS.** The agent container gets no working upstream DNS. It can resolve
  only `gateway`, so DNS can't be used to leak data. The spike verifies this
  first, because Docker's embedded DNS can forward queries even on internal
  networks.

### 5. Data service

- **`python-oracledb` in thin mode.** No Oracle client install is needed.
  Each query uses a fresh connection with `SET TRANSACTION READ ONLY` and a
  call timeout, and the transaction is always rolled back.
- **SQL check with `sqlglot`.** SQL is parsed properly in the Oracle dialect,
  replacing the prototype's regex. The check:
  - requires a single `SELECT`/`WITH` statement;
  - extracts the referenced schemas and tables, for the audit log and the
    schema allowlist;
  - raises advisory warnings, such as `SELECT *` or a join with no join
    condition.
- **Results** stream to CSV in the session's `/data/oracle` folder. A preview
  goes back to the agent. A concurrency limit and row caps on previews
  protect the database.
- **Catalog** is built from the `generated/schema/` metadata for every cohort.
  It powers `search_catalog`, `describe_table`, and the SQL Playground
  browser.
- **Audit log.** Every query appends one metadata-only row (see
  [SAFETY.md](SAFETY.md)). The same rows feed the Data accessed panel.
- **MCP server.** The official MCP Python SDK, with streamable HTTP, mounted
  at `/mcp`. It exposes `search_catalog`, `describe_table`, `query`, and
  `ask_research_helper`. The per-session token decides which workspace
  results land in.
- **Backends.** `oracle` (real) and `synthetic`. The synthetic backend is an
  **Oracle Database Free** container seeded by our generator, so the SQL
  dialect matches reality. It is used for development, CI, evals, and
  practice mode.

### 6. Research helper

- The `ask_research_helper` MCP call parks the request in an approval queue,
  and the UI shows the card. The MCP call **waits**; Codex's tool timeout
  pauses while it waits.
- When approved, the host starts a temporary research-session container with
  only the approved text. It runs one `codex exec`, collects the answer and
  any files, and destroys the container. The result returns to the waiting
  call.

### 7. Workflows runner

- Workflow files are parsed and validated with Pydantic, which also gives
  clear errors in the editor.
- **Steps run on the host, in order:**
  - SQL steps go through the data service. They are audited and the database
    password is never exposed.
  - R and pipeline steps run in the **agent image** with `--network none`.
    No Codex runs in them. Inputs are mounted read-only, and only declared
    outputs come back.
  - QC checks run on the host.
  - Delivery copies files to the destination.
- Each run gets a folder and a run record in SQLite. A run keeps going if the
  browser closes.

### 8. Repos and Save & share

- `ihs-knowledge` and `ihs-pipelines` are cloned into the data folder and
  kept in sync with the **git CLI**. The installer makes sure Git is present.
- Credentials come from the GitHub App's user token, which is kept in the
  keychain and passed to git through a credential helper. The token never goes
  on disk in plain text, and never enters a container.
- **Save & share** runs these steps:
  1. run the check
  2. commit with the user's name
  3. fetch
  4. rebase
  5. push

  If another person changed the same lines, it shows a plain two-version
  screen instead.

### 9. Frontend (`frontend/`)

| Choice | Why |
|---|---|
| React 19, Vite, TypeScript, Tailwind v4 | Same as the prototype, and it worked well |
| React Router | One route per tab and per conversation, so links and the back button work |
| TanStack Query | Server state, caching, and retries without hand-written fetch plumbing |
| Generated API types (`openapi-typescript`) | The frontend can't drift from the backend |
| A small in-repo component set (shadcn/ui-style, Radix-based) | Accessible primitives, so the long inline class strings go away |
| CodeMirror 6 | One editor for SQL, R, YAML, and Markdown, with diff view |
| react-markdown and vega-embed | Chat rendering and inline charts, carried over from the prototype |

- Structure: `app/` (shell, routes), `components/` (shared), and
  `features/<tab>/` (one folder per tab).
- **The chat component is built once** and used by every tab.

### 10. Images (`images/`)

- **Agent image.** Based on the prototype's "medium" image, with pinned
  versions and base digest. It contains:
  - Codex CLI, R and the tidyverse set, and Python data science tools;
  - Oracle-free tooling, meaning no database drivers the agent could misuse;
  - the base `AGENTS.md` and the app skills.

  One image serves conversations, research sessions, and workflow R steps.
- **Gateway images.** Stock nginx and Squid, pinned, plus our config
  templates.
- CI builds all images and publishes them to the public GitHub Container
  Registry. Each release lists their digests.

### 11. Profiles and outside connectors

- **Profiles.** `datalab` runs with a profile. **real** (the default) uses
  Oracle. **practice** uses the synthetic backend only. Each profile has its
  own data folder, port, and keychain namespace, and they never share state.
  Practice mode for new colleagues is simply the practice profile.
- **Connectors.** An MCP server (`/connect/mcp`) and a `datalab` CLI let
  outside AI tools, such as Claude Code or a host-side Codex, drive DataLab
  through the same API as the UI. Their tools cover:
  - conversations: start, send, read events, stop;
  - SQL Playground queries;
  - workflows: run and read run records;
  - knowledge pages;
  - the Safety check and diagnostics.
- **Scopes.** Connector tokens are created in Settings and carry a scope,
  `full` or `metadata`. A single dependency on every API route enforces it.
  `full` is only possible in the practice profile. This is also how we build
  and test DataLab: an outside agent drives the practice instance end to end,
  including the UI in a browser.
- The connector MCP endpoint is distinct from the agent-facing `/mcp`. The
  agent-facing endpoint is reachable only through a session's gateway. The
  connector endpoint is reachable only on the host loopback.

## Repository layout

```
ihs_datalab/
  README.md  PRODUCT.md  docs/
  backend/     pyproject.toml, uv.lock, src/datalab/, tests/
  frontend/    package.json, package-lock.json, src/
  images/      agent/ (Dockerfile, AGENTS.md, skills/), gateway/
  synthetic/   synthetic IHS generator and schema, eval tasks
  installer/   macos.sh, windows.ps1, uninstallers
  .github/     ci.yml, release.yml
```

**Size target.** About 10k lines each for the backend and the frontend,
excluding tests. The prototype was roughly 29k and 23k.

## Testing

- **Unit:** backend (pytest), frontend (Vitest), plus the knowledge base and
  workflow checks.
- **Integration, in CI:** the real app against a synthetic Oracle Free
  container and real Docker. A scripted Codex conversation exercises every
  tool.
- **Safety:** the Safety check suite, plus the adversarial test (escape the
  network, read the host, write to Oracle, find keys). Both run in CI on every
  change, not only at release.
- **Evals:** a dozen representative research tasks on synthetic data. They
  are run manually before releases and after any change to prompts, skills, or
  the Codex version.

## Build order

v1 is one release, built through internal milestones. Each milestone ends in
something we can use end to end ourselves.

0. **Spike.** Current Codex CLI, `app-server`, and the nginx gateway with a
   dummy key against U-M GPT; an approval request answered by our code;
   the DNS lockdown; host reachability from Docker Desktop. Run on Mac, with
   a quick check on Windows.
1. **Foundations.** Repo, CI, and lockfiles. A first synthetic dataset, and
   the data service with SQL check, audit log, and MCP.
2. **First data session.** A conversation starts a container, Codex answers
   a question about synthetic data, and the chat streams in the new UI. The
   Safety check and adversarial test run in CI.
3. **Complete workspace.**
   - Inputs, outputs, checkpoints, and rollback.
   - Export destinations and conversation export.
   - Modes and skills, the Data accessed panel.
   - Research sessions and the research helper.
4. **SQL Playground.**
5. **Knowledge.**
   - GitHub App sign-in and repo sync.
   - The Knowledge tab, proposed-edit cards, and Save & share.
   - The check script, and moving the Spine content over.
6. **Workflows and Pipelines.**
   - The runner and both tabs.
   - Moving `ihsDataR` over, with its history.
   - The default workflows.
7. **Distribution.** Mac and Windows installers, updates and rollback, the
   uninstaller, and the release pipeline. Test on the Windows machine.
8. **Finish.** Evals, the USER_GUIDE, a dry run with one or two colleagues,
   and the definition of done.
