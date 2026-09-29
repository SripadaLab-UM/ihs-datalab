"""Ctrl-C on `datalab serve`: one line and exit code 0, not a traceback."""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from datalab import cli

# A real uvicorn server run the way `_serve` runs DataLab's: uvicorn catches
# Ctrl-C, shuts the app down (its lifespan's shutdown runs), then re-raises
# it as KeyboardInterrupt.
SCRIPT = """
import sys, uvicorn
from datalab import cli

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                print("app shut down", flush=True)
                await send({"type": "lifespan.shutdown.complete"})
                return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]),
                                       log_level="warning"))
def stop_hourly():
    print("hourly check stopped", flush=True)

sys.exit(cli._run_until_stopped(server.run, on_exit=stop_hourly, forced=lambda: server.force_exit))
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT to a child process is POSIX-only")
def test_ctrl_c_stops_with_one_line_and_exit_code_0():
    port = free_port()
    child = subprocess.Popen(
        [sys.executable, "-c", SCRIPT, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
                break
            except OSError:
                if time.monotonic() > deadline or child.poll() is not None:
                    raise
                time.sleep(0.1)
        child.send_signal(signal.SIGINT)
        out, _ = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == 0, out
    assert "Traceback" not in out and "KeyboardInterrupt" not in out
    assert out.strip().splitlines()[-3:] == [
        "app shut down",
        "hourly check stopped",
        "DataLab stopped.",
    ]


def test_a_quit_without_ctrl_c_says_nothing_and_still_stops_the_hourly_check(capsys):
    stopped = []
    assert cli._run_until_stopped(lambda: None, on_exit=lambda: stopped.append(1)) == 0
    assert stopped == [1] and capsys.readouterr().out == ""


def test_another_failure_is_not_hidden():
    stopped = []

    def broken() -> None:
        raise RuntimeError("port in use")

    with pytest.raises(RuntimeError):
        cli._run_until_stopped(broken, on_exit=lambda: stopped.append(1))
    assert stopped == [1]


def test_a_forced_stop_says_it_didnt_tidy_up(capsys):
    # A second Ctrl-C during uvicorn's graceful wait sets force_exit, and the
    # app's shutdown (sessions, containers) is skipped.
    def interrupted() -> None:
        raise KeyboardInterrupt

    stopped = []
    code = cli._run_until_stopped(
        interrupted, on_exit=lambda: stopped.append(1), forced=lambda: True
    )
    assert code == 0 and stopped == [1]
    assert capsys.readouterr().out == (
        "DataLab stopped without tidying up; it cleans up when it next starts.\n"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT to a child process is POSIX-only")
def test_a_second_ctrl_c_during_the_graceful_wait_says_so():
    """A request that never ends (a browser tab's event stream) keeps uvicorn
    waiting; a second Ctrl-C forces it out."""
    port = free_port()
    hanging = SCRIPT.replace(
        '    await send({"type": "http.response.start", "status": 200, "headers": []})',
        "    import asyncio\n"
        '    await send({"type": "http.response.start", "status": 200, "headers": []})\n'
        '    await send({"type": "http.response.body", "body": b"x", "more_body": True})\n'
        "    await asyncio.sleep(3600)",
    ).replace('log_level="warning"', 'log_level="warning", timeout_graceful_shutdown=30')
    child = subprocess.Popen(
        [sys.executable, "-c", hanging, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    stream = None
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                stream = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
                stream.read(1)
                break
            except OSError:
                if time.monotonic() > deadline or child.poll() is not None:
                    raise
                time.sleep(0.1)
        child.send_signal(signal.SIGINT)
        time.sleep(1)
        assert child.poll() is None  # still waiting for the open request
        child.send_signal(signal.SIGINT)
        out, _ = child.communicate(timeout=20)
    finally:
        if stream is not None:
            stream.close()
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == 0, out
    assert "Traceback" not in out
    assert out.strip().splitlines()[-1] == (
        "DataLab stopped without tidying up; it cleans up when it next starts."
    )
