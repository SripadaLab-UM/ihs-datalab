# Migrations

Plain numbered `.sql` files, applied in order at startup, each exactly once
(see `db/__init__.py`). A migration is never edited after it ships: change
the schema with a new file.

Numbers already taken by work in progress, so parallel branches don't
collide. Use the reserved number for your area; take the next free one
after these for anything else.

| Number | For | Status |
|---|---|---|
| 0001–0005 | queries, conversations, attachments, exports, rigor | in use |
| 0006 | `queries.origin`: conversation, playground, or run | in use |
| 0007 | knowledge: proposed edits, each conversation's base, repo sync state (milestone 5) | in use |
| 0008 | workflow runs, their steps and deliveries; `export_destinations.key` (milestone 6) | in use |
| 0009 | update metadata: installed versions and backups (milestone 7) | reserved |
| 0010 | pipelines: proposed changes, each conversation's base, test runs (milestone 6) | in use |
| 0011 | export folders: `export_destinations.offered`; a delivery's folder name and sync app | in use |
| 0012 | knowledge: a person's own page edits (drafts kept on this computer until Save & share) | in use |
| 0013 | `conversations.express`: the Express switch | in use |
