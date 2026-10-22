"""Where a conversation runs: "standard" on DataLab's own machine, or "high"
on the lab's larger machine (`[server.high_compute]`; docs/DISTRIBUTION.md,
"High compute").

A high-compute conversation's folders live on that machine and are mounted
here at the same path, so DataLab reads and writes them as anywhere else,
and its containers mount them there by the same names.
"""

from __future__ import annotations

from pathlib import Path

from datalab.auth.users import User
from datalab.config import HighComputeSettings, Settings
from datalab.sessions.containers import LOCAL, ContainerLimits, Engine, limits_for
from datalab.sessions.store import Compute


def high_compute(settings: Settings) -> HighComputeSettings | None:
    return settings.server.high_compute if settings.server is not None else None


def may_choose_high(settings: Settings, user: User) -> bool:
    """Whether this person may make a high-compute conversation."""
    high = high_compute(settings)
    return high is not None and (high.open_to == "lab" or user.admin)


def session_root(settings: Settings, conversation_id: str, compute: Compute) -> Path:
    high = high_compute(settings)
    if compute == "high" and high is not None:
        return Path(high.sessions_dir) / conversation_id
    return settings.data_dir / "sessions" / conversation_id


def engine_for(settings: Settings, compute: Compute) -> Engine:
    high = high_compute(settings)
    if compute != "high" or high is None:
        return LOCAL
    return Engine(
        env={"DOCKER_HOST": high.docker_host},
        gpu_env={"DOCKER_HOST": high.gpu_docker_host} if high.gpu_docker_host else None,
        relay_socket=high.relay_socket,
    )


def limits_for_compute(settings: Settings, compute: Compute) -> ContainerLimits:
    high = high_compute(settings)
    if compute == "high" and high is not None:
        return ContainerLimits(memory=high.agent_memory, cpus=high.agent_cpus)
    return limits_for(settings)
