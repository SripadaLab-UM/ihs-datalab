-- Approved analysis plans, frozen: never updated, only added.
CREATE TABLE plans (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    approved_at      TEXT NOT NULL,
    content_json     TEXT NOT NULL,
    sha256           TEXT NOT NULL
);

-- The Rigor review switch, per conversation (on by default in Analysis mode).
ALTER TABLE conversations ADD COLUMN rigor_review INTEGER NOT NULL DEFAULT 0;
