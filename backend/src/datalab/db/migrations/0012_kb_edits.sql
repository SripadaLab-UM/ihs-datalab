-- A person's own edits of knowledge-base pages (the Knowledge tab's Edit
-- page, or a suggested update accepted from a conversation). Kept here, on
-- this computer, until the person shares it with Save & share (a commit on
-- GitHub) or discards it. Nothing here is on GitHub.
CREATE TABLE kb_edits (
    id           TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    -- GitHub's main (as last synced) when the edit began: its version of
    -- the page is "where you started", and a newer one is a conflict to see.
    base         TEXT NOT NULL,
    -- Whether the page is new (not in `base`).
    new_page     INTEGER NOT NULL DEFAULT 0,
    text         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
        'draft', 'saving', 'saved', 'conflict', 'check_failed', 'failed', 'discarded'
    )),
    -- Where it came from: {} for the Edit page; for a suggested update, the
    -- conversation, the suggestion, and the queries behind it.
    origin_json  TEXT NOT NULL DEFAULT '{}',
    -- How the last Save & share went (as for kb_proposals).
    result_json  TEXT NOT NULL DEFAULT '{}',
    commit_sha   TEXT,
    decided_by   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
-- One edit of a page at a time: opening the page again finds it.
CREATE UNIQUE INDEX kb_edits_open ON kb_edits (path) WHERE status NOT IN ('saved', 'discarded');
