"""Docker for one session: an internal network, a gateway, and the agent.

The agent container joins only the session's internal network, which has no
route out. Its one exit is the gateway, a stock nginx that forwards to the
model relay and the agent tools in DataLab on the host. See docs/SAFETY.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from datalab.config import Settings
from datalab.container_user import host_user
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
# Which DataLab instance (data folder) owns it, so a second instance of the
# same profile (the evaluation server, say) never removes another's sessions.
_INSTANCE_LABEL = "datalab.instance"


def instance_of(data_dir: Path) -> str:
    """A short, stable name for the DataLab instance using this data folder."""
    return hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:16]


def session_name(session_id: str, instance: str) -> str:
    """The session's network name, and the prefix of its containers' names.

    Names are unique per instance as well as per session: two DataLabs whose
    data folders hold the same conversation (a copied folder, say) must never
    share, reuse or replace each other's containers. Containers named before
    this (`datalab-<session>-agent`) are still found by their labels and
    cleaned up (see remove_all_session_containers).
    """
    if not instance:
        raise ValueError("A session's containers need their DataLab instance.")
    short = session_id.replace("_", "-")[-24:]
    return f"datalab-{instance[:8]}-{short}"


# Stops commands a turn left running after an interrupt. Codex 0.157.1 ends
# the turn but not the shell commands it started (found in the spike).
_KILL_TURN_PROCESSES = (
    "for a in $(pgrep -x codex); do for c in $(pgrep -P $a); do kill -9 -$c 2>/dev/null; done; done"
)


class DockerError(RuntimeError):
    pass


# Where a remote engine's gateway finds DataLab: the relay socket, mounted.
_RELAY_IN_GATEWAY = "/datalab/relay.sock"


@dataclass(frozen=True)
class Engine:
    """Where a conversation's containers run.

    This machine's Docker by default. Or a larger machine's container engine
    (high compute, `[server.high_compute]`), reached through a socket an SSH
    tunnel forwards here (`env`: its DOCKER_HOST), whose gateway reaches
    DataLab through a socket the tunnel forwards back (`relay_socket`, a path
    on that machine). Its Docker API is Podman's: GPU requests are dropped
    there, so a GPU agent is started through a second API (`gpu_env`) that
    gives every container it starts the GPU; and its container names don't
    resolve with `--dns` set, so the agent is told the gateway's address.
    """

    env: dict[str, str] = field(default_factory=dict)
    gpu_env: dict[str, str] | None = None
    relay_socket: str | None = None

    @property
    def remote(self) -> bool:
        return self.relay_socket is not None


LOCAL = Engine()


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
    def processed_files(self) -> Path:
        # Processed-data files DataLab fetched for the agent (dropbox/service.py),
        # mounted read-only at /data/dropbox when the conversation uses them.
        return self.root / "dropbox"

    @property
    def checkpoints(self) -> Path:
        # Outside every folder the container can see.
        return self.root / "checkpoints"

    @property
    def seeds(self) -> Path:
        # Which workspace seeds have run (see hooks.py), outside every mount.
        return self.root / "workspace-seeds.json"

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
        for folder in (
            self.work,
            self.work / "outputs",
            self.codex_home,
            self.oracle_results,
            self.processed_files,
        ):
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
class Mount:
    """A host file or folder mounted read-only into the agent's container.

    Always read-only: DataLab never trusts a folder the agent can write to
    (see docs/ARCHITECTURE.md §2), and /work is the agent's one such folder.
    """

    source: Path
    # Where it appears in the container: an absolute path.
    target: str

    def args(self) -> list[str]:
        """The `docker run` arguments for this mount."""
        # --mount (not -v) so a missing source is an error, never an empty
        # folder, and colons in Windows paths aren't misread. Values are
        # CSV-quoted, so commas and quotes in names are safe.
        fields = ["type=bind", f"source={self.source}", f"target={self.target}", "readonly"]
        return ["--mount", ",".join(_csv_field(f) for f in fields)]


def _csv_field(value: str) -> str:
    if any(c in value for c in ',"\n\r'):
        return '"' + value.replace('"', '""') + '"'
    return value


@dataclass(frozen=True)
class ContainerLimits:
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 1024


def limits_for(settings: Settings) -> ContainerLimits:
    """An agent's limits: on the lab's server, what `[server]` allows each
    (its memory is shared by everyone); on a person's computer, the default."""
    if settings.server is None:
        return ContainerLimits()
    return ContainerLimits(memory=settings.server.agent_memory, cpus=settings.server.agent_cpus)


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
        instance: str,
        limits: ContainerLimits | None = None,
        # More `docker run` mount arguments, read when the agent starts: the
        # conversation's attached inputs, all read-only (see inputs.py).
        extra_mounts: Callable[[], list[str]] | None = None,
        # Whether the conversation uses the processed data: its fetched files
        # are mounted read-only at /data/dropbox (data sessions only).
        processed: bool = False,
        engine: Engine = LOCAL,
        # More of the agent's environment ("NAME=value"): its limits, on the
        # lab's server (compute.limit_env).
        agent_env: tuple[str, ...] = (),
    ) -> None:
        self.engine = engine
        self.agent_env = agent_env
        self._extra_mounts = extra_mounts
        self.processed = processed
        self.session_id = session_id
        self.kind: SessionKind = kind
        self.paths = paths
        self.agent_image = agent_image
        self.host_port = host_port
        self.limits = limits or ContainerLimits()
        self._labels = [
            "--label", f"{_LABEL}={session_id}",
            "--label", f"{_PROFILE_LABEL}={profile}",
            "--label", f"{_INSTANCE_LABEL}={instance}",
        ]  # fmt: skip
        self.network = session_name(session_id, instance)
        self.gateway = f"{self.network}-gateway"
        self.agent = f"{self.network}-agent"
        self.proxy = f"{self.network}-proxy"

    async def start(self, token: str) -> None:
        """Create or restart the session's network and containers."""
        self.paths.create()
        # Files the Linux containers read get Unix line ends, on Windows too.
        _write_readable(
            self.paths.gateway_conf,
            render_gateway_conf(self.kind, self.host_port, relay=self.engine.remote),
        )
        await self._ensure_network()
        await self._start_gateway()
        if self.kind == "research":
            await self._start_proxy()
        await self._start_agent(token)

    async def stop(self) -> None:
        """Stop the containers and remove their network, which `start()` makes
        again. The workspace and Codex home stay on disk.

        The network goes too: Docker has room for only about 30, so leaving
        one behind per conversation stopped new ones starting after a while.
        """
        for name in (self.agent, self.gateway, self.proxy):
            await self._docker("rm", "-f", name, check=False)
        await self._docker("network", "rm", self.network, check=False)

    async def remove(self) -> None:
        await self.stop()

    async def is_running(self) -> bool:
        """Running and not paused: a paused agent can't run Codex."""
        output = await self._docker(
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
            await self._docker("pause", self.agent)
        if await self._agent_state() != "paused":
            raise DockerError("DataLab couldn't pause the agent's container.")

    async def unpause(self) -> None:
        if await self._agent_state() == "paused":
            await self._docker("unpause", self.agent)

    async def _agent_state(self) -> str | None:
        """'running', 'paused', 'not running', or None if there's no container.

        Raises DockerError if Docker can't say.
        """
        code, out, err = await docker_status(
            "inspect", "-f", "{{.State.Running}} {{.State.Paused}}", self.agent, env=self.engine.env
        )
        if code != 0:
            if "no such" in err.lower():
                return None
            raise docker_failure("inspect", err[:300])
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
            env=_with(self.engine.env),
        )  # fmt: skip

    async def kill_turn_processes(self) -> None:
        await self._docker("exec", self.agent, "sh", "-c", _KILL_TURN_PROCESSES, check=False)

    async def _docker(self, *args: str, check: bool = True) -> str:
        return await docker(*args, check=check, env=self.engine.env)

    async def _ensure_network(self) -> None:
        if not self.engine.remote:
            if not await self._exists("network", self.network):
                await docker("network", "create", "--internal", *self._labels, self.network)
            return
        # Docker's client can't read Podman's networks (`network inspect`
        # fails on them), so: create it, and an existing one is fine.
        code, _, err = await docker_status(
            "network", "create", "--internal", *self._labels, self.network, env=self.engine.env
        )
        if code != 0 and "exists" not in err.lower():
            raise docker_failure("network create", err)

    async def _start_gateway(self) -> None:
        if await self._running(self.gateway):
            return
        await self._docker("rm", "-f", self.gateway, check=False)
        if self.engine.remote:
            # The way to DataLab: the socket the tunnel forwards back.
            upstream = ["-v", f"{self.engine.relay_socket}:{_RELAY_IN_GATEWAY}"]
        else:
            upstream = ["--add-host", "host.docker.internal:host-gateway"]
        await self._docker(
            "run", "-d", "--name", self.gateway,
            *self._labels,
            *upstream,
            "--read-only", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/var/run",
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "NET_BIND_SERVICE",
            "--security-opt", "no-new-privileges",
            "--memory", "128m", "--pids-limit", "128",
            "-v", f"{self.paths.gateway_conf}:/etc/nginx/conf.d/default.conf:ro",
            GATEWAY_IMAGE,
        )  # fmt: skip
        await self._docker("network", "connect", "--alias", "gateway", self.network, self.gateway)

    async def _start_proxy(self) -> None:
        if await self._running(self.proxy):
            return
        await self._docker("rm", "-f", self.proxy, check=False)
        _write_readable(
            self.paths.squid_conf,
            resources.files(__package__).joinpath("squid.conf").read_text(encoding="utf-8"),
        )
        await self._docker(
            "run", "-d", "--name", self.proxy,
            *self._labels,
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE",
            "--security-opt", "no-new-privileges",
            "--memory", "256m", "--pids-limit", "256",
            "-v", f"{self.paths.squid_conf}:/etc/squid/squid.conf:ro",
            PROXY_IMAGE,
        )  # fmt: skip
        await self._docker("network", "connect", "--alias", "proxy", self.network, self.proxy)
        await self._wait_for_proxy()

    async def _wait_for_proxy(self, timeout: float = 20) -> None:
        """Squid takes a moment to start; don't hand the agent a dead proxy."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            logs = await self._docker("logs", self.proxy, check=False)
            if "Accepting HTTP Socket connections" in logs:
                return
            await asyncio.sleep(0.25)
        raise DockerError("The research session's internet proxy didn't start.")

    async def _start_agent(self, token: str) -> None:
        if await self._running(self.agent):
            return
        await self._docker("rm", "-f", self.agent, check=False)
        # The token goes in a private env file, not on a command line where
        # other programs on this computer could see it.
        self.paths.prepare_config_mountpoint()
        env_file = self.paths.root / ".agent.env"
        env_file.touch(mode=0o600)
        env = [f"DATALAB_SESSION_TOKEN={token}", *self.agent_env]
        user = host_user()
        if user:
            # Native Linux: the agent runs as DataLab's own user, the owner of
            # its folders (container_user.py). The image's home belongs to
            # its own user, so this one's is /tmp.
            env.append("HOME=/tmp")
        if self.kind == "research":
            # The internet goes through the proxy; the model goes to the gateway.
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                env.append(f"{name}={_PROXY_URL}")
            env += ["NO_PROXY=gateway,localhost", "no_proxy=gateway,localhost"]
        env_file.write_text("\n".join(env) + "\n", encoding="utf-8", newline="\n")
        mounts = [
            "-v", f"{self.paths.work}:/work",
            "-v", f"{self.paths.codex_home}:/codex-home",
            "-v", f"{self.paths.codex_config}:/codex-home/config.toml:ro",
        ]  # fmt: skip
        if self.kind == "data":
            mounts += ["-v", f"{self.paths.oracle_results}:/data/oracle:ro"]
            if self.processed:
                mounts += ["-v", f"{self.paths.processed_files}:/data/dropbox:ro"]
        if self._extra_mounts:
            # Checks the disk (and maybe slow cloud folders): not on the event loop.
            mounts += await asyncio.to_thread(self._extra_mounts)
        hosts: list[str] = []
        if self.engine.remote:
            # Podman resolves no container names once --dns is set (Docker's
            # resolver still does): the agent is told their addresses.
            for alias, name in (("gateway", self.gateway), ("proxy", self.proxy)):
                if name == self.proxy and self.kind != "research":
                    continue
                address = await self._address(name)
                if not address:
                    raise DockerError(f"DataLab couldn't find the session's {alias}.")
                hosts += ["--add-host", f"{alias}:{address}"]
        # A GPU agent starts through the engine's GPU API (see Engine).
        start_env = self.engine.gpu_env if self.engine.gpu_env is not None else self.engine.env
        await docker(
            "run", "-d", "--name", self.agent,
            *self._labels,
            "--network", self.network,
            "--dns", _NO_DNS,
            *hosts,
            "--init",
            *(["--user", user] if user else []),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", self.limits.memory, "--cpus", self.limits.cpus,
            "--pids-limit", str(self.limits.pids),
            "--env-file", str(env_file),
            *mounts,
            self.agent_image,
            env=start_env,
        )  # fmt: skip
        env_file.unlink(missing_ok=True)

    async def _address(self, name: str) -> str:
        template = f'{{{{(index .NetworkSettings.Networks "{self.network}").IPAddress}}}}'
        return (await self._docker("inspect", "-f", template, name, check=False)).strip()

    async def _running(self, name: str) -> bool:
        output = await self._docker("inspect", "-f", "{{.State.Running}}", name, check=False)
        return output.strip() == "true"

    async def _exists(self, kind: str, name: str) -> bool:
        output = await self._docker(kind, "inspect", "-f", "{{.Name}}", name, check=False)
        return bool(output.strip())


def _write_readable(path: Path, text: str) -> None:
    """A config file the gateway or proxy reads, with Unix line ends, and
    readable by everyone whatever DataLab's umask (0077 on the lab's server):
    on native Linux their processes can't override file permissions
    (--cap-drop ALL). Neither holds anything secret."""
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o644)


def render_gateway_conf(kind: SessionKind, host_port: int, *, relay: bool = False) -> str:
    """The gateway's nginx config. DataLab is at `host.docker.internal` on
    this machine, or (`relay`: a remote engine) through the relay socket."""
    template = resources.files(__package__).joinpath("gateway.conf").read_text(encoding="utf-8")
    upstream = f"unix:{_RELAY_IN_GATEWAY}:" if relay else f"host.docker.internal:{host_port}"
    mcp = ""
    if kind == "data":
        mcp = (
            "\n    # Agent tools (data sessions only).\n"
            "    location /mcp {\n"
            f"        proxy_pass http://{upstream}/mcp;\n"
            "    }\n"
        )
    return (
        template.replace("{mcp_location}", mcp)
        .replace("{upstream}", upstream)
        .replace("{port}", str(host_port))
    )


def _with(env: dict[str, str] | None) -> dict[str, str] | None:
    """This process's environment plus an engine's (its DOCKER_HOST); None,
    the environment as it is, for this machine's Docker."""
    return {**os.environ, **env} if env else None


async def docker_status(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run docker; return its exit code, output, and error output."""
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_with(env),
    )  # fmt: skip
    out, err = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def docker(*args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_with(env),
    )  # fmt: skip
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise docker_failure(args[0], err.decode(errors="replace"))
    return out.decode(errors="replace")


# Docker Desktop not running, as the docker command says it on Mac, Linux and
# Windows (its socket, or its named pipe, isn't there).
_ENGINE_DOWN = re.compile(
    r"cannot connect to the docker daemon|is the docker daemon running"
    r"|error during connect|docker_engine.*(cannot find|could not find)",
    re.IGNORECASE,
)
DOCKER_NOT_RUNNING = (
    "Docker Desktop isn't running. Open Docker Desktop, wait until it says "
    "'Engine running', then try again."
)


def docker_failure(command: str, stderr: str) -> DockerError:
    """The error for a failed docker command: in plain words when Docker
    Desktop isn't running, else Docker's own (never its arguments: they may
    include the session token)."""
    if _ENGINE_DOWN.search(stderr):
        return DockerError(DOCKER_NOT_RUNNING)
    return DockerError(f"docker {command} failed: {stderr.strip()[:500]}")


async def remove_all_session_containers(
    profile: str, data_dir: Path, engine: Engine = LOCAL
) -> None:
    """Clean up this instance's containers and networks left by a previous run.

    Only this instance's: another DataLab with the same profile (an
    evaluation server, say) may be running now. A container from before
    instances were labelled counts as this one's only if its session's folder
    is in this data folder.

    Found by label, never by name, so containers from before names carried
    the instance (`datalab-<session>-agent`) are cleaned up too.
    """
    instance = instance_of(data_dir)
    owned = f"label={_PROFILE_LABEL}={profile}"
    fields = f'{{{{.ID}}}}|{{{{.Label "{_INSTANCE_LABEL}"}}}}|{{{{.Label "{_LABEL}"}}}}'
    env = engine.env
    listed = await docker("ps", "-a", "--filter", owned, "--format", fields, check=False, env=env)
    if ids := _owned(listed, instance, data_dir):
        await docker("rm", "-f", *ids, check=False, env=env)
    if engine.remote:
        # Docker's client can't list Podman's networks: each session's is
        # named after it, so this instance's containers name the networks.
        mine = set(ids)
        sessions = {
            parts[2].strip()
            for line in listed.splitlines()
            if len(parts := line.split("|")) == 3 and parts[0].strip() in mine
        }
        names = sorted(session_name(s, instance) for s in sessions if s)
        if names:
            await docker("network", "rm", *names, check=False, env=env)
        return
    listed = await docker("network", "ls", "--filter", owned, "--format", fields, check=False)
    if networks := _owned(listed, instance, data_dir):
        await docker("network", "rm", *networks, check=False)


def _owned(listing: str, instance: str, data_dir: Path) -> list[str]:
    """IDs from `ID|INSTANCE|SESSION` lines that belong to this instance."""
    ids = []
    for line in listing.splitlines():
        object_id, label, session = ([*line.split("|"), "", ""])[:3]
        if not object_id.strip():
            continue
        label, session = label.strip(), session.strip()
        ours = label == instance or (
            not label
            and bool(session)
            and any(
                (data_dir / kind / session).exists() for kind in ("sessions", "helpers", "safety")
            )
        )
        if ours:
            ids.append(object_id.strip())
    return ids
