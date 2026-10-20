"""The lab's processed study data in Dropbox, as a second data source.

Conversations can use the study database, this folder, or both. DataLab
reaches Dropbox only from the host, through Dropbox's API with the lab's
read-only app; a conversation's container never talks to Dropbox. A synced
Dropbox folder on the computer isn't used: online-only files there are
0-byte placeholders that a plain read doesn't download.

- auth.py: signing in (PKCE, no app secret) and the tokens, in the keychain.
- client.py: the API calls DataLab makes: list, metadata, download.
- index.py: the chosen folder's file list (metadata only), kept in SQLite.
- families.py: files grouped into datasets by name pattern.
- service.py: what the routes and agent tools use, including fetching one
  file into a conversation (staging) and the practice profile's local folder.
"""
