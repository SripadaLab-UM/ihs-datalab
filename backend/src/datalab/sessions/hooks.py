"""Where later parts of DataLab plug into conversations, without editing the manager.

- **After-turn hooks** run once a turn is over: after its checkpoint, number
  check and rigor review, before the chat hears the turn is done. The
  Knowledge milestone uses one to diff `/work/kb` and propose edits.
- **Workspace seeds** put files into a conversation's `/work` once, before
  its first turn: the Knowledge milestone's editable copy of the knowledge
  base at `/work/kb`. Each records the base it copied (a commit id, say).
- **Mount providers** add read-only mounts to a conversation's container
  when it starts, next to its attachments, for what the agent reads but
  mustn't change. Only under `/mnt`, and only from folders the provider
  declared.

All are registered on the `SessionManager` when the app is built
(`register_after_turn`, `register_workspace_seed`, `register_mounts`). One
that fails is logged and skipped: it can never break a turn or stop a
container starting.
"""

from __future__ import annotations

import os
import posixpath
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from datalab.sessions.containers import Mount
from datalab.sessions.inputs import private_place
from datalab.sessions.store import Conversation


@dataclass(frozen=True)
class TurnInfo:
    """What an after-turn hook is told about the turn that just ended."""

    # The turn's number in the conversation (1 for the first question).
    turn: int
    # How the agent's turn ended.
    status: Literal["completed", "interrupted", "failed"]
    # The turn's events are the conversation's events after this seq.
    since: int
    # The checkpoint of /work as the turn left it (after its review, if that
    # ran commands), or None if none was saved. Read files from it, never
    # from the live folder the agent can change (docs/ARCHITECTURE.md §3).
    checkpoint: int | None
    # True when only the rigor review was run again: there's no new answer.
    review_only: bool = False


# `await hook(conversation_id, info)`. All of a turn's hooks run at once and
# share one time limit (the manager's AFTER_TURN_SECONDS); the turn stays
# busy meanwhile. A hook still going then is cancelled and left to finish
# on its own, and the turn ends anyway. So a hook must not do blocking I/O
# on the event loop (use asyncio.to_thread), and must not catch
# asyncio.CancelledError: either would hold up the whole app.
AfterTurnHook = Callable[[str, TurnInfo], Awaitable[None]]

# `seed(conversation, work)`: write files into `work`, the host folder that
# is the conversation's /work, and return the base they came from (a commit
# id, say), which the manager records (`SessionManager.workspace_base`).
# Called on a worker thread before the conversation's first turn, and once
# only, ever: never again when its container restarts after being idle, so
# the agent's edits are never overwritten. The agent hasn't run yet, but a
# seed still writes only new files, without following links. If it raises,
# nothing is recorded and it's tried again before the next turn.
WorkspaceSeed = Callable[[Conversation, Path], str | None]

# `provider(conversation)`: the mounts to add when its container starts.
# Called on a worker thread, so it may read the disk, but must be quick and
# thread-safe. Each mount is checked (see `checked_mount`) every time.
MountProvider = Callable[[Conversation], list[Mount]]

# The only place in the container a provider may mount into: nothing of
# DataLab's, Codex's or the system's lives there.
MOUNT_ROOT = "/mnt"


class MountRefused(ValueError):
    pass


def checked_mount(mount: Mount, *, roots: Sequence[Path], sessions_dir: Path) -> Mount:
    """The mount to pass to docker, or MountRefused saying why not.

    - The target is normalised (docker would read `/./work` as `/work`) and
      must be inside MOUNT_ROOT.
    - The source must be a full path. It's resolved, following every link,
      and the real location is what's mounted. It must be inside one of the
      provider's declared `roots`, never in any conversation's folders
      (`sessions_dir`), and never a home folder, a dot-folder in it, or a
      credentials file.
    """
    target = _checked_target(mount.target)
    if not mount.source.is_absolute():
        raise MountRefused(f"{mount.source} isn't a full path")
    try:
        real = mount.source.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MountRefused(f"{mount.source} can't be found") from error
    sessions = _real(sessions_dir)
    if _within(real, sessions) or _within(sessions, real):
        raise MountRefused(f"{real} is in DataLab's conversation folders")
    if not any(_within(real, _real(root)) for root in roots):
        raise MountRefused(f"{real} isn't inside a folder its provider declared")
    problem = private_place(real)
    if problem:
        raise MountRefused(f"{real} can't be mounted: {problem}")
    return Mount(real, target)


def _checked_target(target: str) -> str:
    if not target.startswith("/") or any(c in target for c in "\\\0\n\r"):
        raise MountRefused(f"{target!r} isn't a plain absolute container path")
    normal = posixpath.normpath(target)
    if not normal.startswith(MOUNT_ROOT + "/"):
        raise MountRefused(f"{target!r} isn't inside {MOUNT_ROOT}")
    return normal


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _within(path: Path, folder: Path) -> bool:
    """Is `path` the folder itself or inside it? Compared the way the disk
    does: Mac and Windows ignore case."""
    a, b = _key(path), _key(folder).rstrip("/")
    return a == b or a.startswith(b + "/")


def _key(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return text.casefold() if sys.platform in ("darwin", "win32") else text
