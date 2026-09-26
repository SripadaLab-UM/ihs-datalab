-- Files and folders the user attached to a conversation. They are mounted
-- read-only into its container; nothing is copied.
CREATE TABLE attachments (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    host_path        TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('file', 'folder')),
    added_at         TEXT NOT NULL,
    UNIQUE (conversation_id, name)
);
