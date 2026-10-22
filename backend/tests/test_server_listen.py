"""A server DataLab's two listeners each answer only their own routes."""

import os
import stat
import tempfile

import pytest

from datalab.server_listen import ListenerSplit, unix_listener

APACHE = ("/run/datalab/web.sock", None)  # uvicorn's scope["server"] for a Unix socket
BRIDGE = ("172.17.0.1", 8765)  # and for a TCP listener


async def ask(server, path):
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    statuses = []

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "path": path, "server": server, "method": "GET", "headers": []}
    await ListenerSplit(app, bridge_port=8765)(scope, receive, send)
    return statuses[0], bool(reached)


@pytest.mark.parametrize(
    ("server", "path", "answered"),
    [
        (APACHE, "/api/conversations", True),
        (APACHE, "/workspace", True),
        (APACHE, "/relay/v1/responses", False),  # agents only
        (APACHE, "/mcp", False),
        (BRIDGE, "/relay/v1/responses", True),
        (BRIDGE, "/mcp", True),
        (BRIDGE, "/api/conversations", False),  # never anyone's conversations
        (BRIDGE, "/", False),
        (BRIDGE, "/sign-in", False),
        (("127.0.0.1", 80), "/api/conversations", True),  # not the gateways' port
    ],
)
async def test_each_listener_answers_only_its_own_routes(server, path, answered):
    status, reached = await ask(server, path)
    assert reached is answered
    assert status == (200 if answered else 404)


def test_apaches_socket_isnt_open_to_everyone():
    # A short folder: a Unix socket's path is limited to about 100 characters.
    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "web.sock")
    sock = unix_listener(path, group=None)
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o660
        # A socket left by a DataLab that didn't stop cleanly is replaced.
        sock.close()
        unix_listener(path, group=None).close()
    finally:
        sock.close()
