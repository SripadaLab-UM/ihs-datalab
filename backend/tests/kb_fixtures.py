"""A small knowledge base, and local bare git repos standing in for GitHub."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from datalab.knowledge import check as kb

FITBIT = """\
---
id: fitbit
kind: source
status: reviewed
summary: Fitbit trackers, daily summaries from 2021 on.
evidence:
  - schema: IHS_2025.VFITBITDAILYDATA.TRACKERSTEPS
limitations:
  - Wear time isn't recorded.
related: [qc/midnight-sleep]
cohorts: [2025]
reviewed_by: yfang
reviewed_on: 2026-09-01
---

# Fitbit

Daily steps are in IHS_2025.VFITBITDAILYDATA.TRACKERSTEPS.
See [the sleep rule](../qc/midnight-sleep.md).
"""

MIDNIGHT = """\
---
id: midnight-sleep
kind: qc
status: draft
summary: Sleep that spans midnight counts for the night it started.
evidence:
  - legacy: reference/2024/Sleep_2024.R#L40-72
limitations:
  - Naps aren't covered.
cohorts: [2024, 2025]
---

# Midnight-spanning sleep

Assign a sleep period to the date it began.
"""

SCHEMA = """\
schema: IHS_2025
name: VFITBITDAILYDATA
type: VIEW
comment: Fitbit daily summary
columns:
- name: STUDY_PARTICIPANT_ID
  type: VARCHAR2(64)
  nullable: false
  comment: ''
- name: TRACKERSTEPS
  type: NUMBER
  nullable: true
  comment: ''
primary_key: []
"""


def sample_kb() -> dict[str, bytes]:
    files = {
        "AGENTS.md": b"# Lab knowledge base\n\nRead index.md first.\n",
        "sources/fitbit.md": FITBIT.encode(),
        "qc/midnight-sleep.md": MIDNIGHT.encode(),
        "generated/schema/IHS_2025/VFITBITDAILYDATA.yml": SCHEMA.encode(),
        ".github/workflows/kb-check.yml": b"name: check\non: push\n",
    }
    files["index.md"] = kb.check(files).index.encode()
    return files


def page(page_id: str, kind: str, folder: str, *, body: str = "", status: str = "draft") -> str:
    return (
        f"---\nid: {page_id}\nkind: {kind}\nstatus: {status}\n"
        f"summary: About {page_id}.\nevidence:\n  - legacy: reference/{page_id}.R\n"
        f"limitations:\n  - Only a test.\ncohorts: [2025]\n---\n\n# {page_id}\n\n{body}\n"
    )


def git(*args: str, cwd: Path, input: bytes | None = None) -> str:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "Someone Else",
        "GIT_AUTHOR_EMAIL": "else@example.com",
        "GIT_COMMITTER_NAME": "Someone Else",
        "GIT_COMMITTER_EMAIL": "else@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(cwd),
    }
    done = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgSign=false", *args],
        cwd=cwd,
        env=env,
        input=input,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


class Remote:
    """A bare repo standing in for GitHub, and someone else's clone of it."""

    def __init__(self, root: Path, files: dict[str, bytes] | None = None) -> None:
        self.bare = root / "github" / "ihs-knowledge.git"
        self.bare.mkdir(parents=True)
        git("init", "--bare", "-q", str(self.bare), cwd=root)
        # Like the "Protect main" ruleset on GitHub: no force-push, no deleting.
        git("config", "receive.denyNonFastForwards", "true", cwd=self.bare)
        git("config", "receive.denyDeletes", "true", cwd=self.bare)
        self.other = root / "someone-else"
        self.other.mkdir()
        git("clone", "-q", str(self.bare), str(self.other), cwd=root)
        self.write(files if files is not None else sample_kb(), "Start the knowledge base")

    @property
    def url(self) -> str:
        return str(self.bare)

    def write(self, files: dict[str, bytes | None], message: str) -> str:
        """Someone else commits and pushes to main."""
        if self.head():
            git("pull", "-q", "--ff-only", cwd=self.other)
        for path, content in files.items():
            target = self.other / path
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        git("add", "-A", cwd=self.other)
        git("commit", "-q", "-m", message, cwd=self.other)
        git("push", "-q", "origin", "HEAD:main", cwd=self.other)
        return self.head() or ""

    def head(self) -> str | None:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "refs/heads/main"],
            cwd=self.bare,
            capture_output=True,
        )
        return done.stdout.decode().strip() or None

    def show(self, path: str, ref: str = "main") -> str:
        return git("show", f"{ref}:{path}", cwd=self.bare)

    def log(self, fmt: str = "%H %P|%an <%ae>|%s") -> list[str]:
        return git("log", f"--format={fmt}", "main", cwd=self.bare).splitlines()
