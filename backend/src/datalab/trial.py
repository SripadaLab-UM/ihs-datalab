"""`datalab try`: one question in a fresh session, printed to the terminal.

A development aid for exercising the whole path (containers, gateway, relay,
Codex, agent tools) before the web UI exists.
"""

from __future__ import annotations

import asyncio
import secrets
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


async def run_trial(settings: Settings, question: str, image: str, research: bool) -> int:
    app = create_app(settings)
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


async def run_safety_check(settings: Settings) -> int:
    """Start DataLab in this process, run the Safety check, print the results."""
    app = create_app(settings)
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
    print("\nAll checks passed." if report.passed else "\nSOME CHECKS FAILED.")
    return 0 if report.passed else 1
