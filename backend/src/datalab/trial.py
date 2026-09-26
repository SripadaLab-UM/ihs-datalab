"""`datalab try`: one question in a fresh session, printed to the terminal.

A development aid for exercising the whole path (containers, gateway, relay,
Codex, agent tools) before the web UI exists.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import sys
from typing import Any

import uvicorn

from datalab.app import create_app
from datalab.config import Settings
from datalab.sessions.containers import SessionContainers, SessionPaths
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.tokens import SessionKind

INSTRUCTIONS = (
    "You are helping a researcher with the Intern Health Study. Be concise. "
    "Use the ihs-data tools for database questions."
)


def refuse_if_running(settings: Settings) -> None:
    """Exit if DataLab is already running for this profile.

    Starting a second copy would clean up the running one's session
    containers at startup, and then fail anyway because its port is taken.
    """
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", settings.port)) == 0:
            sys.exit(
                f"DataLab ({settings.profile}) is already running on port {settings.port}. "
                "Use it (Settings & Safety has the Safety check), or stop it first."
            )


async def run_trial(settings: Settings, question: str, image: str, research: bool) -> int:
    refuse_if_running(settings)
    # This process's containers are cleaned up below; never touch others.
    app = create_app(settings, manage_containers=False)
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    session_id = f"try_{secrets.token_hex(4)}"
    kind: SessionKind = "research" if research else "data"
    paths = SessionPaths(settings.data_dir / "sessions" / session_id)
    runtime = SessionRuntime(
        session_id,
        kind,
        paths,
        SessionContainers(
            session_id,
            kind,
            paths,
            agent_image=image,
            host_port=settings.port,
            profile=settings.profile,
        ),
        app.state.services.tokens,
        model=settings.default_model,
        developer_instructions=INSTRUCTIONS,
        tool_timeout_seconds=int(settings.limits.deadline_seconds) + 60,
        emit=_print_event,
    )
    print(f"session {session_id} ({kind}); workspace {paths.work}", file=sys.stderr)
    try:
        result = await runtime.send(question)
        print(
            f"\n[turn {result.status}{': ' + result.error if result.error else ''}]",
            file=sys.stderr,
        )
        return 0 if result.status == "completed" else 1
    finally:
        await runtime.close()
        await runtime.containers.remove()
        server.should_exit = True
        await serving


async def _print_event(kind: str, data: dict[str, Any]) -> None:
    if kind == "answer_delta":
        print(data["text"], end="", flush=True)
    elif kind == "reasoning_delta":
        print(f"\033[2m{data['text']}\033[0m", end="", flush=True, file=sys.stderr)
    elif kind == "command_started":
        print(f"\n\033[36m$ {data.get('command')}\033[0m", file=sys.stderr)
    elif kind == "tool_call":
        print(
            f"\n\033[35m[{data.get('server')}.{data.get('tool')} {data.get('status')}]\033[0m",
            file=sys.stderr,
        )
    elif kind == "error":
        print(f"\n\033[31merror: {data.get('message')}\033[0m", file=sys.stderr)


async def run_safety_check(settings: Settings, *, strict: bool = False) -> int:
    """Start DataLab in this process, run the Safety check, print the results.

    In strict mode (CI), a required check that couldn't be verified fails.
    """
    refuse_if_running(settings)
    app = create_app(settings, manage_containers=False)
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        report = await app.state.safety.run()
    finally:
        server.should_exit = True
        await serving
    symbols = {"pass": "\033[32m✓\033[0m", "fail": "\033[31m✗\033[0m", "skip": "\033[33m-\033[0m"}
    for result in report.results:
        print(f"{symbols[result.status]} {result.label}")
        if result.detail:
            print(f"    {result.detail}")
    passed = report.passed_strict if strict else report.passed
    if passed:
        print("\nAll checks passed.")
    elif report.passed:
        print("\nNo failures, but some required checks couldn't be verified (strict mode).")
    else:
        print("\nSOME CHECKS FAILED.")
    return 0 if passed else 1
