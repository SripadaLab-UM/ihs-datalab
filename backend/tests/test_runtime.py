"""SessionRuntime against a fake Codex app-server (tests/fake_app_server.py).

These cover the paths that are easy to get wrong: Stop before the turn has
started, Codex crashing mid-turn, recovery after a crash, and a thread that
can't be resumed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from datalab.sessions.approvals import Approvals
from datalab.sessions.containers import SessionPaths
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.tokens import SessionTokens

FAKE_SERVER = Path(__file__).with_name("fake_app_server.py")


class FakeContainers:
    """Records what the runtime asks Docker to do; runs the fake app-server."""

    def __init__(self, log: Path, mode: str = "normal", start_delay: float = 0) -> None:
        self.log = log
        self.mode = mode
        self.start_delay = start_delay
        self.running = False
        self.tokens: list[str] = []
        self.stops = 0

    async def start(self, token: str) -> None:
        await asyncio.sleep(self.start_delay)
        self.tokens.append(token)
        self.running = True

    async def stop(self) -> None:
        self.stops += 1
        self.running = False

    async def stop_and_confirm(self) -> None:
        await self.stop()

    async def is_running(self) -> bool:
        return self.running

    async def kill_turn_processes(self) -> None:
        pass

    async def open_app_server(self) -> asyncio.subprocess.Process:
        env = {**os.environ, "FAKE_MODE": self.mode, "FAKE_LOG": str(self.log)}
        return await asyncio.create_subprocess_exec(
            sys.executable,
            str(FAKE_SERVER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

    def requests(self) -> list[str]:
        if not self.log.exists():
            return []
        return [json.loads(line)["method"] for line in self.log.read_text().splitlines()]


def make(
    tmp_path: Path,
    containers: FakeContainers,
    tokens: SessionTokens | None = None,
    approvals: Approvals | None = None,
    **options,
):
    events: list[tuple[str, dict]] = []

    async def emit(kind: str, data: dict) -> None:
        events.append((kind, data))

    runtime = SessionRuntime(
        "c_test",
        "data",
        SessionPaths(tmp_path / "session"),
        containers,  # type: ignore[arg-type]
        tokens or SessionTokens(),
        model="gpt-test",
        developer_instructions="Be brief.",
        tool_timeout_seconds=60,
        emit=emit,
        approvals=approvals,
        **options,
    )
    return runtime, events


async def test_a_turn_completes_and_config_is_outside_the_agent_folders(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl")
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    result = await runtime.send("hi")
    assert result.status == "completed"
    assert ("answer_delta", {"id": "m1", "text": "done"}) in events
    paths = runtime.paths
    assert paths.codex_config.exists()
    # DataLab never writes config into a folder the agent can change.
    assert not (paths.codex_home / "config.toml").exists()
    await runtime.close()


async def test_the_lab_skills_in_the_knowledge_base_copy_are_a_skill_root(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl")
    runtime, _ = make(tmp_path, containers)
    runtime.begin_turn()
    await runtime.send("hi")
    requests = containers.requests()
    # Before the thread starts, so its skills include them.
    assert requests.index("skills/extraRoots/set") < requests.index("thread/start")
    [sent] = [
        json.loads(line)["params"]
        for line in containers.log.read_text().splitlines()
        if json.loads(line)["method"] == "skills/extraRoots/set"
    ]
    assert sent == {"extraRoots": ["/work/kb/skills"]}
    await runtime.close()


async def test_a_modes_data_access_reaches_its_token_and_codex_config(tmp_path):
    """Knowledge writing: its token allows only its tools; Codex doesn't list the rest."""
    tokens = SessionTokens()
    containers = FakeContainers(tmp_path / "log.jsonl")
    runtime, _ = make(
        tmp_path,
        containers,
        tokens,
        tools=frozenset({"search_catalog"}),
        tools_off=("query", "propose_plan"),
    )
    runtime.begin_turn()
    await runtime.send("hi")
    access = tokens.resolve(containers.tokens[-1])
    assert access is not None and access.tools == {"search_catalog"}
    assert access.allows("search_catalog") and not access.allows("query")
    assert 'disabled_tools = ["query", "propose_plan"]' in runtime.paths.codex_config.read_text()
    await runtime.close()
    # By default, a data session queries, and every tool is listed.
    tokens = SessionTokens()
    containers = FakeContainers(tmp_path / "log2.jsonl")
    runtime, _ = make(tmp_path / "other", containers, tokens)
    runtime.begin_turn()
    await runtime.send("hi")
    access = tokens.resolve(containers.tokens[-1])
    assert access is not None and access.tools is None and access.allows("query")
    assert "disabled_tools" not in runtime.paths.codex_config.read_text()
    await runtime.close()


async def test_stop_while_starting_prevents_the_turn(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", start_delay=0.3)
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    sending = asyncio.create_task(runtime.send("do a lot of work"))
    await asyncio.sleep(0.05)  # still starting the container
    await runtime.stop_turn()
    result = await asyncio.wait_for(sending, 5)
    assert result.status == "interrupted"
    assert "turn/start" not in containers.requests()
    assert ("turn_finished", {"status": "interrupted"}) in events
    await runtime.close()


async def test_stop_during_a_turn_interrupts_it(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", mode="slow")
    runtime, _ = make(tmp_path, containers)
    runtime.begin_turn()
    sending = asyncio.create_task(runtime.send("long job"))
    for _ in range(100):
        if "turn/start" in containers.requests():
            break
        await asyncio.sleep(0.02)
    await runtime.stop_turn()
    result = await asyncio.wait_for(sending, 5)
    assert result.status == "interrupted"
    assert "turn/interrupt" in containers.requests()
    await runtime.close()


async def test_a_request_refused_while_stopping_leaves_the_turn_stopped_not_failed(tmp_path):
    """Stop is recorded before the interrupt reaches Codex. A model request
    sent in between is refused by the relay, and Codex ends the turn failed
    with its raw error: that's still the person's Stop, with no error shown
    (and so no Continue offered)."""
    containers = FakeContainers(tmp_path / "log.jsonl", mode="stop_race")
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    sending = asyncio.create_task(runtime.send("long job"))
    for _ in range(100):
        if "turn/start" in containers.requests():
            break
        await asyncio.sleep(0.02)
    await runtime.stop_turn()
    result = await asyncio.wait_for(sending, 5)
    assert (result.status, result.error) == ("interrupted", None)
    finished = [data for kind, data in events if kind == "turn_finished"]
    assert [data["status"] for data in finished] == ["interrupted"]
    assert not any(kind == "error" for kind, _ in events)
    assert "409" not in json.dumps(events)
    await runtime.close()


async def test_a_crash_mid_turn_ends_the_turn(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", mode="crash")
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    result = await asyncio.wait_for(runtime.send("hi"), 5)  # doesn't hang
    assert result.status == "failed"
    assert any(kind == "turn_finished" and data["status"] == "failed" for kind, data in events)
    assert not runtime.busy
    await runtime.close()


async def test_recovery_restarts_the_container_with_a_live_token(tmp_path):
    tokens = SessionTokens()
    containers = FakeContainers(tmp_path / "log.jsonl", mode="crash")
    runtime, _ = make(tmp_path, containers, tokens)
    runtime.begin_turn()
    await asyncio.wait_for(runtime.send("first"), 5)

    containers.mode = "normal"
    runtime.begin_turn()
    result = await asyncio.wait_for(runtime.send("second"), 5)
    assert result.status == "completed"
    # The container was restarted, not reused, and holds a token that still works.
    assert containers.stops >= 2
    assert len(containers.tokens) == 2
    assert tokens.resolve(containers.tokens[-1]) is not None
    assert tokens.resolve(containers.tokens[0]) is None
    await runtime.close()


async def test_a_failed_resume_is_visible(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl")
    runtime, _ = make(tmp_path, containers)
    runtime.begin_turn()
    await runtime.send("first")
    await runtime.close()

    # A new runtime for the same conversation, whose saved thread can't be resumed.
    containers.mode = "no_resume"
    again, events = make(tmp_path, containers)
    again.begin_turn()
    result = await again.send("second")
    assert result.status == "completed"
    assert any(kind == "notice" and "starting fresh" in data["text"] for kind, data in events)
    await again.close()


@pytest.fixture(autouse=True)
def _quiet(caplog):
    caplog.set_level("ERROR")


def test_a_planted_symlink_is_removed_not_followed(tmp_path):
    outside = tmp_path / "precious.txt"
    outside.write_text("keep me")
    paths = SessionPaths(tmp_path / "session")
    paths.create()
    (paths.codex_home / "config.toml").symlink_to(outside)
    paths.prepare_config_mountpoint()
    assert outside.read_text() == "keep me"
    target = paths.codex_home / "config.toml"
    assert target.is_file() and not target.is_symlink()


async def _wait_for_event(events, kind, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        found = [data for k, data in events if k == kind]
        if found:
            return found[-1]
        await asyncio.sleep(0.02)
    raise AssertionError(f"no {kind} event")


def helper_runtime(tmp_path, monkeypatch, mode="elicit"):
    approvals = Approvals()
    containers = FakeContainers(tmp_path / "log.jsonl", mode)
    runtime, events = make(tmp_path, containers, approvals=approvals)
    return runtime, events, approvals


async def test_codex_waits_for_the_persons_decision_on_the_host(tmp_path, monkeypatch):
    runtime, events, approvals = helper_runtime(tmp_path, monkeypatch)
    pending = approvals.open("c_test", "How do lme4 random slopes work?")
    monkeypatch.setenv("FAKE_APPROVAL", pending.id)
    runtime.begin_turn()
    turn = asyncio.create_task(runtime.send("look it up"))
    # The card appears once Codex forwards the request, with the host's text.
    card = await _wait_for_event(events, "approval_requested")
    assert card == {
        "id": pending.id,
        "kind": "research_helper",
        "question": "How do lme4 random slopes work?",
    }
    await asyncio.sleep(0.2)
    assert not turn.done()  # Codex is still waiting
    approvals.answer("c_test", pending.id, True, "Random slopes in lme4?")
    assert (await asyncio.wait_for(turn, 5)).status == "completed"
    assert ("answer_delta", {"id": "m2", "text": "accept"}) in events
    await runtime.close()


async def test_stop_withdraws_a_pending_question(tmp_path, monkeypatch):
    runtime, events, approvals = helper_runtime(tmp_path, monkeypatch)
    pending = approvals.open("c_test", "q")
    monkeypatch.setenv("FAKE_APPROVAL", pending.id)
    runtime.begin_turn()
    turn = asyncio.create_task(runtime.send("look it up"))
    await asyncio.sleep(0.3)
    await runtime.stop_turn()
    assert (await asyncio.wait_for(turn, 5)).status == "interrupted"
    assert (await _wait_for_event(events, "approval_withdrawn"))["id"] == pending.id
    assert pending.decision.result() == (False, "")
    with pytest.raises(KeyError):
        approvals.answer("c_test", pending.id, True, "too late")
    await runtime.close()


async def test_requests_that_arent_datalabs_own_are_declined(tmp_path, monkeypatch):
    runtime, events, approvals = helper_runtime(tmp_path, monkeypatch, mode="elicit_other")
    pending = approvals.open("c_test", "q")
    monkeypatch.setenv("FAKE_APPROVAL", pending.id)
    runtime.begin_turn()
    await asyncio.wait_for(runtime.send("look it up"), 5)
    assert ("answer_delta", {"id": "m2", "text": "decline"}) in events
    await runtime.close()


async def test_another_conversations_approval_is_declined(tmp_path, monkeypatch):
    runtime, events, approvals = helper_runtime(tmp_path, monkeypatch)
    elsewhere = approvals.open("c_other", "q")
    monkeypatch.setenv("FAKE_APPROVAL", elsewhere.id)
    runtime.begin_turn()
    await asyncio.wait_for(runtime.send("look it up"), 5)
    assert ("answer_delta", {"id": "m2", "text": "decline"}) in events
    assert not elsewhere.decision.done()
    await runtime.close()


async def test_a_request_of_the_wrong_kind_is_declined(tmp_path, monkeypatch):
    runtime, events, approvals = helper_runtime(tmp_path, monkeypatch)
    pending = approvals.open("c_test", "q")  # a research-helper question
    monkeypatch.setenv("FAKE_APPROVAL", pending.id)
    monkeypatch.setenv("FAKE_KIND", "analysis_plan")  # asked for as if it were a plan
    runtime.begin_turn()
    await asyncio.wait_for(runtime.send("look it up"), 5)
    assert ("answer_delta", {"id": "m2", "text": "decline"}) in events
    await runtime.close()


async def test_a_review_runs_on_the_thread_and_reports_its_findings(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl")
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    await runtime.send("hi")
    result = await asyncio.wait_for(runtime.review("check it"), 5)
    assert result.status == "completed"
    assert ("review", {"id": "r1", "text": "1. ok"}) in events
    assert "review/start" in containers.requests()
    await runtime.close()


async def test_stop_interrupts_a_review_by_the_id_of_the_turn_that_runs(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", "slow_review")
    runtime, _ = make(tmp_path, containers)
    runtime.begin_turn()
    review = asyncio.create_task(runtime.review("check it"))
    await asyncio.sleep(0.3)
    await runtime.stop_turn()
    assert (await asyncio.wait_for(review, 5)).status == "interrupted"
    await runtime.close()


async def test_a_stop_before_codex_names_the_running_review_still_stops_it(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", "late_review")
    runtime, _ = make(tmp_path, containers)
    runtime.begin_turn()
    review = asyncio.create_task(runtime.review("check it"))
    await asyncio.sleep(0.2)
    await runtime.stop_turn()
    assert (await asyncio.wait_for(review, 5)).status == "interrupted"
    await runtime.close()


async def test_another_threads_notifications_are_ignored(tmp_path):
    containers = FakeContainers(tmp_path / "log.jsonl", "other_thread")
    runtime, events = make(tmp_path, containers)
    runtime.begin_turn()
    result = await asyncio.wait_for(runtime.send("hi"), 5)
    assert result.status == "completed"
    assert not any("leak" in str(data) for _, data in events)
    assert [data.get("status") for kind, data in events if kind == "turn_finished"] == ["completed"]
    await runtime.close()


def test_tool_summaries_keep_metadata_and_never_rows():
    import json as _json

    from datalab.sessions.runtime import tool_summary

    def result(value):
        return {"content": [{"type": "text", "text": _json.dumps(value)}]}

    query = tool_summary(
        "query",
        "ihs-data",
        result(
            {
                "query_id": "q1",
                "row_count": 2,
                "columns": ["ID", "STEPS"],
                "result_file": "/data/oracle/q1.csv",
                "preview": [["SYN25-0001", 9000]],
                "tables": ["IHS_2025.X"],
            }
        ),
    )
    assert query == {
        "row_count": 2,
        "columns": ["ID", "STEPS"],
        "result_file": "/data/oracle/q1.csv",
        "tables": ["IHS_2025.X"],
        "warnings": [],
    }
    assert "SYN25-0001" not in _json.dumps(query)  # the rows stay out of the event log
    table = tool_summary(
        "describe_table",
        "ihs-data",
        result(
            {
                "table": "IHS_2025.X",
                "columns": [{"name": "STEPS", "type": "NUMBER", "comment": "<b>x</b>"}],
            }
        ),
    )
    assert table["columns"][0] == {"name": "STEPS", "type": "NUMBER", "comment": "<b>x</b>"}
    assert table["column_count"] == 1
    assert tool_summary("query", "someone-else", result({"row_count": 1})) is None
    assert (
        tool_summary("query", "ihs-data", {"content": [{"type": "text", "text": "not json"}]})
        is None
    )
