"""Docker for one session: an internal network, a gateway, and the agent.

The agent container joins only the session's internal network, which has no
route out. Its one exit is the gateway, a stock nginx that forwards to the
model relay and the agent tools in DataLab on the host. See docs/SAFETY.md.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from datalab.sessions.tokens import SessionKind

log = logging.getLogger(__name__)

GATEWAY_IMAGE = "nginx@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"
# An unroutable address (TEST-NET-1): containers can resolve only names Docker
# knows (the gateway), and nothing else, even on older Docker versions.
_NO_DNS = "192.0.2.1"
_LABEL = "datalab.session"

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
    def gateway_conf(self) -> Path:
        return self.root / "gateway.conf"

    def create(self) -> None:
        for folder in (self.work, self.work / "outputs", self.codex_home, self.oracle_results):
            folder.mkdir(parents=True, exist_ok=True)


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
        limits: ContainerLimits | None = None,
    ) -> None:
        self.session_id = session_id
        self.kind: SessionKind = kind
        self.paths = paths
        self.agent_image = agent_image
        self.host_port = host_port
        self.limits = limits or ContainerLimits()
        short = session_id.replace("_", "-")[-24:]
        self.network = f"datalab-{short}"
        self.gateway = f"datalab-{short}-gateway"
        self.agent = f"datalab-{short}-agent"

    async def start(self, token: str) -> None:
        """Create or restart the session's network and containers."""
        self.paths.create()
        self.paths.gateway_conf.write_text(render_gateway_conf(self.kind, self.host_port))
        if not await self._exists("network", self.network):
            await docker(
                "network",
                "create",
                "--internal",
                "--label",
                f"{_LABEL}={self.session_id}",
                self.network,
            )
        await self._start_gateway()
        await self._start_agent(token)

    async def stop(self) -> None:
        """Stop the containers. The workspace and Codex home stay on disk."""
        for name in (self.agent, self.gateway):
            await docker("rm", "-f", name, check=False)

    async def remove(self) -> None:
        await self.stop()
        await docker("network", "rm", self.network, check=False)

    async def is_running(self) -> bool:
        output = await docker("inspect", "-f", "{{.State.Running}}", self.agent, check=False)
        return output.strip() == "true"

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
            "--label", f"{_LABEL}={self.session_id}",
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

    async def _start_agent(self, token: str) -> None:
        if await self._running(self.agent):
            return
        await docker("rm", "-f", self.agent, check=False)
        # The token goes in a private env file, not on a command line where
        # other programs on this computer could see it.
        env_file = self.paths.root / ".agent.env"
        env_file.touch(mode=0o600)
        env_file.write_text(f"DATALAB_SESSION_TOKEN={token}\n")
        mounts = [
            "-v", f"{self.paths.work}:/work",
            "-v", f"{self.paths.codex_home}:/codex-home",
        ]  # fmt: skip
        if self.kind == "data":
            mounts += ["-v", f"{self.paths.oracle_results}:/data/oracle:ro"]
        await docker(
            "run", "-d", "--name", self.agent,
            "--label", f"{_LABEL}={self.session_id}",
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


async def docker(*args: str, check: bool = True) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    if check and process.returncode != 0:
        # Never log arguments: they may include the session token.
        raise DockerError(f"docker {args[0]} failed: {err.decode(errors='replace').strip()[:500]}")
    return out.decode(errors="replace")


async def remove_all_session_containers() -> None:
    """Clean up containers and networks a previous DataLab run left behind."""
    ids = (await docker("ps", "-aq", "--filter", f"label={_LABEL}", check=False)).split()
    if ids:
        await docker("rm", "-f", *ids, check=False)
    networks = (
        await docker("network", "ls", "-q", "--filter", f"label={_LABEL}", check=False)
    ).split()
    if networks:
        await docker("network", "rm", *networks, check=False)
