"""A conversation's /work files around its turns: the checkpoint after a turn,
and putting the files back as they were at one.

Checkpoints themselves are checkpoints.py's; these add what a conversation
needs around them: the container frozen while one is taken, a notice in the
chat when one can't be, and a restore that can be undone. Holding the
conversation meanwhile (nothing may start, the container stopped) is the
SessionManager's (manager.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

from datalab.config import Settings
from datalab.sessions.checkpoints import Checkpoint, Checkpoints, RestoreResult
from datalab.sessions.containers import DockerError, SessionContainers
from datalab.sessions.store import ConversationStore

log = logging.getLogger(__name__)


async def checkpoint_after_turn(
    store: ConversationStore,
    checkpoints: Checkpoints,
    containers: SessionContainers,
    settings: Settings,
    root: Path,
    conversation_id: str,
    turn: int,
    label: str = "",
    *,
    review: bool = False,
) -> int | None:
    """Checkpoint /work after a turn, with the container frozen meanwhile.
    Its number, or None if it couldn't be saved."""
    failed = {"text": "DataLab couldn't save a checkpoint of the files after this turn."}
    if not enough_disk(root, settings):
        store.append(
            conversation_id,
            "notice",
            {
                "text": "This computer is low on disk space, so DataLab didn't save a "
                "checkpoint of the files after this turn."
            },
        )
        return None
    try:
        await containers.pause()
        take = asyncio.ensure_future(
            asyncio.to_thread(
                checkpoints.take, label or f"After turn {turn}", turn=turn, review=review
            )
        )
        try:
            checkpoint = await asyncio.shield(take)
        except asyncio.CancelledError:
            # Finish reading before the container can run again.
            with contextlib.suppress(Exception):
                await take
            raise
    except DockerError:
        log.exception("couldn't pause %s for a checkpoint", conversation_id)
        store.append(conversation_id, "notice", failed)
        return None
    except Exception:
        log.exception("checkpoint failed in %s", conversation_id)
        store.append(conversation_id, "notice", failed)
        return None
    finally:
        # Always, even if cancelled: a paused agent can't work.
        with contextlib.suppress(DockerError):
            await containers.unpause()
    store.append(
        conversation_id,
        "checkpoint",
        {
            "number": checkpoint.number,
            "turn": turn,
            "files": checkpoint.files,
            "bytes": checkpoint.bytes,
            "skipped": len(checkpoint.skipped),
        },
    )
    return checkpoint.number


async def restore_files(
    store: ConversationStore,
    checkpoints: Checkpoints,
    conversation_id: str,
    number: int,
    target: Checkpoint,
) -> RestoreResult:
    """Put /work back as it was at checkpoint `number` (`target`), with the
    container already stopped. The current files are checkpointed first, so
    the restore can be undone; the chat is told what was restored."""
    before = await asyncio.to_thread(
        checkpoints.take, f"Before restoring to {target.label.lower()}"
    )
    # Whatever that checkpoint couldn't save is left alone: deleting it
    # would lose it for good.
    keep = frozenset(s.path for s in before.skipped)
    try:
        result = await asyncio.to_thread(checkpoints.restore, number, keep=keep)
    except Exception as error:
        log.exception("restore failed in %s", conversation_id)
        store.append(
            conversation_id,
            "files_restored",
            {"label": target.label, "failed": True, "error": str(error)},
        )
        # So the files shown match what's there now.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(checkpoints.take, "After a restore that failed")
        raise
    store.append(
        conversation_id,
        "files_restored",
        {
            "checkpoint": number,
            "label": target.label,
            "turn": target.turn,
            "written": result.written,
            "removed": result.removed,
            "left_alone": result.left_alone,
            "not_restored": result.not_restored,
        },
    )
    try:
        await asyncio.to_thread(checkpoints.take, f"Restored to {target.label.lower()}")
    except Exception:
        log.exception("checkpoint after restore failed in %s", conversation_id)
        store.append(
            conversation_id,
            "notice",
            {"text": "The files were restored, but DataLab couldn't save a checkpoint."},
        )
    return result


def enough_disk(folder: Path, settings: Settings) -> bool:
    try:
        free = shutil.disk_usage(folder if folder.exists() else settings.data_dir).free
    except OSError:
        return True
    return free >= settings.limits.min_free_disk_bytes
