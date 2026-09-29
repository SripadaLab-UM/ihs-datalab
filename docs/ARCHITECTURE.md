# Architecture

Status: **as built**, 0.3.0b1 and `main` (2026-09-29). Where something
isn't built yet it says so. What DataLab does, and how ready each part is,
is in [PRODUCT.md](../PRODUCT.md); this document is how it's built. It names
each module's responsibility rather than every file, so it stays true when
large files are split.

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
| Python 3.13 (`requires-python = ">=3.13"`), FastAPI, uvicorn | Same language as the prototype. The ecosystem covers what we need: `python-oracledb`, the official MCP SDK, `keyring`, `sqlglot` |
| SQLite, with plain numbered `.sql` migrations applied at startup | One file, no server. Migrations let data survive updates. The mechanism is small enough to read in a minute |
| Pydantic models for every API shape | Validation, and the OpenAPI schema the frontend types are generated from |
| Docker driven through the `docker` CLI, as async subprocesses | Transparent and debuggable. It works the same on Mac and Windows, and `docker exec -i` gives clean stdio pipes to `codex app-server` |
| `uv` for dependencies (`uv.lock`) | Exact, reproducible installs, and it's the same tool the installer uses |

Modules (`backend/src/datalab/`), by responsibility:

```
app.py, web.py      app factory, startup and shutdown, local API protection,
                    serves the built frontend
cli.py, setup.py    the `datalab` command (serve, setup, practice-db, github,
                    repos, versions, backup, rollback, safety-check, catalog,
                    kb-check, …) and the install steps shared by both installers
config.py           settings: defaults plus the profile's settings.toml
db/                 SQLite connection, numbered migrations, backups, rollback
api/                one router per area: conversations, files, code, inputs,
                    provenance, sql, workflows, pipelines, knowledge, github,
                    exports, settings, safety, support
sessions/           conversations: container lifecycle (containers.py, with
                    gateway.conf and squid.conf), the Codex app-server
                    adapter and turn runtime, the conversation manager and
                    event store, modes, session tokens, approvals (research
                    helper, plans), checkpoints, tracing, provenance, the
                    rigor review, the Code tab's files, after-turn hooks
relay/              model relay: request policy, key injection, busy retries
data/               Oracle client and errors, SQL check, catalog (and where
                    it comes from), audit log, the `ihs-data` agent tools,
                    metadata helpers, SQL Playground drafts
playground.py       the SQL Playground's own queries and results
knowledge/          the knowledge base: each conversation's copy, proposals,
                    page edits, suggestions, the check, Save & share
pipelines/          the pipelines repo: proposals, a person's edits, tests
                    in a no-network container, Save & share
repos/              git clones and sync, GitHub App sign-in, credential helper
workflows/          workflow file model and validation, the runner, QC and
                    small-cell suppression, run records, drafts, stages,
                    practice built-ins
exports.py, export_folders.py, htmlclean.py
                    export folders, exporting files and conversation reports
safety/             the Safety check and canaries
practice_db/        the synthetic database: generator, guard, verify, and
                    the practice database's container lifecycle
releases.py, updates.py, updater.py, update_gate.py, signing.py, release_keys.py
                    update checks, signed releases, installing an update
storage.py, diagnostics.py, support.py
                    the Storage view, Copy diagnostics, support reports
credentials.py, secret_prompt.py
                    keychain get/set; masked prompts at setup
datalock.py         one DataLab per data folder (see below)
launcher_icons.py, windows_vm.py, docker_path.py
                    platform helpers
```

**Seams for the parallel milestones.** So that milestones 4 to 7 can be
built side by side without editing the same core files, each area has its
own slot, and the shared modules offer extension points:

- **Routers.** `api/sql.py`, `api/knowledge.py`, `api/workflows.py`,
  `api/pipelines.py`, and `api/settings.py` each have a
  `build_<name>_router(services)` under `/api/<name>`, registered once in
  `create_app`. `services` is a small frozen dataclass in the same module
  (`SqlServices`, `KnowledgeServices`, …) holding what that area uses. Each
  has a `GET /api/<name>/status`.
- **Query origin.** `DataService.run_query(..., origin=)` and the access log
  record what a query was for: `conversation` (the default), `playground`,
  or `run`. `session_id` stays the owner: a conversation id, a `pg_…`
  Playground id, or a `run_…` workflow run id; a mismatch is refused.
  `AccessLog.for_origin(origin, owner)` lists one owner's queries, and
  `for_session` stays the conversation's Data accessed panel.
- **Settings.** `settings.toml` has `[playground]`, `[repos]`, `[workflows]`,
  and `[updates]` tables, read into frozen dataclasses on `Settings`
  (`PlaygroundSettings`, …). Unknown keys and wrong types are refused.
- **Session hooks** (`sessions/hooks.py`), registered on the
  `SessionManager`:
  - `register_after_turn(async fn(conversation_id, turn_info))` runs after
    every turn's checkpoint, number check, and review, before `turn_done`.
    `TurnInfo` gives the turn number, how it ended, where its events start,
    and the checkpoint to read files from. A turn's hooks run concurrently
    within one 30-second limit; one that fails is logged, and one still
    going is cancelled and left to end on its own, so `turn_done` is always
    written. Hooks must not block the event loop or catch cancellation.
    Knowledge uses one to diff `/work/kb`.
  - `register_workspace_seed(name, fn(conversation, staging) -> base,
    into=...)` runs once per conversation, before its first turn only. The
    seed writes into a fresh folder DataLab made in the session folder,
    outside `/work`; DataLab then renames it to `/work/<into>` in one step,
    refusing if anything is already there (a link included), and records
    the base it returns (`workspace_base`). It is never run again: not
    after a failure or a crash (the chat gets a notice), and not when the
    container restarts, so the agent's edits are kept. Knowledge uses one
    for the `/work/kb` copy.
  - `register_mounts(fn(conversation) -> list[Mount], roots=[...])` adds
    read-only mounts when a conversation's container starts, alongside its
    attachments. Targets are normalised and must be under `/mnt`; duplicates
    are dropped. Sources are resolved, following links, and must be inside
    the provider's declared roots. Nothing in DataLab's data folder can be
    mounted except under `repos/` (not the database, the logs, or any
    conversation's folder), and no root may hold the data folder. Nor can a
    home folder, a dot-folder in it, or a credentials file. The resolved
    path is what's mounted.
- **Migrations** are numbered, one per change (0001 to 0014 on `main`; see
  `db/migrations/README.md`), and CI upgrades a data folder from the
  previous release through them.
- **Data-folder lock.** `serve` (and `try` and `safety-check`) take an
  exclusive lock on `<data folder>/.lock` before anything else (`fcntl.flock`
  on Mac and Linux, `msvcrt.locking` on Windows, and a process-id check where
  a file system has no locks). A second DataLab on the same folder is
  refused with a message: two launches at once could both pass the port
  check, and the second would then remove the first's containers and end
  its turns.
- **Quitting.** Ctrl-C on `serve` is the normal way to quit: uvicorn shuts
  the app down (the lifespan stops sessions, containers and keepers), then
  re-raises the Ctrl-C, which `cli._run_until_stopped` turns into one line,
  "DataLab stopped.", and exit code 0 (a deliberate quit, not a failure; the
  launchers don't treat it as one). The hourly update check is stopped
  however it ends.

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
  work as exploratory. The plan types and their sections live in one
  versioned registry (`sessions/plan_schema.py`): it checks plans (types,
  required sections, duplicates, hidden text, size limits), writes the
  tool's description, is served to the card at `/api/plan-schema`, and adds
  the type's checks to the rigor review. A version-2 plan stores its schema
  version, type, labels, and section order in `content_json`, so the hash
  covers them and a frozen plan shows as approved even if the registry
  changes; version-1 plans (seven fixed parts) are read as they were, with
  their original hashes. No migration was needed.
  A revision names the approved plan it replaces (`revises`: its id and
  sha256, filled in by the host, not the agent) with a `revision_reason`;
  only a current plan (one not yet revised) can be revised, and if two
  revisions of one plan wait at once only the first approved is frozen.
  The card shows a plan against a `compare_to` (display only, never
  hashed): the plan it revises, or the version the person last sent back.
  Sending a plan back can ask for another type (`change_type`); the agent
  gets the person's draft, which may be unfinished, and what the new type
  needs. When a plan is proposed, the tool adds `proposed_after` from the
  access log (queries that returned data so far in the session, and their
  tables); it's part of the hashed content and can't be edited on the card.
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
- **Provenance** (`sessions/provenance.py`, `api/provenance.py`). The runtime
  keeps, beside that evidence, where each piece came from (a command by its
  event id, a query by its access-log id, shown in the Queries tab; in memory only, and not a
  review's), read through `SessionManager.turn_sources`. An after-turn hook
  matches the answer's numbers against it and the turn checkpoint's output
  data files (`tracing.trace_sources`: one sorted list of values, so it's
  linear in the evidence) and appends a `provenance` event: each number's
  sources (capped) and the output files the answer names. `GET
  /api/conversations/{id}/provenance/{path}` builds a file's chain on
  demand from the checkpoint summaries (reading checkpoints' files only
  back to the one that first saved the current content), that turn's
  events alone, and the access log. Neither returns command output or
  query rows.
- **Rigor review** (`sessions/rigor.py`). A per-conversation switch, on by
  default in Analysis mode. After each completed turn, DataLab runs
  app-server `review/start` (inline, custom instructions). Review mode starts
  without the conversation's history, so the instructions carry the
  question, the approved plans (whole: every valid plan fits, and anything
  longer is marked as cut), the queries run, and the answer, fenced and
  marked as data, plus the checks for the latest plan's type. It's skipped when a turn did no work and states no
  numbers. The review's text (the `exitedReviewMode` item) is shown under
  the answer, with a button to ask the agent to address it; the review can
  be stopped like a turn. (Codex 0.157 answers `review/start` with a
  different turn id than the one it runs, so the runtime takes the id from
  `turn/started`.)
- **Express** (`sessions/modes.py`, `sessions/tokens.py`). A
  per-conversation switch, stored with the conversation (migration 0014),
  never on together with the rigor review. It isn't a mode: DataLab puts a
  short note in front of each message asked with it on (and an "Express is
  off again" note in front of the first one after), so a switch applies from
  the next message without restarting the thread. The token refuses
  `propose_plan` and `ask_research_helper` for its turns; data access and
  checks don't change. Each question's event records the switches and the
  effort it began with, and the turn's review follows that record.
  WORKSPACE.md, "Express", has the detail.

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
    the server's `Retry-After`, up to three attempts within 60 seconds and
    at least a second apart, even when `Retry-After` is 0 or missing
    (`relay/recovery.py`). It never retries a used-up allowance, a refused
    key, a missing model or a bad request. Before every attempt, the first
    too, it checks the request's turn is still going: nothing more is sent
    after Stop (even once a new turn has begun), or once the session's token
    is revoked (the conversation was deleted, shut down or reaped, or a
    helper was cancelled). Codex's own request retries are kept to one, so a
    request makes at most two rounds of relay attempts (six upstream calls,
    about two minutes of waiting at most).
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
  SQL Playground results go to `<data_dir>/playground/<pg_id>/results/`
  instead, outside every conversation's folder, so no agent can see them;
  they leave only through an export (`POST /api/sql/results/{id}/export`).
- **Catalog** is built from the `generated/schema/` metadata for every cohort.
  It powers `search_catalog`, `describe_table`, and the SQL Playground
  browser. The real DataLab reads the folder `catalog_dir` names, else the
  lab knowledge base's `generated/schema` from its clone (GitHub's `main` as
  last synced, from git's objects, each file checked as the knowledge-base
  check checks it), else `<data_dir>/catalog`; and again after each sync,
  with no restart (`data/catalog_source.py`). With none, every query is
  refused, saying what's missing and how to fix it. The knowledge base's
  `main` is therefore a trust input for the SQL check, relying on its branch
  protection (SAFETY.md, "Every column must be a real column"). Practice DataLab, which
  names no catalog folder, builds its own in `<data_dir>/catalog` from the synthetic database's catalog views the
  first time it connects (metadata only; `data/autocatalog.py`). To rebuild
  it, delete `<data_dir>/catalog` and start DataLab again.
- **Audit log.** Every query appends one metadata-only row (see
  [SAFETY.md](SAFETY.md)). The same rows feed the Data accessed panel.
- **Agent tools (`/mcp`, `data/agent_tools.py`).** The official MCP Python
  SDK, with streamable HTTP. The `ihs-data` server exposes
  `search_catalog`, `describe_table`, `join_paths`, `find_concept`, `query`,
  `check_workflow`, `propose_plan`, `ask_research_helper`, and the opt-in
  `propose_sql` (SQL drafting only) and `suggest_kb_update`. The session
  token carries the mode's tool list (PRODUCT.md, "Modes and their policy")
  and decides which workspace results land in; any other tool is refused,
  and so are `propose_plan` and `ask_research_helper` on a turn asked with
  Express on. A failed call returns a failure category and code
  (`data/failures.py`), which the chat turns into its own words.
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
- **Making a workflow.** Save as workflow and Turn this into a workflow are
  drafted by DataLab's own code from the SQL (`workflows/drafts.py`), with
  no model. New workflow runs a Workflow authoring conversation and shows
  its newest draft as three editable stages (`workflows/stages.py`: edits
  are made from the workflow model, never by editing the YAML text); a test
  run on practice uses the same runner. See WORKFLOWS.md.

### 8. Repos and Save & share

- `ihs-knowledge` and `ihs-pipelines` are cloned into the data folder and
  kept in sync with the **git CLI**. The installer makes sure Git is present.
- Credentials come from the GitHub App's user token, which is kept in the
  keychain and passed to git through a credential helper. The token never goes
  on disk in plain text, and never enters a container. (Implemented in
  `repos/`: the device flow and refresh in `github.py`, the helper in
  `credential_helper.py`, git and the clones in `git.py`, keeping a clone
  in step with GitHub in `sync.py`; see KNOWLEDGE_BASE.md, "How it's
  built", and WORKFLOWS.md, "As built (Pipelines)". One `GitHubAuth` serves
  both repos, since each token refresh replaces the refresh token.)
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

  As built, for both repos, step 4 re-runs the checks (and, for pipelines,
  the tests when the rebased `ihsDataR` tree differs) and pushes if they pass,
  without showing the person the others' changes first.
- The participant-data scan covers workflow files too, since SQL literals,
  comments, and parameter defaults could contain IDs.
- If another person changed the same lines, a plain two-version screen appears
  instead.
- **A person's own edits** (Knowledge's Edit page, Pipelines' Edit
  manually) are drafts kept in SQLite on this computer (migrations 0012 and
  0013), checked as they're typed, and shared through the same Save & share,
  with a three-way view (theirs, GitHub's, where they started) when GitHub
  moved on.

### 9. Frontend (`frontend/`)

| Choice | Why |
|---|---|
| React 19, Vite, TypeScript, Tailwind v4 | Same as the prototype, and it worked well |
| React Router | One route per tab and per conversation, so links and the back button work |
| TanStack Query | Server state, caching, and retries without hand-written fetch plumbing |
| Generated API types (`openapi-typescript`) | The frontend can't drift from the backend |
| A small in-repo component set (`components/ui`) | Accessible primitives on the "paper" design tokens (docs/DESIGN.md) |
| CodeMirror 6 | One editor for SQL, R, YAML, and Markdown, with diff view |
| react-markdown and vega-embed | Chat rendering and inline charts, carried over from the prototype |

- Structure: `app/` (shell, routes), `api/` (typed clients and the generated
  `schema.d.ts`), `components/` (shared: `chat/`, `editor/`, `ui/`), `lib/`
  (including the bundled guide), and `features/<tab>/` (workspace, sql,
  workflows, pipelines, knowledge, settings, toolbar, help).
- **The chat component is built once** and used by every tab: the Workspace
  shows it in full, and the other tabs dock a compact version
  (`DockedChat`) in their own mode.
- **Help** is `docs/guide/*.md`, bundled at build time (`lib/guide.ts`):
  the Help tab, its search, and the glossary's tooltips.
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
- **Gateway and research proxy.** Upstream nginx and Squid images, pinned by
  digest in `sessions/containers.py`, with DataLab's config mounted
  read-only.
- **Probe image** (`images/probe`): a small image for CI's Safety check.
- The release workflow builds the agent image (reusing it when `images/agent`
  is unchanged) and publishes it to the public GitHub Container Registry.
  Each release's `images.json` lists every image by digest.
- The practice profile also runs **Oracle Database Free**, pinned by digest
  in `practice_db`.

### 11. Profiles and outside connectors (connectors not built)

- **Profiles.** `datalab` runs with a profile. **real** (the default) uses
  Oracle. **practice** uses the synthetic backend only. Each profile has its
  own data folder, port, and keychain namespace, and they never share state.
  Practice mode for new colleagues is simply the practice profile.
- **Connectors (planned, not built).** An MCP server (`/connect/mcp`) and a
  `datalab` CLI would let outside AI tools, such as Claude Code or a host-side Codex, drive DataLab
  through the same API as the UI. Their tools cover:
  - conversations: start, send, read events, stop;
  - SQL Playground queries;
  - workflows: run and read run records;
  - knowledge pages;
  - the Safety check and diagnostics.
- **Scopes (planned).** Connector tokens would be created in Settings and
  carry a scope, enforced by a single dependency on every API route. **In v1,
  connectors are for the practice profile only** (scope `full`). Real-profile access with a
  `metadata` scope is deferred until its exact fields and error handling are
  specified. Until they exist, outside agents that build and test DataLab
  drive the practice instance through its UI in a browser, and only the
  practice instance.
- **Practice really is synthetic-only.** In the practice profile:
  - you can't attach host files or folders; bundled synthetic fixtures are
    available instead;
  - export destinations are limited to a disposable practice exports folder;
  - Dropbox and the lab repos' write access are unavailable.
  So nothing real can find its way in.
- The connector MCP endpoint would be distinct from the agent-facing
  `/mcp`, which is reachable only through a session's gateway; the connector
  endpoint would be reachable only on the host loopback.

### 12. SQL Playground

- The person's own queries (`playground.py`, `api/sql.py`) go through
  `DataService.run_query` with `origin="playground"`: the same SQL check,
  caps and logging as the agent's. Results stay in
  `<data_dir>/playground/<pg_id>/results/`, outside every conversation.
- Its chat is a SQL drafting conversation. `propose_sql`
  (`data/sql_drafts.py`) checks the query with the editor's check and
  records a `sql_proposed` event; the editor offers the turn's latest one.
  A proposal is never run: the person runs it.

### 13. Updates and releases

- **Releases** (`.github/workflows/release.yml`) run CI, build the agent
  image, the package and `requirements.txt` (pinned by hash), gather the
  installers, write `images.json` and `SHA256SUMS`, and sign `SHA256SUMS`
  (Ed25519, `signing.py`; the private key is only in the release
  environment's secret).
- **Checking** (`releases.py`): the host asks GitHub's Releases API, without
  signing in, at start and about once an hour (`HourlyCheck`), and on
  **Check now**. Only a release whose files match the signed `SHA256SUMS`
  and GitHub's own checksums is offered, verified against the key pinned in
  `release_keys.py`.
- **Installing** (`updater.py`, `update_gate.py`, `updates.py`): nothing new
  may start while an update runs; the database is backed up; the new version
  is installed beside the current one; the launcher switches; DataLab
  restarts. An interrupted update is finished or undone at the next start.
  `datalab versions --use` and `datalab rollback` go back. See
  DISTRIBUTION.md.

### 14. Installers and the practice database

- `installer/macos/install.sh` and `installer/windows/install.ps1` install
  Docker Desktop if needed, `uv`, and DataLab into a per-version folder in
  the person's account, then call the shared steps: `datalab pull-images`,
  `datalab setup` (the lab's settings and the keys), and either GitHub
  sign-in and `datalab repos sync` (real) or `datalab practice-db setup`
  (practice). The lab's install page provides the command with the lab's
  settings; the manual path passes `--settings <file>`.
- **The practice database** (`practice_db/`): Oracle Database Free in a
  labelled container and volume on this computer, loaded once by DataLab's
  generator, started by practice DataLab when it opens, reset only when
  asked. A marker table only the synthetic database has is checked before
  every practice query. Practice builds its catalog from the database's
  catalog views the first time (`data/autocatalog.py`).

### 15. Support reports and diagnostics

- **Copy diagnostics** (`diagnostics.py`) and **Send feedback**
  (`support.py`, `api/support.py`) collect an allowlist of metadata only:
  versions, check results, recent operation ids and statuses, scrubbed
  error templates. A report is shown in full before it's saved or sent. See
  [SUPPORT.md](SUPPORT.md).

## Repository layout

```
ihs_datalab/
  README.md  PRODUCT.md  AGENTS.md  docs/ (guide/ is the Help)
  backend/     pyproject.toml, uv.lock, src/datalab/, tests/
  frontend/    package.json, package-lock.json, src/
  images/      agent/ (Dockerfile, AGENTS.md, skills/, bin/), probe/
  installer/   macos/ and windows/: install and uninstall scripts
  synthetic/   db.sh and the synthetic database's README (generator: backend/src/datalab/practice_db)
  evals/       the scientific evaluation set and its results
  kb/          the check workflow for the ihs-knowledge repo
  scripts/     release build and signing, parity, Spine conversion, CI helpers
  branding/    the marks and the icon build
  spikes/      throwaway proofs of concept kept as design evidence
  .github/     workflows/ci.yml, workflows/release.yml
```


## Testing

- **Unit:** backend (pytest), frontend (Vitest), plus the knowledge base and
  workflow checks.
- **Integration, in CI:** the real app against a synthetic Oracle Free
  container and real Docker (the `oracle` job). Codex itself is stood in for
  by `tests/fake_app_server.py`, which speaks the app-server protocol; tests
  at that boundary use the live notification shapes (for example
  `test_the_live_app_server_shape_of_a_failed_query_keeps_its_reason` in
  `tests/test_runtime.py`).
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
  must match (`scripts/parity/`, [acceptance/2026-09-27-parity.md](acceptance/2026-09-27-parity.md)).
  This is a one-off validation, not a feature.
- **Evals** (`evals/`): 19 representative research tasks on synthetic data,
  with mechanical graders (tested in CI). They are run by hand on the
  practice profile before releases and after any change to prompts, skills,
  the model, or the Codex version; results are kept in `evals/results/`.

## How it was built

v1 is one release, built through internal milestones and pre-releases.
Readiness against the definition of done is tracked in
[PRODUCT.md](../PRODUCT.md), "v1 readiness"; this is only the history.

0. **Spike** (2026-09-26, Mac): app-server with Codex 0.157.1, the nginx
   gateway, the DNS lockdown, elicitation approvals, resume, the Squid
   research proxy. It found the hosted-tool bypass (fixed by the relay) and
   that interrupt doesn't kill commands (fixed in the adapter).
   [spikes/2026-09-26-codex-gateway](../spikes/2026-09-26-codex-gateway/README.md).
1. **Foundations** (2026-09-26): repo, CI, lockfiles, the synthetic
   dataset, the data service and the `ihs-data` tools.
2. **First data session** (2026-09-26): the runtime, conversations API and
   web UI; the Safety check in the app and CI; research sessions.
3. **Complete workspace** (2026-09-26 to 27): inputs, outputs, checkpoints
   and rollback, exports, modes and skills, the research helper, plans,
   tracing and the rigor review. Demonstrated end to end
   ([acceptance](acceptance/2026-09-26-milestone-3.md)); acceptance is the
   lab's.
4. to 7. **SQL Playground, Knowledge, Workflows and Pipelines, Distribution**
   (2026-09-27 to 29): built side by side on the seams above, released as
   `v0.2.0-beta.1` to `v0.2.0-beta.8` and `v0.3.0-beta.1`. Since then on
   `main`: Express (#24), Knowledge suggestion rows (#25), live failed-query
   reasons (#26), and the hourly update check (#27).
8. **Finish**: the definition of done on fresh Mac and Windows machines,
   with colleagues (PRODUCT.md).
