-- Folders the user chose to export to, such as a local Dropbox folder.
CREATE TABLE export_destinations (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    path      TEXT NOT NULL UNIQUE,
    added_at  TEXT NOT NULL
);
