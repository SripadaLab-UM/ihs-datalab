"""The lab knowledge base in DataLab (docs/KNOWLEDGE_BASE.md).

- `check.py`: the knowledge-base check, DataLab's own code (never a script
  from the repo). A library, and `datalab kb-check <path>` for GitHub Actions.
- `proposals.py`: each conversation's copy at `/work/kb`, compared after
  every turn with the version it came from; the differences are proposed
  edits, kept in the database (migration 0007).
- `share.py`: Save & share. Check, commit as the person, rebase onto GitHub's
  `main`, check again, and push exactly the commit that passed.
- `service.py`: ties them to the clone, the sign-in, and the conversations.
"""
