-- Processed study data (the lab's Dropbox folder) as a second data source.

-- Which sources a conversation uses: a JSON list of "database" and
-- "dropbox". Every conversation before this used the database only.
ALTER TABLE conversations ADD COLUMN data_sources TEXT NOT NULL DEFAULT '["database"]';

-- The folder this person chose, and how far the file list has got. One row.
-- `folder_id` is Dropbox's id for it (it survives renames); `cursor` is
-- Dropbox's, for asking only what changed since the last refresh.
CREATE TABLE processed_source (
    one          INTEGER PRIMARY KEY CHECK (one = 1),
    folder_id    TEXT NOT NULL,
    folder_path  TEXT NOT NULL,
    root_ns      TEXT,
    cursor       TEXT,
    indexed_at   TEXT,
    chosen_at    TEXT NOT NULL
);

-- The file list: metadata only, never contents. Paths are relative to the
-- chosen folder; `path_key` is the lower-cased one (Dropbox paths ignore case).
CREATE TABLE processed_files (
    path_key         TEXT PRIMARY KEY,
    path             TEXT NOT NULL,
    file_id          TEXT NOT NULL,
    size             INTEGER NOT NULL,
    modified         TEXT,
    content_hash     TEXT
);

-- Every file DataLab fetched for a conversation's agent: its Data accessed
-- entries for files, beside the `queries` table. Local conversation data:
-- the path may name a participant. The audit log has metadata only.
CREATE TABLE file_loads (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'refused')),
    path          TEXT NOT NULL,
    size          INTEGER,
    content_hash  TEXT,
    elapsed_ms    INTEGER,
    message       TEXT
);

CREATE INDEX file_loads_by_session ON file_loads (session_id, started_at);
