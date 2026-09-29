-- A person's own changes to the pipelines repo (the Pipelines tab's Edit
-- manually, New file, and Edit before accepting on an assistant's proposal).
-- Kept here, on this computer, until the person shares it with Save & share
-- (a commit on GitHub, after the same check and tests as a proposal) or
-- discards it. Nothing here is on GitHub.
CREATE TABLE pipeline_edits (
    id           TEXT PRIMARY KEY,
    -- GitHub's main (as last synced) when the edit began, or the proposal's
    -- base when it began from an assistant's proposal: "where you started".
    base         TEXT NOT NULL,
    -- The files as the person has them: {path: {"text": text or null (deleted), "new": bool}}.
    -- One open edit per file: DataLab looks for it before starting another.
    files_json   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
        'draft', 'saving', 'saved', 'conflict', 'check_failed', 'tests_failed',
        'failed', 'discarded'
    )),
    -- Where it came from: {} for Edit manually; for Edit before accepting,
    -- the proposal and its conversation.
    origin_json  TEXT NOT NULL DEFAULT '{}',
    -- The base with the person's files (never pushed), and its tree: what the
    -- tests run on. Made again each time the draft is kept.
    edit_commit  TEXT NOT NULL,
    tree         TEXT NOT NULL,
    -- How the last Save & share went (as for pipeline_proposals).
    result_json  TEXT NOT NULL DEFAULT '{}',
    commit_sha   TEXT,
    decided_by   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX pipeline_edits_by_status ON pipeline_edits (status, updated_at);
