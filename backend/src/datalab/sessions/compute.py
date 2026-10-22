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


def resources_note(settings: Settings, compute: Compute) -> str:
    """On the lab's server: what the agent's container may use, for its
    instructions. Tools there (`nproc`, `free`, /proc/meminfo) show the whole
    machine, not the container's share (a server with cgroup v1 doesn't hide
    it), so an agent left to them sizes its work for 32 CPUs and 754 GB."""
    if settings.server is None:
        return ""
    limits = limits_for_compute(settings, compute)
    high = high_compute(settings)
    gpu = compute == "high" and high is not None and bool(high.gpu_docker_host)
    where = "the lab's high-compute server" if compute == "high" and high else "the lab's server"
    return (
        f"\nThis conversation runs on {where}. Your container may use at most "
        f"{_cpus(limits.cpus)} and {_memory(limits.memory)} of memory"
        + (", and has a GPU (see `nvidia-smi`)" if gpu else "")
        + ". `nproc`, `free` and /proc/meminfo show the whole machine, not your share: "
        "size parallel work and memory to these limits (they're in DATALAB_CPUS and "
        "DATALAB_MEMORY, and OMP_NUM_THREADS and MC_CORES are set to match). A process "
        "that goes over the memory limit is stopped.\n"
    )


def limit_env(settings: Settings, compute: Compute) -> list[str]:
    """The agent's environment for its limits, on the lab's server: numeric
    libraries' thread counts, and R's parallel default, set to its CPUs."""
    if settings.server is None:
        return []
    limits = limits_for_compute(settings, compute)
    cpus = str(max(1, int(float(limits.cpus))))
    threads = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_MAX_THREADS")
    return [
        f"DATALAB_CPUS={limits.cpus}",
        f"DATALAB_MEMORY={limits.memory}",
        f"MC_CORES={cpus}",
        *(f"{name}={cpus}" for name in threads),
    ]


def _cpus(cpus: str) -> str:
    return "1 CPU" if float(cpus) == 1 else f"{cpus} CPUs"


def _memory(size: str) -> str:
    number, unit = size[:-1], size[-1]
    return f"{number} {dict(k='KB', m='MB', g='GB')[unit]}"


def limits_for_compute(settings: Settings, compute: Compute) -> ContainerLimits:
    high = high_compute(settings)
    if compute == "high" and high is not None:
        return ContainerLimits(memory=high.agent_memory, cpus=high.agent_cpus)
    return limits_for(settings)
