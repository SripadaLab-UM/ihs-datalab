-- Workflow runs (milestone 6). The run folder (runs/<id>/) holds the files:
-- the kept extracts, every step's outputs, result.json and log, and
-- record.json, a copy of everything below, so a run folder explains itself.
-- These tables are what the Workflows tab lists, and what Run again, Replay
-- and delivery read.
--
-- Local study data, like `queries`: SQL text and bind values may contain
-- participant identifiers. The audit log keeps metadata only.

CREATE TABLE workflow_runs (
    id                 TEXT PRIMARY KEY,        -- run_20260927T110059_4c7e20, as queries.origin = 'run' expects
    workflow_name      TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('run', 'run_again', 'replay')),
    of_run             TEXT REFERENCES workflow_runs (id) ON DELETE SET NULL,  -- run_again, replay
    set_id             TEXT,                    -- "Run a set": runs started together
    status             TEXT NOT NULL CHECK (status IN
                           ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    started_by         TEXT NOT NULL,           -- the person's git name and email
    -- The definition: the file itself, so a replay needs no repo, and where it came from.
    workflow_path      TEXT NOT NULL,           -- relative to the workflows folder
    workflow_source    TEXT NOT NULL CHECK (workflow_source IN ('git', 'file')),
    repo_commit        TEXT,                    -- the commit, when the file is committed as it is
    workflow_blob      TEXT NOT NULL,           -- git blob id, or sha256:<hex> of the file when not in git
    workflow_text      TEXT NOT NULL,
    pipelines_json     TEXT NOT NULL DEFAULT '[]',  -- [{name, package, tree_sha256, library_sha256}]
    -- The environment.
    image_ref          TEXT NOT NULL,           -- as configured: a tag or ref@sha256:…
    image_digest       TEXT NOT NULL,           -- the image's own id (sha256:…); steps run by it
    image_platform     TEXT NOT NULL,           -- linux/arm64 or linux/amd64
    host_platform      TEXT NOT NULL,           -- what Docker runs on here
    r_packages_sha256  TEXT NOT NULL,           -- fingerprint of installed.packages()
    runner_version     TEXT NOT NULL,           -- DataLab version and step wrapper sha256
    runtime_json       TEXT NOT NULL,           -- TZ, locale, thread counts, RNGkind
    -- What it was asked to do.
    params_json        TEXT NOT NULL,
    seed               INTEGER NOT NULL,        -- step seeds derive from this and the step id
    reads_json         TEXT NOT NULL,           -- the declared Oracle objects
    run_dir            TEXT NOT NULL,           -- relative to the data folder
    inputs_kept        INTEGER NOT NULL DEFAULT 1,  -- 0 once Storage cleanup removed them
    -- Replay only: whether every pin could be met before it started (and why
    -- not), and afterwards whether every output matched the original's.
    replay_exact       INTEGER,
    replay_notes_json  TEXT NOT NULL DEFAULT '[]',
    reproduced         INTEGER,
    -- Delivery: none (the workflow has none), pending, delivered, skipped, failed.
    delivery_status    TEXT NOT NULL DEFAULT 'none' CHECK (delivery_status IN
                           ('none', 'pending', 'delivered', 'skipped', 'failed')),
    delivery_message   TEXT,
    message            TEXT
);

CREATE INDEX workflow_runs_by_workflow ON workflow_runs (workflow_path, started_at);
CREATE INDEX workflow_runs_by_set ON workflow_runs (set_id);

CREATE TABLE workflow_run_steps (
    run_id        TEXT NOT NULL REFERENCES workflow_runs (id) ON DELETE CASCADE,
    step_id       TEXT NOT NULL,
    position      INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('sql', 'r', 'pipeline', 'qc_builtin', 'qc_custom')),
    status        TEXT NOT NULL CHECK (status IN
                      ('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),
    started_at    TEXT,
    finished_at   TEXT,
    seed          INTEGER,
    -- SQL steps (and a pipeline's extracts). The data service's own rows
    -- (queries, session_id = the run id) have the same; they're copied here
    -- because Storage cleanup may remove them.
    query_id      TEXT,
    sql_text      TEXT,
    binds_json    TEXT,
    queries_json  TEXT NOT NULL DEFAULT '[]',  -- a pipeline's extracts: [{object, query_id, sql, binds}]
    -- Container steps.
    exit_code     INTEGER,
    elapsed_ms    INTEGER,
    inputs_json   TEXT NOT NULL DEFAULT '{}',  -- {name: {step, file, sha256}}
    outputs_json  TEXT NOT NULL DEFAULT '{}',  -- {name: {file, sha256, bytes, rows, columns}}
    result_json   TEXT,                        -- result.json, or the built-in checks
    message       TEXT,
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE workflow_run_deliveries (
    id                TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs (id) ON DELETE CASCADE,
    destination_key   TEXT NOT NULL,           -- as the workflow names it
    destination_id    TEXT REFERENCES export_destinations (id) ON DELETE SET NULL,
    destination_path  TEXT NOT NULL,           -- the folder it was then
    folder            TEXT NOT NULL,           -- the dated folder export() made
    files_json        TEXT NOT NULL,           -- [{path, bytes, sha256}], as in the manifest
    manifest_sha256   TEXT NOT NULL,
    delivered_at      TEXT NOT NULL
);

CREATE INDEX workflow_run_deliveries_by_run ON workflow_run_deliveries (run_id);

-- A workflow file names its destination (`destination: dropbox-ihs-2025`);
-- each computer maps that key to a folder the person chose. Destination names
-- are labels and needn't be unique, and ids are random per computer, so the
-- mapping gets its own column.
ALTER TABLE export_destinations ADD COLUMN key TEXT;
CREATE UNIQUE INDEX export_destinations_by_key ON export_destinations (key) WHERE key IS NOT NULL;
