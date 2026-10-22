"""Where a server DataLab listens (`deployment = "server"`).

On a person's computer DataLab answers one port on 127.0.0.1, for its own
page and for its sessions' gateways (host.docker.internal). On the lab's
server it answers two places, each only what belongs there:

- a Unix socket for Apache ([server] socket), which nobody else on the
  server can open: the web UI and its API, never the agent-only routes;
- a TCP port on the Docker bridge, where the session gateways reach the
  host (host.docker.internal is that bridge's address on Linux): only the
  model relay and the agent tools, never the UI or API.

So nothing on the bridge (a container, another app's) can reach anyone's
conversations, and nothing coming through Apache can reach the relay.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# What the session gateways ask for (sessions/gateway.conf).
AGENT_PATHS = ("/relay/", "/mcp")
_GATEWAY = "{{range .IPAM.Config}}{{.Gateway}}{{end}}"


class ListenerSplit:
    """Each listener answers only its own routes (see the module's note).
    The gateways' listener is told by its port: `bridge_port`."""

    def __init__(self, app: ASGIApp, bridge_port: int) -> None:
        self._app = app
        self._bridge_port = bridge_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            # uvicorn gives a TCP listener as (host, port), a Unix socket as (path, None).
            server = scope.get("server") or (None, None)
            from_bridge = server[1] == self._bridge_port
            for_agents = scope["path"].startswith(AGENT_PATHS)
            if from_bridge != for_agents:
                await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def bridge_address() -> str:
    """The Docker bridge's address on this host: what host.docker.internal
    (`host-gateway`) means to a container here."""
    try:
        found = subprocess.run(
            ["docker", "network", "inspect", "bridge", "--format", _GATEWAY],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Couldn't ask Docker for its bridge's address ({error}).") from None
    if not found:
        raise RuntimeError("Docker's bridge network has no gateway address.")
    return found


def unix_listener(path: str, group: str | None) -> socket.socket:
    """A listening Unix socket at `path`, readable and writable by its owner
    and `group` (Apache's) only."""
    target = Path(path)
    if target.is_socket():
        target.unlink()  # left by a DataLab that didn't stop cleanly
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)  # never open to everyone, even for a moment
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    if group:
        shutil.chown(path, group=group)
    os.chmod(path, 0o660)
    sock.listen(128)
    return sock


def tcp_listener(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock
