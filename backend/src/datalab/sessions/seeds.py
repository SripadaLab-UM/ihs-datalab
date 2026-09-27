"""Workspace seeds: files DataLab puts into a conversation's /work, once.

A seed (see hooks.py) writes into a fresh staging folder that DataLab made
in the session folder, outside every mount, where the agent can't reach.
DataLab then renames that folder into /work in one step. It never writes
into /work itself: that folder is the agent's, and anything there (a link
to ~/.ssh left where the copy should go, say) could redirect the write.

Each seed runs at most once per conversation, and only before its first
turn, when the agent hasn't run yet. What happened is recorded in the
session folder before and after it runs, so a crash midway is noticed and
never followed by a second copy on top of the first:

- `started`: running now, or cut off by a crash (then counted as failed);
- `done`: in place, with the base it came from (a commit id, say);
- `failed`: it raised, was cut off, or its place in /work was taken. The
  chat says so; it isn't tried again;
- `skipped`: the conversation had already had turns when the seed was
  first registered, so /work may hold the agent's files.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datalab.sessions.containers import SessionPaths
from datalab.sessions.hooks import WorkspaceSeed
from datalab.sessions.store import Conversation

log = logging.getLogger(__name__)

# A seed's place in /work: one plain name, such as "kb".
_PLACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Seed:
    name: str
    run: WorkspaceSeed
    # The folder in /work it becomes.
    into: str
    # Only for conversations in these modes (all, when None). A mode never
    # changes, so the others are simply never seeded, and nothing is recorded.
    modes: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not _PLACE.fullmatch(self.into) or self.into in ("outputs",):
            raise ValueError(f"{self.into!r} can't be a workspace seed's folder in /work")


def records(paths: SessionPaths) -> dict[str, dict[str, Any]]:
    """What happened to each seed in this conversation, by name."""
    try:
        value = json.loads(paths.seeds.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def base(paths: SessionPaths, name: str) -> str | None:
    """The base a seed that's in place came from."""
    entry = records(paths).get(name) or {}
    return entry.get("base") if entry.get("status") == "done" else None


def run_seeds(
    seeds: list[Seed],
    conversation: Conversation,
    paths: SessionPaths,
    *,
    had_turn: bool,
    notice: Callable[[str], object],
) -> None:
    """Run the seeds that haven't run for this conversation (on a worker thread)."""
    done = records(paths)
    for seed in seeds:
        if seed.modes is not None and conversation.mode not in seed.modes:
            continue
        entry = done.get(seed.name)
        if entry is not None and entry.get("status") != "started":
            continue
        if entry is not None:
            # Cut off by a crash: whatever it did stays as it is.
            _failed(done, paths, seed, "DataLab stopped while it was being set up", notice)
            continue
        if had_turn:
            log.info("not seeding %s into %s: it has had turns", seed.name, conversation.id)
            _save(done, paths, seed.name, status="skipped")
            continue
        _save(done, paths, seed.name, status="started")
        try:
            result = _run(seed, conversation, paths)
        except Exception as error:
            log.exception("workspace seed %s failed in %s", seed.name, conversation.id)
            _failed(done, paths, seed, str(error) or type(error).__name__, notice)
            continue
        _save(done, paths, seed.name, status="done", base=result)


def _run(seed: Seed, conversation: Conversation, paths: SessionPaths) -> str | None:
    paths.create()
    target = paths.work / seed.into
    if _taken(target):
        raise FileExistsError(f"/work/{seed.into} already exists")
    staging_root = paths.root / "seed-staging"
    # Left over from a crash: DataLab's own folder, never the agent's.
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir()
    staging = tempfile.mkdtemp(prefix=f"{seed.name}-", dir=staging_root)
    try:
        result = seed.run(conversation, Path(staging))
        # Checked again just before: nothing may have appeared meanwhile.
        if _taken(target):
            raise FileExistsError(f"/work/{seed.into} already exists")
        # One step, and the name itself is replaced, never followed. The
        # agent hasn't run yet (seeds run only before the first turn), so
        # nothing can appear between the check above and this.
        os.rename(staging, target)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return result


def _taken(path: Path) -> bool:
    """Whether anything is at `path`, a link included (never followed)."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _failed(
    done: dict[str, dict[str, Any]],
    paths: SessionPaths,
    seed: Seed,
    reason: str,
    notice: Callable[[str], object],
) -> None:
    _save(done, paths, seed.name, status="failed", error=reason)
    notice(
        f"DataLab couldn't set up /work/{seed.into} in this conversation ({reason}). "
        "It won't try again here; a new conversation will have it."
    )


def _save(done: dict[str, dict[str, Any]], paths: SessionPaths, name: str, **entry: Any) -> None:
    done[name] = {**entry, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    paths.root.mkdir(parents=True, exist_ok=True)
    fresh = paths.seeds.with_suffix(".tmp")
    fresh.write_text(json.dumps(done, indent=1))
    fresh.replace(paths.seeds)
