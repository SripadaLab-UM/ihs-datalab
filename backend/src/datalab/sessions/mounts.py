"""What a conversation's container mounts beyond DataLab's own: its
attachments, then what mount providers add (hooks.py).

Worked out each time a container starts, so every mount passes its checks as
things are then: an attachment that has moved or become unsafe, or a
provider's mount outside the folders it declared, is left out and the
container starts without it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from datalab.config import Settings, default_data_dir
from datalab.sessions import modes
from datalab.sessions.hooks import MountProvider, MountRefused, checked_mount, data_dir_problem
from datalab.sessions.inputs import (
    AttachmentStore,
    is_sample,
    mount_args,
    practice_samples,
    private_place,
    recheck,
)
from datalab.sessions.store import ConversationStore

log = logging.getLogger(__name__)


def other_data_dirs(settings: Settings) -> list[Path]:
    """The other profiles' usual data folders: never mountable at all."""
    return [d for p in ("real", "practice") if (d := default_data_dir(p)) != settings.data_dir]


def check_mount_roots(roots: Sequence[Path], settings: Settings) -> None:
    """Refuse a mount root that isn't a full path, or is (or holds) a data
    folder or a private place."""
    for root in roots:
        real = Path(os.path.realpath(root))
        problem = (
            "it isn't a full path"
            if not root.is_absolute()
            else data_dir_problem(real, settings.data_dir, other_data_dirs(settings))
            or private_place(real)
        )
        if problem:
            raise ValueError(f"{root} can't be a mount root: {problem}")


def extra_mounts(
    store: ConversationStore,
    settings: Settings,
    attachments: AttachmentStore | None,
    providers: Sequence[tuple[MountProvider, tuple[Path, ...]]],
    conversation_id: str,
) -> list[str]:
    """Mounts beyond DataLab's own: attachments, then what providers add."""
    mounts = input_mounts(store, settings, attachments, conversation_id) if attachments else []
    conversation = store.get(conversation_id) if providers else None
    if conversation is None:
        return mounts
    others = other_data_dirs(settings)
    targets: set[str] = set()
    for provider, roots in providers:
        try:
            provided = provider(conversation)
        except Exception:
            # The container starts without them rather than not at all.
            log.exception("a mount provider failed in %s", conversation_id)
            continue
        for mount in provided:
            try:
                checked = checked_mount(
                    mount, roots=roots, data_dir=settings.data_dir, other_data_dirs=others
                )
                if checked.target in targets:
                    # Docker refuses to start a container with two.
                    raise MountRefused(f"{checked.target} is already mounted")
            except MountRefused as refused:
                log.warning("not mounting %s in %s: %s", mount.target, conversation_id, refused)
                continue
            targets.add(checked.target)
            mounts += checked.args()
    return mounts


def input_mounts(
    store: ConversationStore,
    settings: Settings,
    attachments: AttachmentStore,
    conversation_id: str,
) -> list[str]:
    """Mounts for the attachments that still pass every check, right now."""
    conversation = store.get(conversation_id)
    mode = modes.MODES.get(conversation.mode) if conversation else None
    if mode is None or not mode.attachments:
        return []  # Knowledge writing: metadata only (the API refuses to attach, too)
    protected = [settings.data_dir, *(default_data_dir(p) for p in ("real", "practice"))]
    mountable = []
    for attachment in attachments.list(conversation_id):
        samples = practice_samples()
        if settings.profile == "practice" and is_sample(attachment, samples):
            mountable.append(attachment)
            continue
        problem = recheck(attachment, protected=protected)
        if problem is None:
            mountable.append(attachment)
        else:
            log.warning("not mounting an attachment in %s: %s", conversation_id, problem)
            store.append(
                conversation_id,
                "input_unavailable",
                {"path": attachment.container_path, "reason": problem},
            )
    return mount_args(mountable)
