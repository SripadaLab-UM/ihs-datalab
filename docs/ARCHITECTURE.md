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
│  │  Web UI (static)   REST + SSE API                                │  │
│  │  Model relay (/relay/v1): validates requests, adds U-M key ──────┼──┼──▶ U-M GPT
│  │  Agent tools (/mcp)   Data service ─ audit ──────────────────────┼──┼──▶ Oracle (VPN)
│  │  Sessions ─ Codex adapter ─ checkpoints   Workflows runner       │  │
│  │  Git sync   Exports   Safety check   SQLite · keychain           │  │
│  └───────┬───────────────────────────────▲──────────────────────────┘  │
│          │ docker CLI                    │ host.docker.internal        │
│          ▼                               │ (/relay and /mcp only)      │
│  ┌──── Docker Desktop ───────────────────┼─────┐                       │
│  │ per-session internal network          │     │                       │
│  │  ┌──────────────┐    ┌────────────────┴───┐ │                       │
│  │  │ Agent        │───▶│ Gateway (nginx)    │ │                       │
│  │  │ codex        │    │ routes /v1 → relay │ │                       │
│  │  │ app-server   │    │ routes /mcp → host │ │                       │
│  │  │ /work /inputs│    │ research: + Squid ─┼─┼──▶ internet (not host/LAN)
│  │  └──────────────┘    └────────────────────┘ │                       │
│  │  Workflow R steps: agent image, --network none                      │
│  └─────────────────────────────────────────────┘                       │
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
  relay/            model relay: session tokens, request validation, key injection
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
  Containers run with `--init` (so orphaned processes are reaped),
  `--dns 192.0.2.1` (see §4), non-root, and with capabilities dropped.
- **Codex runs as `codex app-server --strict-config`** inside the container.
  The host talks to it over stdio with JSON-RPC, through `docker exec -i`.
  The protocol provides threads, turns, streaming items, interrupt, resume,
  and approval requests the UI can answer. The sequence was verified in the
  spike on 0.157.1:
  1. `initialize`, then `initialized`;
  2. `thread/start` with `developerInstructions`;
  3. `turn/start`, streaming until `turn/completed`;
  4. `turn/interrupt` and `thread/resume` as needed.
- **Stop must kill commands too.** In 0.157.1, `turn/interrupt` ends the turn
  but *not* the shell commands it started. After an interrupt, the adapter
  kills the turn's process groups inside the container. On startup it sweeps
  any orphans left behind.
- **Host code never trusts agent-writable folders.** The agent can plant
  symlinks in `/work` and `/codex-home`.
  - DataLab never writes a file into those folders. The Codex config lives
    outside them and is mounted read-only over `config.toml`, so the agent
    also can't change its own configuration.
  - When DataLab reads from them, for example to list outputs or export
    files, it resolves paths and refuses anything that leads outside the
    session folder.
- **`CODEX_HOME` is per session,** on the host, and mounted into the
  container. It holds conversation content (session files and a local log),
  so it counts as study data and lives in the session folder. Its generated
  `config.toml` sets:
  - `model_provider` pointing at `http://gateway/v1`, with
    `env_key` set to the **session token** (§4), never the real key;
  - the `ihs-data` MCP server at `http://gateway/mcp`, with the same token;
  - `approval_policy = { granular = { mcp_elicitations = true, … } }` and
    `default_tools_approval_mode = "approve"`, so only DataLab's own approval
    requests reach the user. `never` would silently decline them;
  - analytics, feedback, otel, history, and update checks off;
  - features that open new channels off: memories, apps and plugins,
    browser and computer use, image generation, multi-agent, and realtime.
    Web search is also off in data sessions. Most of these are on by default,
    so all are listed explicitly.
- **Mode instructions** are passed as `developerInstructions` on
  `thread/start`. The base `AGENTS.md` and the app skills are baked into the
  image. Lab skills come from the knowledge base copy through Codex's extra
  skill roots.
- **Event log.** Every event is stored in SQLite with a sequence number:
  answer text, reasoning, commands, tool calls, approvals, and outputs. The
  browser follows over SSE and can **reattach from any point**. Closing a tab
  or switching views never kills a turn. Both were prototype problems.
- **If DataLab stops mid-turn,** the turn is marked interrupted on restart.
  The conversation resumes normally, which the spike verified.
- **The adapter is small.** It turns app-server notifications into our event
  types. It is written against a pinned app-server schema, regenerated with
  `codex app-server generate-json-schema` whenever the Codex version changes.

### 2b. Rigor features

- **Analysis plans** (`sessions/plans.py`). The agent drafts one through the
  `propose_plan` tool on `/mcp`. Like the research helper, the plan and the
  person's decision are held on the host (`sessions/approvals.py`); the card
  appears when Codex forwards the request, the person may edit any part, and
  the approved version is frozen in SQLite (`plans`) with a timestamp and a
  hash. The agent gets the approved text back and is told to label off-plan
  work as exploratory.
- **Tracing** (`sessions/tracing.py`). After each completed turn, DataLab
  takes the numbers in the final answer (leaving out code, links, dates,
  years, small counts, and confidence levels) and looks for each, allowing
  for rounding and percentages, in what the turn produced: each command's
  whole output, the data tool's query results (kept in memory for the
  turn, never stored), and the data files (CSV/TSV) in `/work/outputs`. Not
  the agent's own prose (its reports would "trace" whatever they say), and
  not research-helper answers or plans. Clock times and dates are ignored.
  The chat says how many numbers were found and lists the rest. It's an aid:
  a coincidental match passes, and a correct derived number is flagged.
- **Rigor review** (`sessions/rigor.py`). A per-conversation switch, on by
  default in Analysis mode. After each completed turn, DataLab runs
  app-server `review/start` (inline, custom instructions). Review mode starts
  without the conversation's history, so the instructions carry the
  question, the approved plans, the queries run, and the answer, fenced and
  marked as data. It's skipped when a turn did no work and states no
  numbers. The review's text (the `exitedReviewMode` item) is shown under
  the answer, with a button to ask the agent to address it; the review can
  be stopped like a turn. (Codex 0.157 answers `review/start` with a
  different turn id than the one it runs, so the runtime takes the id from
  `turn/started`.)

### 3. Workspace and checkpoints

- The host folder `sessions/<id>/work` is mounted at `/work`. Attached items
  are mounted read-only under `/inputs`. Query results go under
  `/data/oracle`, also read-only.
- **Attaching** (`sessions/inputs.py`, `sessions/picker.py`). The UI asks
  DataLab to open the native picker (`osascript` on a Mac, a Windows Forms
  dialog through PowerShell on Windows); what the person picks is resolved to
  its real location, checked against the refused places (see SAFETY.md), and
  recorded. Mounts use `--mount type=bind,…,readonly`, so a missing source
  is an error rather than an empty folder. The practice profile never opens
  the picker; it offers synthetic samples shipped with DataLab.
- **Checkpoints.** After each turn, the host records every file in `/work`
  in a content-addressed store in the session folder, outside every mount,
  so the agent can't touch it. Unchanged files cost nothing extra. This is
  DataLab's own small module (`sessions/checkpoints.py`) rather than git:
  git would read `.gitattributes` from the agent's folder, and it would be
  one more thing to install on Windows.
  - The agent's container is **paused** while a checkpoint is taken, so
    nothing can swap a file for a link mid-read. Links are recorded as links
    and never followed, and every file opened must resolve inside `/work`.
  - **The browser sees workspace files from the latest checkpoint**, never
    the live folder: reading a folder the agent is changing can be raced,
    and on Windows there's no `O_NOFOLLOW` at all. So the Outputs panel
    updates after each turn. Query results in `/data/oracle` are DataLab's
    own read-only files and are served directly.
  - Each checkpoint has a small summary file and a separate contents file,
    so listing History stays cheap. Old checkpoints aren't pruned yet (a
    v1.1 item: a retention limit and clean-up of unused objects).
- **What rollback restores, precisely.** It restores **files in `/work`** to
  how they were after a chosen turn. Around that:
  - The conversation itself is *not* rewound. The agent receives a note that
    files were restored, and the chat shows the same note.
  - The container is stopped first, which also stops running commands. It
    starts again with the next message, and Codex resumes the thread.
  - The current files are checkpointed first, so a rollback can be undone.
    Anything that checkpoint couldn't save is left exactly as it is.
  - The container must be confirmed gone before any file changes, and no
    turn can start meanwhile (a turn being set up counts as busy).
  - Query results in `/data/oracle` are immutable, so they need no restoring.
  - Files over a size threshold (100 MB) are excluded from checkpoints. The
    rollback screen lists any that can't be restored; they are left as they
    are.
  - We don't use Codex's own thread rollback.
- **Knowledge edits.** `/work/kb` starts as a copy of the synced knowledge
  base. After each turn the host diffs it, and any change becomes a proposed
  edit card.

### 4. Model relay, gateway, and networks

- **Model relay (in the host app).** All model traffic ends at
  `/relay/v1` in DataLab:
  - Each session's Codex uses a **session token** as its API key. The relay
    looks the token up, which tells it the session and its type. It then
    swaps in the real U-M key from the keychain. **The real key never leaves
    the host process.** No container or file holds it.
  - The relay allows only what Codex needs:
    - `GET /v1/models`;
    - `POST /v1/responses` with `store: false`;
    - tools of type function or custom, or client-side tool search;
    - images only as inline `data:` URLs;
    - only known request fields.
  - Everything else is refused, including:
    - **hosted tools** such as web search, remote MCP, and code
      interpreter;
    - **URLs the provider would fetch** (image or file URLs);
    - background or stored responses;
    - other endpoints.
  - Research sessions get the same rules, plus hosted web search.
  - The spike showed why this matters. Bypassing Codex, a request sent
    straight through a blind proxy got U-M to run web search, call a remote
    MCP server, and run code. Each of those is a way out for data.
  - Responses stream straight through. The relay adds no buffering.
  - **When U-M GPT is busy** (429, or a 5xx before any of the answer has
    streamed), the relay itself retries the same checked request, honouring
    the server's `Retry-After`, up to three attempts within 60 seconds
    (`relay/recovery.py`). It never retries a used-up allowance, a refused
    key, a missing model or a bad request, and nothing after Stop. Codex's own
    request retries are kept to one, so a request makes at most two rounds of
    relay attempts (six upstream calls, about two minutes of waiting at most).
    A failure mid-stream is Codex's to retry (up to twice), as before. Each failure is
    recorded as metadata only (kind, status, provider code, wait), and the
    conversation gets a `model_status` event, so the chat can say "busy,
    trying again in 8 s" and, if it gives up, explain it plainly and offer
    Continue (the same thread picks up, so nothing is sent twice). A rigor
    review that couldn't finish can be run again on its own.
- **Gateway (stock nginx).** It is a pure router with no secrets:
  - `/v1/*` goes to DataLab's relay;
  - `/mcp` goes to DataLab's agent tools;
  - everything else gets a 404.

  It reaches the host through `host.docker.internal`, which on Docker Desktop
  reaches services bound to `127.0.0.1` (verified on Mac).
- **Networks.** For each session, DataLab creates an **internal Docker
  network**, which has no route out. The agent joins only that network. The
  gateway joins it and the normal bridge. Network and container names carry
  the DataLab instance (a short hash of the data folder) as well as the
  session (`datalab-<instance>-<session>-agent`), so two DataLabs whose data
  folders hold the same conversation never share or replace each other's
  containers. Cleanup finds them by label, never by name.
- **Research sessions** add **Squid** as a forward proxy for the internet
  (verified in the spike). It is started with `-n` so it doesn't do reverse
  lookups. Its deny rules come *first* and cover the host, private and
  link-local ranges, IP-encoding tricks, and container names. Both
  `HTTPS_PROXY` and lowercase `https_proxy` are set. Squid is restarted,
  never reconfigured in place.
- **DNS.** On Docker 29, an internal network's resolver answers only container
  names; external names fail and the queries never leave (verified with
  tcpdump). As a second layer for older engines, containers also get
  `--dns 192.0.2.1`, an unroutable address. The adversarial suite includes the
  tcpdump leak test.
- **Session tokens** are random and scoped to one session. They are revoked
  when the session ends. A token only works through the relay and `/mcp`, and
  only for its own session.

### 5. Data service

- **`python-oracledb` in thin mode.** No Oracle client install is needed.
  Each query uses a fresh connection. That connection first runs
  `SET ROLE` with only the configured read-only roles, because the shared
  account also holds a write role (see [SAFETY.md](SAFETY.md)). It then runs
  `SET TRANSACTION READ ONLY`, and the
  transaction is always rolled back.
- **Guardrails** (in v1):
  - **Deadlines.** `call_timeout` only limits each database round trip, so
    every query also has an **end-to-end deadline**. On expiry, DataLab calls
    `connection.cancel()` and closes the connection.
  - **Extraction caps.** Each query may return at most a set number of rows
    and bytes. Going above that needs an explicit OK from the user, not the
    agent.
  - **Disk-space check** before and during writing. A query stops cleanly,
    removing partial output, if space runs low.
  - **Concurrency.** Few queries run at once across the whole app, and only a
    limited number of containers run at once.
- **SQL check with `sqlglot`.** SQL is parsed properly in the Oracle dialect,
  replacing the prototype's regex. The check:
  - requires a single `SELECT`/`WITH` statement;
  - extracts the referenced schemas and tables, for the audit log and the
    schema allowlist;
  - raises advisory warnings, such as `SELECT *` or a join with no join
    condition.
- **Results** stream to CSV in the session's `/data/oracle` folder. Each
  result is immutable once written. A preview goes back to the agent.
- **Catalog** is built from the `generated/schema/` metadata for every cohort.
  It powers `search_catalog`, `describe_table`, and the SQL Playground
  browser.
- **Audit log.** Every query appends one metadata-only row (see
  [SAFETY.md](SAFETY.md)). The same rows feed the Data accessed panel.
- **Agent tools (`/mcp`).** The official MCP Python SDK, with streamable HTTP.
  It exposes `search_catalog`, `describe_table`, `query`,
  `ask_research_helper`, and `propose_plan`, plus the metadata helpers: join
  paths, concept lookup, cohort plan draft, and survey dictionary search. The
  session token decides which workspace results land in.
- **Backends.** `oracle` (real) and `synthetic`. The synthetic backend is an
  **Oracle Database Free** container seeded by our generator, so the SQL
  dialect matches reality. It is used for development, CI, evals, and
  practice mode.

### 6. Research helper

- The `ask_research_helper` tool sends an MCP **elicitation** to Codex. Codex
  forwards it to our client as `mcpServer/elicitation/request`, and the UI
  shows the approval card. Behaviour verified in the spike:
  - the tool **waits**; Codex's tool timeout pauses, and waits of 30 s and
    150 s were tested;
  - **decline** returns cleanly to the agent;
  - **Stop** while pending ends the turn. Codex sends `serverRequest/resolved`,
    which withdraws the card.
- Gotcha: Codex rejects an elicitation schema with a top-level `title`, which
  the MCP SDK adds by default. We send a plain `type`/`properties`/`required`
  schema.
- When approved, the host starts a temporary research-session container with
  only the approved text (`sessions/helper.py`): its own empty workspace, no
  attachments, no query results, no route to the data tools, the research
  proxy for the internet. It runs one `codex exec` with a time limit,
  collects the answer (text only in v1), and destroys the container and its
  folder. The result returns to the waiting call, and the answer is shown
  under the approval card.
- The approval request carries a small JSON message
  (`{"datalab": "research_helper", "question": …}`) from DataLab's own
  `ihs-data` server; anything else Codex forwards is declined without
  asking. The runtime maps Codex's request id to the card, so
  `serverRequest/resolved` (Stop) and the end of a turn withdraw it.
- If DataLab restarts while an approval is pending, the turn ends as
  interrupted, and the card disappears.

### 7. Workflows runner

- Workflow files are parsed and validated with Pydantic, which also gives
  clear errors in the editor.
- **Steps run in order:**
  - SQL steps go through the data service, with the same guardrails and
    audit. The database password is never exposed.
  - R, pipeline, and **custom R QC** steps run in the **agent image** with
    `--network none`. No Codex runs in them. Inputs are mounted read-only,
    and only declared outputs come back.
  - Built-in QC checks, such as row counts, required columns, and unique
    keys, run in DataLab's own trusted code.
  - Delivery copies files to the destination.
- **Run records** capture everything needed to replay a run: the workflow file
  and repo commit, the agent image digest, parameters, each SQL statement with
  its binds, random seeds, output checksums, and the **extracted inputs**,
  which are kept in the run folder.
  - **Run again** re-extracts from today's database.
  - **Replay** reruns the processing on the original inputs.
- Each run gets a folder and a run record in SQLite. A run keeps going if the
  browser closes.

### 8. Repos and Save & share

- `ihs-knowledge` and `ihs-pipelines` are cloned into the data folder and
  kept in sync with the **git CLI**. The installer makes sure Git is present.
- Credentials come from the GitHub App's user token, which is kept in the
  keychain and passed to git through a credential helper. The token never goes
  on disk in plain text, and never enters a container.
- **The checks are DataLab's own code.** The knowledge-base check and the
  workflow-file check ship with DataLab. DataLab never runs a script taken
  from a repo on the host. Repo tests, such as `ihsDataR`'s R tests, run in a
  no-network container.
- **Save & share binds to exactly what was reviewed:**
  1. Run the checks, including the participant-data scan, on the proposed
     change. For pipelines, run the tests too.
  2. Commit with the user's name.
  3. Fetch and rebase onto the latest `main`.
  4. **If anyone else's change landed in between**, re-run the checks on the
     rebased result, and the pipeline tests if any pipeline files changed.
     Show the user what changed before pushing.
  5. Push the exact commit that passed. Record the test result against that
     commit.
- The participant-data scan covers workflow files too, since SQL literals,
  comments, and parameter defaults could contain IDs.
- If another person changed the same lines, a plain two-version screen appears
  instead.

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
- **The browser is part of the boundary.** Content made by the agent must not
  be able to send data out when a user views it:
  - **An app-wide Content Security Policy** allows network requests only to
    DataLab itself. That means no external images, scripts, fonts, or fetches,
    so a Markdown image such as `![](https://…?data=…)` or a chart's
    `data.url` can't leak anything.
  - **Agent-made HTML** (reports, dashboards) is shown in an iframe with
    `sandbox=""` (no allowances) and served with a `sandbox` policy of its
    own, from `/preview/<token>/…` capability links. **Scripts are off**: a
    script can always navigate its own frame to an outside URL carrying
    data, and no CSP directive blocks that. Without scripts, the page's
    styles, images, and fonts still load from the same folder; nothing else
    can. Meta refreshes and forms are blocked by the sandbox too. The page
    is also cleaned server-side (`htmlclean.py`: parsed and written out
    again without resource hints, `<meta>`, `<base>`, scripts, or frames),
    and served only when the browser asks for it as a frame
    (`Sec-Fetch-Dest`). (An earlier plan used a separate origin with scripts
    on; it doesn't close the navigation hole.)
  - **Workspace files** opened through the API are served as plain text or
    images, with a `sandbox` policy, so they are inert even in their own tab.
  - **Exported conversation reports** embed the same "no network" CSP, so
    they stay inert when opened later in any browser. The web UI renders the
    report with the chat's own transcript and Markdown code, drawing charts
    as SVG (no scripts needed); DataLab cleans that markup (`htmlclean.py`)
    and wraps it in a page whose first element is the policy
    (`exports.py`).
- **Local API protection.** The app listens on `127.0.0.1` only. The launcher
  opens it with a one-time token that sets a session cookie, so other local
  programs or web pages can't drive DataLab's API. The cookie is named for
  the port (`datalab_session_8766`): browsers share cookies between ports of
  one host, so two DataLabs on one computer (practice and an evaluation run,
  say) would otherwise sign each other's windows out.

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
- **Scopes.** Connector tokens are created in Settings and carry a scope. A
  single dependency on every API route enforces it. **In v1, connectors exist
  only in the practice profile** (scope `full`). Real-profile access with a
  `metadata` scope is deferred until its exact fields and error handling are
  specified. This is also how we build and test DataLab: an outside agent
  drives the practice instance end to end, including the UI in a browser.
- **Practice really is synthetic-only.** In the practice profile:
  - you can't attach host files or folders; bundled synthetic fixtures are
    available instead;
  - export destinations are limited to a disposable practice exports folder;
  - Dropbox and the lab repos' write access are unavailable.
  So nothing real can find its way in.
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
- **Safety:** the Safety check suite, plus the adversarial test. Both run in
  CI on every change, not only at release. The adversarial test attempts to:
  - escape the network, including DNS lookups watched with tcpdump;
  - send hosted-tool and URL-fetch requests straight to the relay;
  - read the host;
  - write to Oracle;
  - find keys;
  - exfiltrate through an HTML preview or a Markdown image.
- **Upgrade and rollback:** CI upgrades a data folder from the previous
  release, rolls it back, and checks that nothing is lost.
- **Prototype comparison (release check):** the 8 default workflows and
  `daily_metrics_2025` run in both the prototype and v1, and their outputs
  must match. This is a one-off validation, not a feature.
- **Evals:** a dozen representative research tasks on synthetic data. They
  are run manually before releases and after any change to prompts, skills, or
  the Codex version.

## Build order

v1 is one release, built through internal milestones. Each milestone ends in
something we can use end to end ourselves.

0. **Spike** ✅ done 2026-09-26, on Mac. It confirmed:
   - app-server with Codex 0.157.1;
   - the nginx gateway, the DNS lockdown, and host reachability;
   - elicitation approvals, including decline and stop;
   - resume after a restart;
   - the Squid research proxy.

   It also found the hosted-tool bypass, fixed by the relay, and the
   interrupt-doesn't-kill-commands issue, fixed in the adapter. Evidence is
   in [spikes/2026-09-26-codex-gateway](../spikes/2026-09-26-codex-gateway/README.md).
1. **Foundations** ✅ done 2026-09-26.
   - Repo, CI, and lockfiles.
   - The synthetic dataset: 3 cohorts on Oracle Free, with a write role that
     mirrors the real account's.
   - The data service: SQL check, read-only-roles sessions, guardrails, the
     Data accessed log and audit log, the catalog, and the `ihs-data` MCP
     tools.
   - Verified against both the synthetic and the real database.
   - The metadata helpers and the frontend skeleton move to milestone 2.
2. **First data session, installed on both platforms.** A conversation
   starts a container, Codex answers a question about synthetic data, and the
   chat streams in the new UI. The Safety check and adversarial test run in
   CI. A **minimal installer and upgrade rehearsal on Mac and Windows** is
   included, since Windows Docker networking could break the design and we
   should find out early.
   - 2a–2c done 2026-09-26: the runtime, the conversations API, and the
     web UI.
   - 2d done 2026-09-26:
     - the Safety check (Settings & Safety screen, `datalab safety-check`,
       and CI);
     - research sessions with internet through Squid;
     - a CI test that the check catches a deliberately leaky sandbox.
     Added in 3g: the tcpdump DNS leak test in CI, a preview containment
     row, and browser-side Markdown and chart leak tests.
3. **Complete workspace.**
   - Inputs, outputs, checkpoints, and rollback. (3a done 2026-09-26:
     checkpoints and rollback, the Outputs and History panels, the file
     viewer, and HTML previews. 3b done the same day: attaching files and
     folders through the native picker, read-only mounts, and practice
     samples. 3c done the same day: export folders, exporting outputs with
     a manifest, and "Export conversation" as one self-contained page.
     3d done the same day: the four modes' full instructions and starter
     prompts, the sql-extraction, statistical-review, and academic-figures
     skills, and the model picker, with the relay allowing only approved
     models. 3e done the same day: the research helper, from the
     approval card to the throwaway research container. 3f done the same
     day: analysis plans, claim tracing, and the rigor review. 3g done the
     same day: the join_paths and find_concept metadata tools, confirmed
     external links, and the leak tests above.)
   - Export destinations and conversation export.
   - Modes and skills, the Data accessed panel.
   - Research sessions and the research helper.
   - **Status: implemented, tested, and demonstrated (3h, 2026-09-26);
     acceptance pending the lab's review.** 3h recorded, on the practice
     profile, against a commit:
     - the journey discover tables → approve a plan → analyse → inspect a
       chart → revise it → restore a checkpoint → export exactly the
       reviewed version, through the UI, including a Stop and a restart;
     - the research helper approved, edited, declined, and cancelled;
     - a first scientific evaluation set with answers calculated
       independently from the synthetic data: participant counts,
       missingness, cross-cohort joins, duplicate rows, empty tables, and
       within- versus between-person analyses, checking denominators,
       exclusions, uncertainty, and interpretation. Re-run when prompts,
       skills, the model, or the Codex version change.

   | Milestone | Implemented | Tested automatically | Demonstrated end to end | Accepted |
   |---|---|---|---|---|
   | 0 Spike | yes | n/a | yes (Mac) | yes |
   | 1 Foundations | yes | yes | yes | yes |
   | 2 First data session, both platforms | yes | yes (CI, Linux) | Mac only; Windows fresh install pending | no |
   | 3 Complete workspace | yes | yes | yes, 2026-09-26 ([acceptance](acceptance/2026-09-26-milestone-3.md), [evals](../evals/results/)) | pending the lab's review |
4. **SQL Playground.**
5. **Knowledge.**
   - GitHub App sign-in and repo sync.
   - The Knowledge tab, proposed-edit cards, and Save & share.
   - The check script, and moving the Spine content over.
6. **Workflows and Pipelines.**
   - The runner and both tabs.
   - Moving `ihsDataR` over, with its history.
   - The default workflows.
7. **Distribution polish.** Complete the installers, updates and rollback
   with database backup, the uninstaller, and the release pipeline. Test on
   the Windows machine.
8. **Finish.** Evals (growing from 3h's set), full claim-to-evidence
   provenance (each number linked to its query, script, and output, and the
   "How was this made?" view; milestone 3 only checks that numbers appear in
   the turn's outputs), the USER_GUIDE, a dry run with one or two
   colleagues, and the definition of done.
