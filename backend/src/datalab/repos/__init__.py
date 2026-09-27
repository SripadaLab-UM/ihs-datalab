"""The lab's git repositories: GitHub sign-in, the clones, and git itself.

- `github.py`: signing in with the lab's GitHub App through the device flow
  ("enter this code at github.com/login/device"), and the user's tokens,
  kept in the OS keychain and refreshed there.
- `credential_helper.py`: how git gets the token. DataLab names it for each
  git command it runs (never in anyone's git config); it reads the token
  from the keychain and hands it to git through a pipe.
- `git.py`: running git with a fixed, safe configuration, and the clones in
  `<data_dir>/repos/`: clone, sync, status, and the plumbing Save & share
  needs.

The token never enters a container, and never goes on disk in plain text
(docs/ARCHITECTURE.md §8, docs/SAFETY.md "Shared knowledge base").
"""
