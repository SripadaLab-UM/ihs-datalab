"""The no-network container a workflow's R, pipeline, and custom QC steps run in.

Each step gets a fresh container of the agent image, pinned by its image id,
with the flag set the runner spike tested
(spikes/2026-09-27-workflow-runner/README.md, §1): no network, a read-only
root with a noexec /tmp, an explicit non-root user, no capabilities, no new
privileges, an init process (so Stop takes 0.1 s, not 10), pids and memory
limits, `--pull never`, and `--mount` for every folder, read-only except the
step's own output and result folders.

`/run/out` and `/run/result` are bind mounts, not the noexec /tmp, so a
step can write a program there and run it. That is no more than its R code
can do already; the container has no network and no capabilities, and only
declared outputs (regular files) come back.

Containers carry `datalab.run`, `datalab.profile` and `datalab.instance`
labels. DataLab's startup cleanup (`remove_all_session_containers`) removes
this instance's leftovers by the last two; Stop removes a run's by the first.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

RUN_LABEL = "datalab.run"
PROFILE_LABEL = "datalab.profile"
INSTANCE_LABEL = "datalab.instance"

# Deterministic numerics and time: one thread for BLAS, OpenMP and
# data.table, and UTC. The image sets LC_ALL=C.UTF-8.
RUNTIME_ENV = {
    "TZ": "Etc/UTC",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "R_DATATABLE_NUM_THREADS": "1",
}
# What run_step.R sets before every step.
RNG_KIND = ("Mersenne-Twister", "Inversion", "Rejection")
MAX_LOG_BYTES = 1024 * 1024
# The image's own user; on native Linux Docker, the host's (see _user).
STEP_USER = "10004:10004"
STEP_COMMAND = ("Rscript", "--vanilla", "/run/datalab/run_step.R")

_PACKAGES_R = (
    'ip <- installed.packages()[, c("Package", "Version")];'
    'ip <- ip[order(ip[, "Package"]), , drop = FALSE];'
    'cat(R.version.string, "\\n");'
    'cat(paste(ip[, 1], ip[, 2], sep = "=="), sep = "\\n")'
)


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageFacts:
    ref: str  # as configured
    digest: str  # the image's id: sha256:…; steps run by this
    platform: str  # linux/arm64
    repo_digests: tuple[str, ...]
    r_version: str
    r_packages_sha256: str


@dataclass(frozen=True)
class StepLimits:
    timeout_seconds: float = 30 * 60
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 256


@dataclass(frozen=True)
class Bind:
    source: Path
    target: str
    readonly: bool = True

    def args(self) -> list[str]:
        # --mount, not -v: a missing source is an error, never a new empty
        # folder. Values are CSV-quoted, so commas and quotes are safe.
        fields = ["type=bind", f"source={self.source}", f"target={self.target}"]
        if self.readonly:
            fields.append("readonly")
        return ["--mount", ",".join(_csv_field(f) for f in fields)]


@dataclass
class ContainerStep:
    run_id: str
    name: str  # unique per run: the step id, or build-<package>
    image: str  # the image id
    binds: list[Bind]
    # A folder the step writes to, watched against `max_bytes`.
    watch: Path
    max_bytes: int
    command: tuple[str, ...] = STEP_COMMAND
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerOutcome:
    exit_code: int
    log: bytes
    timed_out: bool = False
    over_cap: bool = False


class Sandbox(Protocol):
    """Runs container steps. DockerSandbox is the real one; tests fake it."""

    async def image(self, ref: str) -> ImageFacts | None:
        """The image's facts, or None if it isn't on this computer."""
        ...

    async def host_platform(self) -> str: ...

    async def run(self, step: ContainerStep) -> ContainerOutcome: ...

    async def remove_run(self, run_id: str) -> None:
        """Remove every container of a run (Stop, and after each run)."""
        ...


class DockerSandbox:
    def __init__(
        self,
        *,
        profile: str,
        instance: str,
        limits: StepLimits,
        cache_dir: Path,
        extra_labels: dict[str, str] | None = None,
    ) -> None:
        if not instance:
            raise ValueError("A run's containers need their DataLab instance.")
        self.profile = profile
        self.instance = instance
        self.limits = limits
        self.cache_dir = cache_dir
        self.labels = {PROFILE_LABEL: profile, INSTANCE_LABEL: instance, **(extra_labels or {})}

    def container_name(self, run_id: str, name: str) -> str:
        return f"datalab-{self.instance[:8]}-{run_id}-{name}".replace("_", "-")[:120]

    def argv(self, step: ContainerStep) -> list[str]:
        labels = {**self.labels, RUN_LABEL: step.run_id}
        args = ["docker", "run", "--rm", "--pull", "never"]
        args += ["--name", self.container_name(step.run_id, step.name)]
        for key, value in labels.items():
            args += ["--label", f"{key}={value}"]
        args += [
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=1g,mode=1777,nosuid,nodev,noexec",
            "--user", _user(),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--init",
            "--pids-limit", str(self.limits.pids),
            "--memory", self.limits.memory, "--memory-swap", self.limits.memory,
            "--cpus", self.limits.cpus,
            "--workdir", "/run/out",
        ]  # fmt: skip
        for key, value in {**RUNTIME_ENV, "HOME": "/tmp", **step.env}.items():
            args += ["--env", f"{key}={value}"]
        for bind in step.binds:
            args += bind.args()
        return [*args, step.image, *step.command]

    async def image(self, ref: str) -> ImageFacts | None:
        code, out, err = await _docker("image", "inspect", "--format", "{{json .}}", ref)
        if code != 0:
            if "no such" in err.lower():
                return None
            raise SandboxError(f"Docker couldn't describe the agent image: {err.strip()[:300]}")
        info = json.loads(out)
        digest = info["Id"]
        platform = f"{info.get('Os', '')}/{info.get('Architecture', '')}"
        if info.get("Variant"):
            platform += f"/{info['Variant']}"
        r_version, packages = await self._packages(digest)
        return ImageFacts(
            ref=ref,
            digest=digest,
            platform=platform,
            repo_digests=tuple(info.get("RepoDigests") or ()),
            r_version=r_version,
            r_packages_sha256=packages,
        )

    async def host_platform(self) -> str:
        code, out, err = await _docker("version", "--format", "{{.Server.Os}}/{{.Server.Arch}}")
        if code != 0:
            raise SandboxError(f"Docker isn't answering: {err.strip()[:300]}")
        return out.strip()

    async def _packages(self, digest: str) -> tuple[str, str]:
        """R's version and a fingerprint of installed.packages(), cached per image."""
        cache = self.cache_dir / f"image-{digest.split(':')[-1][:32]}.json"
        with contextlib.suppress(OSError, ValueError, KeyError):
            facts = json.loads(cache.read_text())
            return facts["r_version"], facts["r_packages_sha256"]
        name = f"probe-{os.urandom(3).hex()}"
        step = ContainerStep(
            run_id="run_image-facts",
            name=name,
            image=digest,
            binds=[],
            watch=self.cache_dir,
            max_bytes=0,
            command=("Rscript", "--vanilla", "-e", _PACKAGES_R),
        )
        argv = self.argv(step)
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=120)
        except BaseException:
            await self._remove(self.container_name(step.run_id, name))
            raise
        if process.returncode != 0:
            raise SandboxError(
                f"The agent image couldn't list its R packages: {err.decode()[-300:]}"
            )
        lines = out.decode().splitlines()
        facts = {
            "r_version": lines[0].strip() if lines else "",
            "r_packages_sha256": hashlib.sha256("\n".join(lines[1:]).encode()).hexdigest(),
            "r_packages": len(lines) - 1,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(facts, indent=2))
        return facts["r_version"], facts["r_packages_sha256"]

    async def run(self, step: ContainerStep) -> ContainerOutcome:
        name = self.container_name(step.run_id, step.name)
        process = await asyncio.create_subprocess_exec(
            *self.argv(step), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        assert process.stdout is not None
        log = bytearray()

        async def read_log() -> None:
            assert process.stdout is not None
            while chunk := await process.stdout.read(64 * 1024):
                if len(log) < MAX_LOG_BYTES:
                    log.extend(chunk[: MAX_LOG_BYTES - len(log)])

        reader = asyncio.create_task(read_log())
        waiter = asyncio.ensure_future(process.wait())
        deadline = asyncio.get_running_loop().time() + self.limits.timeout_seconds
        timed_out = over_cap = False
        try:
            while True:
                done, _ = await asyncio.wait({waiter}, timeout=1)
                if done:
                    break
                if asyncio.get_running_loop().time() > deadline:
                    timed_out = True
                elif await asyncio.to_thread(folder_bytes, step.watch) > step.max_bytes:
                    over_cap = True
                if timed_out or over_cap:
                    await self._remove(name)
                    await waiter
                    break
        except BaseException:
            # Stop (task cancelled), or anything else: the container goes too.
            await asyncio.shield(self._remove(name))
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise
        finally:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(reader, timeout=5)
        return ContainerOutcome(
            exit_code=process.returncode if process.returncode is not None else -1,
            log=bytes(log),
            timed_out=timed_out,
            over_cap=over_cap,
        )

    async def remove_run(self, run_id: str) -> None:
        filters = [
            "--filter", f"label={RUN_LABEL}={run_id}",
            "--filter", f"label={INSTANCE_LABEL}={self.instance}",
        ]  # fmt: skip
        code, out, _ = await _docker("ps", "-aq", *filters)
        if code == 0 and (ids := out.split()):
            await _docker("rm", "-f", *ids)

    async def _remove(self, name: str) -> None:
        await _docker("rm", "-f", name)


def folder_bytes(root: Path) -> int:
    """Bytes in a folder's files, links not followed."""
    total = 0
    for folder, _, files in os.walk(root, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(Path(folder) / name).st_size
    return total


def _user() -> str:
    """Who steps run as. On native Linux Docker (CI), files a step writes are
    owned by that user on the host, so it's the host's own user there."""
    if sys.platform.startswith("linux") and hasattr(os, "getuid") and os.getuid() != 0:
        return f"{os.getuid()}:{os.getgid()}"
    # Never root, even when DataLab itself runs as root.
    return STEP_USER


def _csv_field(value: str) -> str:
    if any(c in value for c in ',"\n\r'):
        return '"' + value.replace('"', '""') + '"'
    return value


async def _docker(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
