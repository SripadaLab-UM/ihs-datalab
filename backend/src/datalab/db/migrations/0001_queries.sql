-- Every query the data service runs, for the "Data accessed" panel.
-- This is local conversation data: it includes the SQL text and bind values,
-- which may contain participant identifiers. The separate audit log
-- (logs/audit.jsonl) holds metadata only.
CREATE TABLE queries (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rejected', 'cancelled')),
    sql_text        TEXT NOT NULL,
    binds_json      TEXT NOT NULL DEFAULT '{}',
    tables_json     TEXT NOT NULL DEFAULT '[]',
    row_count       INTEGER,
    bytes_written   INTEGER,
    elapsed_ms      INTEGER,
    result_path     TEXT,
    message         TEXT
);

CREATE INDEX queries_by_session ON queries (session_id, started_at);
