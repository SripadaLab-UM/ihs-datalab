"""Where later parts of DataLab plug into conversations, without editing the manager.

- **After-turn hooks** run once a turn is over: after its checkpoint, number
  check and rigor review, before the chat hears the turn is done. The
  Knowledge milestone uses one to diff `/work/kb` and propose edits.
- **Mount providers** add read-only mounts to a conversation's container
  when it starts, next to its attachments. The Knowledge and Pipelines
  milestones use them for what the agent reads but mustn't change.

Both are registered on the `SessionManager` when the app is built
(`register_after_turn`, `register_mounts`). A hook or provider that fails is
logged and skipped: it can never break a turn or stop a container starting.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from datalab.sessions.containers import Mount
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


# `await hook(conversation_id, info)`. Each has a time limit (the manager's
# AFTER_TURN_SECONDS); the turn stays busy while hooks run.
AfterTurnHook = Callable[[str, TurnInfo], Awaitable[None]]

# `provider(conversation)`: the mounts to add when its container starts.
# Called on a worker thread, so it may read the disk, but must be quick and
# thread-safe. A mount onto one of DataLab's own folders is refused.
MountProvider = Callable[[Conversation], list[Mount]]

# Container folders DataLab itself mounts or owns: no provider may mount at
# or inside one of them.
RESERVED_TARGETS = ("/work", "/codex-home", "/data", "/inputs")


def mount_problem(mount: Mount) -> str | None:
    """Why a provider's mount can't be used, or None if it's fine."""
    target = mount.target
    parts = target.split("/")
    if not target.startswith("/") or "\\" in target or ".." in parts or "" in parts[1:]:
        return f"{target!r} isn't a plain absolute container path"
    for reserved in RESERVED_TARGETS:
        if target == reserved or target.startswith(reserved + "/"):
            return f"{target!r} is inside {reserved}, which DataLab manages"
    if not mount.source.exists():
        return f"{mount.source} doesn't exist"
    return None
