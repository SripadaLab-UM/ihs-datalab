# DataLab safety promises

Status: **implemented** for v1, except the connectors for outside AI tools
(the outside-tool MCP server, and the connector tokens and per-route scopes
described under "Outside AI connectors"), which aren't built yet. The agent's
own tool server (`/mcp`, used by Codex inside DataLab) is built. Implemented isn't accepted:
acceptance is tracked in [ARCHITECTURE.md](ARCHITECTURE.md).

DataLab lets you use an AI agent (OpenAI Codex on U-M GPT) with sensitive IHS
data. This page says what DataLab promises, how it keeps each promise, and how
you can check it.

## Two kinds of sessions

Every conversation is one of two kinds. You choose when you start it, and it
can't change afterwards.

| | 🔒 **Data session** (default) | 🌐 **Research session** |
|---|---|---|
| Oracle database | Yes, read-only | No |
| Attached files | Yes, read-only | Yes, with a warning that they may reach the internet |
| Internet | **No.** U-M GPT only | Yes: web search, papers, package installs |
| Use it for | Analysis, extraction, workflows | Literature, methods, trying new tools |

Files can move **from** a research session **into** a data session, for
example code, notes, or papers. Nothing moves the other way. The only way data
leaves DataLab is when you export it.

**Research helper.** A data-session agent can ask a temporary research helper
to look something up, such as package docs or a paper. You see the exact
question first, and you can edit, approve, or decline it. The helper gets only
that question. It has no conversation history, no files, and no database. It
returns an answer and is then deleted.

## The promises

1. **The AI can't touch anything else on your computer.**
   The AI works in a sealed box. It sees only its own workspace and the files
   or folders you attach, and it can't change the files you attach.

2. **The AI can't change or damage the research database.**
   The AI reaches Oracle only through DataLab, using a read-only account. It
   never sees the database password. DataLab also limits how heavy the AI's
   queries can be.

3. **Sensitive data only goes to approved places.**
   A data session can talk to U-M GPT and DataLab, and nothing else. A
   research session has the internet but no access to the database. A
   question from a data session to the research helper goes out only after
   you approve it.

4. **Your work isn't lost.**
   Conversations, results, and workflow runs are saved and survive restarts.
   Every agent turn is checkpointed, so a mistake can be rolled back. Nothing
   is deleted unless you delete it.

5. **Results leave only when you export them.**
   The AI can prepare files, but only you can export them, to a folder you
   chose such as a local folder or your Dropbox folder. The one exception is a
   workflow you've approved: when it runs, it writes to the destination you
   configured for it.

6. **The shared knowledge base never receives data without your review.**
   The knowledge base is shared with the whole lab through GitHub. Agents can
   suggest edits, but you see the exact change before it's shared. It must
   never contain participant-level data.

7. **You can see everything the AI accessed.**
   Each conversation has a **Data accessed** panel. It lists every query the
   AI ran: which tables, when, how many rows, and where the result went.

8. **You can check the first three.**
   The **Safety check** in Settings & Safety tests promises 1 to 3 live when
   you press **Run safety check** (or run `datalab safety-check`), and shows
   the results. It doesn't run by itself. The other promises are covered by
   the rules below and DataLab's automated tests, not by this screen.

## Outside AI tools

Other AI tools, such as Claude, are used to try and test DataLab. They are
**not** approved for study data:

- The **practice** DataLab has only synthetic data, and you can't attach
  your own files to it. Outside tools may be used with it.
- Outside AI tools must not be used with a **real-data** DataLab. Nothing
  technical stops a tool that controls your terminal or browser, so this is a
  rule for people. Connectors, once built, will refuse the real profile.

## What DataLab does *not* promise

- **The AI sees the data you give it.** That is the point, and U-M GPT is the
  approved destination for that data.
- **Exported files are yours to look after.** Once they're in your own folder,
  DataLab no longer controls them.
- **Anything you attach to a research session may reach the internet.**
  The same goes for anything you approve in a research-helper question, so
  read it before you approve.
- **The AI can be wrong.** Check its analysis as you would a colleague's.
- **It can't protect a compromised computer.** If your computer has malware,
  or Docker Desktop has a security flaw, these promises can't hold. Keep your
  OS and Docker Desktop updated.

---

## How each promise is enforced (for maintainers)

Design rule: **fail closed.** If something is misconfigured, the agent loses
access. It never gains it.

### Host isolation

- Each conversation runs in its own Docker container. The container runs as a
  non-root user, drops all Linux capabilities, and has CPU and memory limits.
- Mounts are exactly the conversation's workspace plus any attached items,
  **read-only**. Never mounted: the home folder, the Docker socket, credential
  stores, or another conversation's workspace.
- **Only a person can attach, and only by choosing in the computer's own file
  picker.** The browser can ask DataLab to open the picker, but it can't name
  a path, so neither a web page nor the agent can attach anything. Attached
  items are mounted, not copied, and never written to.
- Some places are never attachable: a whole home folder or drive, system
  folders, private app data (`~/.ssh`, `~/Library`, `AppData`, and so on,
  except cloud-synced folders such as Dropbox), and DataLab's own data folder,
  which would expose other conversations.
- **An attached folder is shared whole.** The agent can read every file in it,
  including files added later (read-only stops changes, not reading).
  Credentials files are refused when attached on their own; inside a folder
  they can't be, so DataLab lists any it finds when the folder is attached,
  and the person can remove the folder and attach only what's needed.
- Adding or removing an attachment restarts the conversation's container on
  its next message, because mounts are fixed when a container starts. The
  agent is told what changed.
- Model-written code runs only inside containers, never in DataLab's host
  process.
- **The host runs only DataLab's own code.** Scripts from the lab repos, such
  as pipeline tests, custom QC checks, and workflow R steps, run in a
  container with no network. The knowledge-base and workflow checks are part
  of DataLab itself, not files taken from a repo.
- **The browser is part of the boundary.**
  - An app-wide Content Security Policy blocks every network request that
    isn't to DataLab itself. An image link or chart in an agent's answer
    can't send data anywhere.
  - Agent-made HTML reports are previewed in a sandboxed frame with **scripts
    turned off** and a policy that allows no network requests. With scripts
    on, a page could send data out simply by navigating itself to another
    site, and no browser policy can stop that.
  - Previewed pages are cleaned first: resource hints (`dns-prefetch`,
    `preconnect`), `<meta>` and `<base>` tags, scripts, and frames are
    removed, since a DNS lookup of a crafted name could carry data out. DNS
    prefetching is also switched off for the whole app. Addresses outside
    the page's own folder are dropped as well, as a second layer behind the
    policy. Previews are served only into the viewer's frame, never as a
    page of their own.
  - Links in the agent's answers open only after the person has seen the
    full address (where look-alike letters show as `xn--`) and confirmed.
    Links to this computer or the local network, or with a user name or
    password in them, aren't opened at all.
    Charts must embed their data; a chart that points at a link isn't drawn.
  - Workspace files shown in the viewer come from the latest checkpoint,
    never the live folder, so the agent can't swap a file for a link to
    somewhere else on the computer while DataLab reads it. They carry the
    same inert policy, so opening one directly in a tab can't run anything.
  - Exported conversation reports carry the same no-network policy, as the
    first thing in the page, and their text is cleaned the same way
    (links become plain text).
- DataLab listens only on `127.0.0.1`, and its API requires the session
  cookie the launcher sets.

### Database safety

- Only DataLab's host process holds the Oracle password, which it reads from
  the OS keychain. The password never appears in a container, in files, or in
  logs.
- **Every connection enables only the read-only roles.** DataLab runs
  `SET ROLE <read-only roles>` before anything else.
  - Why: the shared service account `SVC_IHS_AGENT` holds a role
    (`IHS_2026_ROLE`) that grants UPDATE, DELETE, and ALTER on the live 2026
    tables, plus CREATE privileges. This was checked on 2026-09-26.
  - Effect: with the write role switched off, the session's only system
    privilege is `CREATE SESSION`, and every cohort remains readable, because
    older cohorts are granted to the account directly.
  - Limit: read-only transactions alone would not be enough, since they don't
    block DDL.
  - Follow-up: the DBA is asked to remove the write role from the account, as
    a second layer (see the PRODUCT to-dos).
- Each query runs in a `READ ONLY` transaction. Only `SELECT`/`WITH`
  statements are accepted; the SQL is parsed, not pattern-matched.
- **Functions: Oracle's built-ins only** (`sqlcheck.ORACLE_FUNCTIONS`).
  - Refused: any other function name, since a function defined in the
    database, perhaps by a more privileged account, could do more than read.
    Package calls (`UTL_HTTP.REQUEST`, `DBMS_*`) are refused too.
  - Also refused, although they are built-ins: the ones that can make the
    database server fetch URLs or files, or describe the server and its
    network (XML functions, URI types, `BFILENAME`, `SYS_CONTEXT`, `USERENV`).
  - Why a built-in's name is safe: an unqualified built-in name always runs
    the built-in. `tests/test_sqlcheck_oracle.py` tries to take over every
    allowed name on the synthetic database, both with a same-named function
    in the session's own schema and with a public synonym. Every one still
    runs the built-in, and a control name that isn't a built-in is taken
    over, as expected. A name can't be added without a sample call that this
    test runs.
  - The synthetic database runs Oracle 23ai; production runs 19c (19.32).
    Every allowed name is a built-in in 19c (checked against the 19c SQL
    Language Reference), so none is open to takeover in production for
    lack of a built-in. Built-ins added after 19c stay off the list.
  - Calls are checked by the name as written, not as the parser reads it
    (it maps some names, such as IFNULL, onto its own functions). A quoted
    name must match exactly: Oracle treats `"nvl"` as a different object
    from `NVL`.
- **Every column must be a real column** of a table in its scope, checked
  against the catalog. Oracle runs a bare name that isn't a column, such as
  `ORA_DATABASE_NAME` or `DBMS_UTILITY.PORT_STRING`, as a function call with
  no arguments. With no catalog, no query runs.
  - **The catalog is a trust input.** Without a `catalog_dir` setting, the
    real DataLab reads it from the lab knowledge base's `generated/schema` on
    GitHub's `main`, as last synced: a column name there is what lets that
    bare name through. So it relies on `main`'s protection: only lab members
    with write access push; the repo's ruleset refuses force-pushes and
    deleting `main`; DataLab's Save & share runs the knowledge-base check
    before every push, and GitHub Actions runs it after every push. And
    DataLab checks each file as that check does (catalog fields only, names
    matching paths, no links or submodules), leaves out column names over
    128 bytes or with quotes or control characters, leaves out columns named
    as Oracle's no-argument built-ins (`USER`, `UID`, `SYSDATE`,
    `ORA_INVOKING_USER`, `ORA_DATABASE_NAME`, …), and doesn't read a
    `generated/schema` over 20,000 files or 64 MB (`data/catalog_source.py`).
  - Allowed without a catalog column: Oracle's pseudo-columns (`ROWNUM`,
    `LEVEL`, `USER`, `SYSDATE`, …). All are reserved words, and the Oracle
    test checks none of them can be taken over.
  - Refused because the column check can't follow them: PIVOT and UNPIVOT
    (use conditional aggregation), and a select-list alias in HAVING (Oracle
    before 23ai would look for a function of that name).
- **Guardrails protect the database and the laptop:**
  - an end-to-end deadline that really cancels the query in Oracle;
  - caps on rows and bytes per extraction, which only the user, not the
    agent, can raise;
  - a disk-space check;
  - limits on how many queries and containers run at once.
- Research-session containers get no data-service route and no data mounts.

### Network and credentials

- **The real U-M GPT key never leaves DataLab's host process.** No container
  and no file holds it.
  - Each session's Codex gets a **session token** instead.
  - All model requests go to DataLab's **model relay**, which checks the
    token, validates the request, and only then adds the real key.
- **The relay allows only the request shapes Codex needs.** It refuses:
  - hosted tools (web search, remote MCP servers, code interpreter);
  - URLs the provider would fetch (image or file links);
  - stored or background responses;
  - other endpoints.

  Research sessions are additionally allowed hosted web search. This matters
  because the U-M endpoint *does* run hosted tools if asked, and code in a
  container could ask directly, bypassing Codex's own settings. The spike
  demonstrated this.
- **Only approved models.** The U-M endpoint also serves other companies'
  models, which aren't approved for study data. The relay refuses any
  request for a model that isn't an OpenAI GPT or o-series text model (a
  lab's settings can narrow that to a list, never widen it), and lists only
  approved models to Codex. New conversations can only pick approved models.
- **The relay forwards exactly what it checked.** It re-serializes each
  request, and refuses one with duplicate keys, since parsers disagree about
  which copy wins.
- **Agent containers sit on an internal Docker network with no route out.**
  Their only exit is a small **gateway** that holds no secrets:
  - **data sessions:** the gateway forwards only to the relay and to DataLab's
    agent tools;
  - **research sessions:** the gateway also has a forward proxy to the general
    internet, which refuses the host, private networks, and DataLab's tools.
- **DNS.** Containers can resolve only `gateway`. Queries for other names
  never leave the machine; this was verified with a packet capture.
- **Codex settings.** Telemetry, update checks, and history are off. Every
  feature that opens a new channel is switched off explicitly, since most are
  on by default:
  - memories;
  - plugins and apps;
  - browser and computer use;
  - image generation;
  - multi-agent;
  - realtime.

  Hosted web search is off in data sessions too. Codex memories in particular
  would store summaries of past sessions where they could reach other
  sessions.
- **Codex home folders.** Each session gets its own. It contains
  conversation content, so it is treated as study data and never shared
  across sessions.
- **Pinned Codex version.** It is fixed in the agent image and upgraded
  deliberately.
- **No internet package installs.** Data sessions can't install packages from
  the internet, so the agent image ships with a curated R/Python toolkit.

### Research helper

- Data sessions get an `ask_research_helper` tool. Calling it pauses the agent
  and shows the user an approval card with the exact question text, which the
  user can edit.
- Nothing is sent until the user approves. Declining returns a "declined"
  result to the agent.
- **The approval is kept on the host.** The question and the person's
  decision are recorded in DataLab itself; the approval card comes from that
  record, and the helper uses only the text stored there. Codex is told the
  outcome so it can carry on, but nothing the agent's side says counts as
  approval: code in the container holds the session token and could answer
  an approval request itself.
- The question must be plain text the person can see: hidden characters
  (zero-width, direction marks, tag characters) are refused, padding is
  collapsed, and it is at most 1,000 characters, shown in full.
- The helper's answer is labelled as internet content that must never be
  followed as instructions, and capped at 30 KB. At most two helpers run at
  once, one per conversation, and each is cleaned up even if its call is
  cancelled.
- When approved, DataLab starts a temporary research-session container. It gets
  only the approved text: no history, no files, no data-service route.
- The helper returns text and, optionally, files, which count as research →
  data transfers. The container is then destroyed.
- There is no auto-approval in v1. Every question gets a human decision, and
  every decision is logged in the conversation.

### Data access log

- The data service records every query in the conversation's **Data
  accessed** panel and in a local append-only audit log. Each entry has the
  time, session, tables and schemas referenced, row count, a SQL fingerprint,
  and the result's file location.
- The log is metadata only. It never contains result values.
- Workflow runs log to the same audit log, attributed to the run.

### Shared knowledge base

- The knowledge base is a git repository synced with GitHub. The synced
  clone stays on the host, and **no container sees it**.
- Each conversation gets its own **editable copy** at `/work/kb`, in both
  session types. It is made **once**, from the synced clone, before the
  conversation's first turn, and DataLab records which commit it came from.
  It is not remade when the container restarts (after being idle, say), so
  the agent's edits are kept until a person saves or discards them. The
  agent edits pages there like any other file in `/work`.
- After each turn, DataLab compares the copy in that turn's checkpoint (never
  the live folder) with the version it was copied from. Any difference
  becomes a proposed-edit card with the exact diff. Only a person saves and
  pushes.
- An edit in one session's copy changes nothing outside that session: other
  sessions, and the shared repo, only see it once it is saved.
- Git operations run in DataLab's host process with the user's GitHub token,
  which is stored in the OS keychain. The token never enters a container.
  Git gets it from DataLab's credential helper, named on each command (never
  in a git config file), which switches off the person's own helpers so none
  of them stores it. The Safety check looks for the token in both session
  types' containers (environment, command, mounts, files) and, in plain
  text, in the repo clones.
- Before sharing, DataLab scans the diff for things that look like row-level
  data, such as participant IDs, per-person dates, or pasted tables, and warns
  the user. The scan is an aid; the human review is the control.
- Because research sessions read the knowledge base too, an edit that came
  from a data session reaches them only after a person has reviewed and saved
  it.

### Outside AI connectors

- **Not built yet (the design):** DataLab exposes its features to outside
  tools through an MCP server and a `datalab` CLI. Both use the same API as
  the UI, and every request carries a connector token whose scope the host
  app enforces on every route.
- **v1: practice profile only.** The practice profile's limits below are
  built; the connectors that would rely on them aren't yet.
  - The practice profile has its own data folder and uses only the synthetic
    backend.
  - It can't attach host files. It can't use real export destinations, only a
    disposable practice folder.
  - It has no write access to the lab repos.

  A practice instance therefore can't hold real data, even by accident.
- **The practice profile checks it's really talking to the synthetic
  database.** Every connection looks for a marker table that exists only
  there. Without it, no query runs, even if something else answers on the
  practice port, such as an SSH tunnel to the real server.
- **The synthetic database is reachable only from this computer.** Its port is
  bound to `127.0.0.1`, since its dev passwords are public. Its setup scripts
  refuse to run against anything that isn't the local Oracle Database Free
  container.
- **Not built yet: real-profile connectors are deferred** until the exact permitted fields,
  operations, and error handling are specified. Even "metadata" can leak
  through error messages or SQL in workflow files.

### Work preservation and storage

- Conversations, run history, and settings live in one local database in
  DataLab's data folder.
- After each turn, the workspace is checkpointed to a host-side store the
  agent can't write to. The container is paused while this happens, and
  links in the workspace are never followed, so the agent can't trick
  DataLab into copying a file from elsewhere on the computer. Links are kept
  as links.
- A rollback never deletes anything the checkpoint taken just before it
  couldn't save (a very large file, a pipe): it's left as it is, and the
  chat says so.
- **Rollback restores workspace files** to how they were after a chosen turn.
  The conversation isn't rewound; the agent is told the files were restored.
  Very large files aren't checkpointed, and the rollback screen lists them.
- Before an app update changes the database, DataLab **backs it up**. Rolling
  back to the previous version restores that backup.
- DataLab writes only to:
  - **its data folder**, which is internal (see
    [DISTRIBUTION.md](DISTRIBUTION.md) for the layout);
  - **export destinations** that the user configures, by choosing a folder
    in the computer's own picker (the same places are off limits as for
    attaching). Files are copied from the checkpoint the person was shown,
    never the live workspace, and nothing already in the folder is
    overwritten.
  - **Export folders inside a sync app's folder** (Dropbox, OneDrive, Box,
    Google Drive, iCloud Drive) are ordinary folders on this computer:
    DataLab has no Dropbox API access or key, and uses the folder the app
    keeps in sync. DataLab finds those folders by name in the home folder
    and `~/Library/CloudStorage`, without listing what's in them, only to
    open the picker there; the browser can't name a path. A chosen link
    that leads out of the sync folder it seems to be in is refused, as is a
    saved folder later replaced by a link. **Test folder** writes a
    synthetic `datalab-test-<time>.txt` (no study data), reads it back and
    removes it. Nothing says a file was synced or uploaded: exports and
    deliveries say "Saved to *name* (on this computer)", and for a sync
    folder, that the app will upload it when it's running and signed in,
    which DataLab can't confirm. Removing a folder only forgets it.
    Writes can't be redirected after the check: the checked folder is
    opened (`O_DIRECTORY | O_NOFOLLOW`), must be the same folder (device and
    inode) the check saw, and every folder, file and removal is made
    relative to that open folder (`dir_fd`), never by path; a subfolder that
    is a link is refused. (Windows, which lacks `dir_fd`, re-checks the
    folder's identity before each step.) DataLab's own data folder is
    recognised by what's on disk as well as by name, so another spelling
    (case, composed or decomposed accents, a link) doesn't get past. If a
    checked folder is moved away (with its parent, say) while an export is
    being written, the rest of the export still goes into that same folder,
    the one the person chose, wherever it now is. On Windows, a junction or
    other reparse point counts as a link and is refused, though `lstat`
    reports it as a plain folder.
  - **Exported files can't act on the computer by themselves.** The agent's
    files go under `files/`, apart from DataLab's report and manifest. Types
    that run code or open something when double-clicked (`.bat`, `.lnk`,
    `.hta`, `.command`, …) get `.txt` added. Web pages are exported as inert
    copies (cleaned, their own images embedded, and a policy that blocks
    every request), unless the person ticks "keep web pages exactly as the
    agent made them". An SVG keeps its name only if every element,
    attribute, and bit of CSS in it is on a short list of static drawing
    features (shapes, text, gradients, filters, links within the file, and
    embedded PNG/JPEG/GIF/WebP images); anything else, or anything that
    doesn't parse, gets `.txt` added. It's an allowlist because an SVG can
    spell a request in too many ways (animation, CSS escapes, presentation
    attributes) for a list of bad patterns to keep up.
    Every exported file is marked as downloaded (Mark of the Web on Windows,
    the quarantine flag on a Mac), so the computer treats it with the same
    caution as a file from the internet.
  - Every export is recorded in the audit log: when, where to, how many
    files, and whether it may contain study data. No contents. Each export
    goes into its own dated subfolder with a manifest recording what it is,
    when it was
    made, and which session or workflow produced it.
  - **Export folder names carry no study identifiers.** A folder is named
    for the date, the conversation's title, and the conversation's ID. The
    model that writes titles is told to leave out participant IDs, names,
    email addresses and dates, and every title (the model's, or the
    question's first words when there's no model) is scrubbed before it's
    stored: long digit runs, ID-shaped words such as `P-0001` or
    `IHS2025_00123`, phone-like digit groups, numbers labelled as someone's
    ("participant 0001", "#1234"), email addresses and dates are removed,
    and a title with nothing meaningful left stays "New conversation". The folder
    name, the manifest and the report's page title are scrubbed again at
    export, so a title typed by the person can't name a participant there
    either. Names can't be recognised reliably; that part rests on the
    model's instructions. Every title, typed or written, is one line with
    no control characters or bidi overrides, so it can't disguise itself.
- Nothing is deleted automatically. A Storage view in Settings shows disk
  use and offers one-click cleanup of old conversations and runs.
- PHI is permitted on these machines, which are PHI-approved. Cleanup exists
  for tidiness, not as a safety control.

### Safety check

The Safety check runs at startup and on demand. It never attempts to write to
Oracle.

| Check | How it's tested |
|---|---|
| Data session can't reach the internet | A request from inside a container to an outside host fails, and an outside DNS name doesn't resolve. In CI, every network interface is watched while the check runs (`scripts/dns-leak-test.sh`): its one-off lookup name must never appear on the wire, since a lookup that fails can still carry data out in the name. A control lookup from the computer itself must appear, so a capture that saw nothing can't pass |
| Hosted tools are refused | A web-search, remote-MCP, or image-URL request sent straight to the relay from a data container is rejected |
| U-M GPT is reachable | The model list loads from inside a container |
| No key in containers | Container environment and config have no U-M GPT key or Oracle password |
| No unexpected mounts | `docker inspect` mounts match the expected set |
| Container is locked down | Non-root, capabilities dropped, not privileged, no Docker socket |
| Research session can't reach data | The data service is unreachable from a research container |
| Helper gets only the approved text | A helper container's mounts and inputs contain only the approved question |
| Database access is read-only | After connecting, the session's enabled roles are exactly the read-only set, its system privileges are exactly `CREATE SESSION`, and it has no non-SELECT privileges on any IHS schema |
| Right agent image | The image digest matches the pinned release |
| Agent HTML can't phone home | A test page that tries every way a page can send data out (scripts, outside images and styles, refresh, prefetch, `<base>`, forms, frames, links and pings, SVG animation, event handlers) is put through the real preview route: nothing active or pointing outside its folder is left, the policy is sandboxed with no allowances and allows only the page's own folder, and the page won't open outside the viewer's frame |
