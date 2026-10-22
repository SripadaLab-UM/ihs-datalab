-- People and their sign-ins, for the lab's shared DataLab (deployment =
-- "server"; auth/). A person's computer has one: the local user, id 1.

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    -- GitHub's numeric id (never reused, unlike a login); NULL for the local user.
    github_id INTEGER UNIQUE,
    login TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    -- Set by an administrator: their sign-ins stop working.
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_seen_at TEXT
);

INSERT INTO users (id, login, is_admin, created_at)
VALUES (1, 'local', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

-- A browser's sign-in. The cookie holds a random value; only its SHA-256 is
-- kept here, so a copy of the database can't sign anyone in.
CREATE TABLE web_sessions (
    id_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX web_sessions_user ON web_sessions (user_id);

-- A person's GitHub sign-in (access and refresh tokens), encrypted with the
-- server's token key: Save & share commits and pushes as them.
CREATE TABLE github_tokens (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    ciphertext BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
