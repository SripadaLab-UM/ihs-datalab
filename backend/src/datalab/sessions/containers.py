"""Docker for one session: an internal network, a gateway, and the agent.

The agent container joins only the session's internal network, which has no
route out. Its one exit is the gateway, a stock nginx that forwards to the
model relay and the agent tools in DataLab on the host. See docs/SAFETY.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from datalab.sessions.tokens import SessionKind

log = logging.getLogger(__name__)

GATEWAY_IMAGE = "nginx@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"
# Research sessions only: a forward proxy to the internet (see squid.conf).
PROXY_IMAGE = "ubuntu/squid@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029"
_PROXY_URL = "http://proxy:3128"
# An unroutable address (TEST-NET-1): containers can resolve only names Docker
# knows (the gateway), and nothing else, even on older Docker versions.
_NO_DNS = "192.0.2.1"
_LABEL = "datalab.session"
# Which DataLab profile owns a container, so a practice instance's cleanup
# never touches a real instance's sessions (or the other way round).
_PROFILE_LABEL = "datalab.profile"

# Stops commands a turn left running after an interrupt. Codex 0.157.1 ends
# the turn but not the shell commands it started (found in the spike).
_KILL_TURN_PROCESSES = (
    "for a in $(pgrep -x codex); do for c in $(pgrep -P $a); do kill -9 -$c 2>/dev/null; done; done"
)


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionPaths:
    root: Path

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def codex_home(self) -> Path:
        return self.root / "codex-home"

    @property
    def oracle_results(self) -> Path:
        return self.root / "oracle"

    @property
    def checkpoints(self) -> Path:
        # Outside every folder the container can see.
        return self.root / "checkpoints"

    @property
    def gateway_conf(self) -> Path:
        return self.root / "gateway.conf"

    @property
    def squid_conf(self) -> Path:
        return self.root / "squid.conf"

    @property
    def codex_config(self) -> Path:
        # Kept outside the agent's folders and mounted read-only: DataLab
        # never writes into a folder the agent can change (it could plant a
        # symlink there to redirect the write), and the agent can't edit its
        # own configuration.
        return self.root / "codex-config.toml"

    def create(self) -> None:
        for folder in (self.work, self.work / "outputs", self.codex_home, self.oracle_results):
            folder.mkdir(parents=True, exist_ok=True)

    def prepare_config_mountpoint(self) -> None:
        """Make sure codex-home/config.toml is a plain file Docker can mount over.

        Docker Desktop can't create the mount point itself inside a bind
        mount. Only call this while the agent's container is stopped. If the
        agent left a symlink here, the link itself is removed (its target is
        never touched), and the placeholder is created without following links.
        """
        target = self.codex_home / "config.toml"
        if target.is_symlink() or (target.exists() and not target.is_file()):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if not target.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(target, flags, 0o644))


@dataclass(frozen=True)
class ContainerLimits:
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 1024


class SessionContainers:
    def __init__(
        self,
        session_id: str,
        kind: SessionKind,
        paths: SessionPaths,
        *,
        agent_image: str,
        host_port: int,
        profile: str,
        limits: ContainerLimits | None = None,
        # More `docker run` mount arguments, read when the agent starts: the
        # conversation's attached inputs, all read-only (see inputs.py).
        extra_mounts: Callable[[], list[str]] | None = None,
    ) -> None:
        self._extra_mounts = extra_mounts
        self.session_id = session_id
        self.kind: SessionKind = kind
        self.paths = paths
        self.agent_image = agent_image
        self.host_port = host_port
        self.limits = limits or ContainerLimits()
        self._labels = [
            "--label", f"{_LABEL}={session_id}",
            "--label", f"{_PROFILE_LABEL}={profile}",
        ]  # fmt: skip
        short = session_id.replace("_", "-")[-24:]
        self.network = f"datalab-{short}"
        self.gateway = f"datalab-{short}-gateway"
        self.agent = f"datalab-{short}-agent"
        self.proxy = f"datalab-{short}-proxy"

    async def start(self, token: str) -> None:
        """Create or restart the session's network and containers."""
        self.paths.create()
        self.paths.gateway_conf.write_text(render_gateway_conf(self.kind, self.host_port))
        if not await self._exists("network", self.network):
            await docker("network", "create", "--internal", *self._labels, self.network)
        await self._start_gateway()
        if self.kind == "research":
            await self._start_proxy()
        await self._start_agent(token)

    async def stop(self) -> None:
        """Stop the containers. The workspace and Codex home stay on disk."""
        for name in (self.agent, self.gateway, self.proxy):
            await docker("rm", "-f", name, check=False)

    async def remove(self) -> None:
        await self.stop()
        await docker("network", "rm", self.network, check=False)

    async def is_running(self) -> bool:
        """Running and not paused: a paused agent can't run Codex."""
        output = await docker(
            "inspect", "-f", "{{.State.Running}} {{.State.Paused}}", self.agent, check=False
        )
        return output.split() == ["true", "false"]

    async def stop_and_confirm(self) -> None:
        """Stop the containers, and fail unless the agent's is really gone.

        Fails closed: if Docker can't be asked, the container isn't known to
        be gone.
        """
        await self.stop()
        if await self._agent_state() is not None:
            raise DockerError("DataLab couldn't stop the agent's container.")

    async def pause(self) -> None:
        """Freeze everything in the agent's container, or confirm it isn't running.

        Raises DockerError unless nothing can run in it afterwards.
        """
        state = await self._agent_state()
        if state is None or state == "not running":
            return
        if state == "running":
            await docker("pause", self.agent)
        if await self._agent_state() != "paused":
            raise DockerError("DataLab couldn't pause the agent's container.")

    async def unpause(self) -> None:
        if await self._agent_state() == "paused":
            await docker("unpause", self.agent)

    async def _agent_state(self) -> str | None:
        """'running', 'paused', 'not running', or None if there's no container.

        Raises DockerError if Docker can't say.
        """
        code, out, err = await docker_status(
            "inspect", "-f", "{{.State.Running}} {{.State.Paused}}", self.agent
        )
        if code != 0:
            if "no such" in err.lower():
                return None
            raise DockerError(f"docker inspect failed: {err.strip()[:300]}")
        running, paused = [*out.split(), "", ""][:2]
        if paused == "true":
            return "paused"
        return "running" if running == "true" else "not running"

    async def open_app_server(self) -> asyncio.subprocess.Process:
        """Start `codex app-server` in the agent; talk to it over stdin/stdout."""
        return await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", self.agent, "codex", "app-server", "--strict-config",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024,
        )  # fmt: skip

    async def kill_turn_processes(self) -> None:
        await docker("exec", self.agent, "sh", "-c", _KILL_TURN_PROCESSES, check=False)

    async def _start_gateway(self) -> None:
        if await self._running(self.gateway):
            return
        await docker("rm", "-f", self.gateway, check=False)
        await docker(
            "run", "-d", "--name", self.gateway,
            *self._labels,
            "--add-host", "host.docker.internal:host-gateway",
            "--read-only", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/var/run",
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "NET_BIND_SERVICE",
            "--security-opt", "no-new-privileges",
            "--memory", "128m", "--pids-limit", "128",
            "-v", f"{self.paths.gateway_conf}:/etc/nginx/conf.d/default.conf:ro",
            GATEWAY_IMAGE,
        )  # fmt: skip
        await docker("network", "connect", "--alias", "gateway", self.network, self.gateway)

    async def _start_proxy(self) -> None:
        if await self._running(self.proxy):
            return
        await docker("rm", "-f", self.proxy, check=False)
        self.paths.squid_conf.write_text(
            resources.files(__package__).joinpath("squid.conf").read_text()
        )
        await docker(
            "run", "-d", "--name", self.proxy,
            *self._labels,
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE",
            "--security-opt", "no-new-privileges",
            "--memory", "256m", "--pids-limit", "256",
            "-v", f"{self.paths.squid_conf}:/etc/squid/squid.conf:ro",
            PROXY_IMAGE,
        )  # fmt: skip
        await docker("network", "connect", "--alias", "proxy", self.network, self.proxy)
        await self._wait_for_proxy()

    async def _wait_for_proxy(self, timeout: float = 20) -> None:
        """Squid takes a moment to start; don't hand the agent a dead proxy."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            logs = await docker("logs", self.proxy, check=False)
            if "Accepting HTTP Socket connections" in logs:
                return
            await asyncio.sleep(0.25)
        raise DockerError("The research session's internet proxy didn't start.")

    async def _start_agent(self, token: str) -> None:
        if await self._running(self.agent):
            return
        await docker("rm", "-f", self.agent, check=False)
        # The token goes in a private env file, not on a command line where
        # other programs on this computer could see it.
        self.paths.prepare_config_mountpoint()
        env_file = self.paths.root / ".agent.env"
        env_file.touch(mode=0o600)
        env = [f"DATALAB_SESSION_TOKEN={token}"]
        if self.kind == "research":
            # The internet goes through the proxy; the model goes to the gateway.
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                env.append(f"{name}={_PROXY_URL}")
            env += ["NO_PROXY=gateway,localhost", "no_proxy=gateway,localhost"]
        env_file.write_text("\n".join(env) + "\n")
        mounts = [
            "-v", f"{self.paths.work}:/work",
            "-v", f"{self.paths.codex_home}:/codex-home",
            "-v", f"{self.paths.codex_config}:/codex-home/config.toml:ro",
        ]  # fmt: skip
        if self.kind == "data":
            mounts += ["-v", f"{self.paths.oracle_results}:/data/oracle:ro"]
        if self._extra_mounts:
            # Checks the disk (and maybe slow cloud folders): not on the event loop.
            mounts += await asyncio.to_thread(self._extra_mounts)
        await docker(
            "run", "-d", "--name", self.agent,
            *self._labels,
            "--network", self.network,
            "--dns", _NO_DNS,
            "--init",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", self.limits.memory, "--cpus", self.limits.cpus,
            "--pids-limit", str(self.limits.pids),
            "--env-file", str(env_file),
            *mounts,
            self.agent_image,
        )  # fmt: skip
        env_file.unlink(missing_ok=True)

    async def _running(self, name: str) -> bool:
        output = await docker("inspect", "-f", "{{.State.Running}}", name, check=False)
        return output.strip() == "true"

    async def _exists(self, kind: str, name: str) -> bool:
        output = await docker(kind, "inspect", "-f", "{{.Name}}", name, check=False)
        return bool(output.strip())


def render_gateway_conf(kind: SessionKind, host_port: int) -> str:
    template = resources.files(__package__).joinpath("gateway.conf").read_text()
    mcp = ""
    if kind == "data":
        mcp = (
            "\n    # Agent tools (data sessions only).\n"
            "    location /mcp {\n"
            f"        proxy_pass http://host.docker.internal:{host_port}/mcp;\n"
            "    }\n"
        )
    return template.replace("{mcp_location}", mcp).replace("{port}", str(host_port))


async def docker_status(*args: str) -> tuple[int, str, str]:
    """Run docker; return its exit code, output, and error output."""
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def docker(*args: str, check: bool = True) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    if check and process.returncode != 0:
        # Never log arguments: they may include the session token.
        raise DockerError(f"docker {args[0]} failed: {err.decode(errors='replace').strip()[:500]}")
    return out.decode(errors="replace")


async def remove_all_session_containers(profile: str) -> None:
    """Clean up this profile's containers and networks left by a previous run."""
    owned = f"label={_PROFILE_LABEL}={profile}"
    ids = (await docker("ps", "-aq", "--filter", owned, check=False)).split()
    if ids:
        await docker("rm", "-f", *ids, check=False)
    networks = (await docker("network", "ls", "-q", "--filter", owned, check=False)).split()
    if networks:
        await docker("network", "rm", *networks, check=False)
