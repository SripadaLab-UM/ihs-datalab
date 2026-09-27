"""The ihs-data tools, exercised over real HTTP with a real MCP client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from datalab.app import create_app
from datalab.sessions.tokens import SessionAccess
from tests.conftest import FakeDatabase, live_server


@pytest.fixture
def server(settings, catalog) -> Iterator[tuple[str, object, FakeDatabase]]:
    database = FakeDatabase()
    app = create_app(
        settings, database=database, catalog=catalog, manage_containers=False, protect_api=False
    )
    with live_server(app) as base_url:
        yield base_url, app.state.services, database


@asynccontextmanager
async def mcp_session(base_url: str, token: str, elicitation_callback=None):
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with client, streamable_http_client(f"{base_url}/mcp", http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write, elicitation_callback=elicitation_callback) as session:
            await session.initialize()
            yield session


def data_token(
    services, tmp_path: Path, kind: str = "data", tools: frozenset[str] | None = None
) -> str:
    access = SessionAccess(
        session_id="sess1",
        kind=kind,  # type: ignore[arg-type]
        results_dir=tmp_path / "oracle",
        tools=tools,
    )
    return services.tokens.issue(access)


def payload(result) -> object:
    assert not result.is_error, result
    return json.loads(result.content[0].text)


async def test_tools_are_listed(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
    assert tools == {
        "search_catalog",
        "describe_table",
        "join_paths",
        "find_concept",
        "query",
        "ask_research_helper",
        "propose_plan",
        "check_workflow",
    }


async def test_search_and_describe(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        hits = payload(await session.call_tool("search_catalog", {"query": "mood"}))
        described = payload(
            await session.call_tool("describe_table", {"table": "IHS_2025.VFITBITDAILYDATA"})
        )
    assert hits[0]["table"] == "IHS_2025.VW_DAILY_MOOD"
    assert described["also_in"] == ["IHS_2024"]
    assert described["columns"][2]["name"] == "TRACKERSTEPS"


async def test_metadata_helpers(server, tmp_path):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        joins = payload(
            await session.call_tool(
                "join_paths",
                {
                    "first_table": "IHS_2025.VFITBITDAILYDATA",
                    "second_table": "IHS_2024.VFITBITDAILYDATA",
                },
            )
        )
        missing = await session.call_tool(
            "join_paths", {"first_table": "IHS_2025.NOPE", "second_table": "IHS_2025.VW_DAILY_MOOD"}
        )
        concept = payload(await session.call_tool("find_concept", {"concept": "mood"}))
    assert joins["shared_columns"][0]["column"] == "STUDY_PARTICIPANT_ID"
    assert any("different cohorts" in note for note in joins["notes"])
    assert missing.is_error
    assert concept["candidates"][0]["table"] == "IHS_2025.VW_DAILY_MOOD"


async def test_query_writes_result_to_the_session_folder(server, tmp_path):
    base_url, services, database = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = payload(
            await session.call_tool(
                "query",
                {"sql": "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA"},
            )
        )
    assert result["row_count"] == 2
    assert result["result_file"].startswith("/data/oracle/q_")
    assert (tmp_path / "oracle" / Path(result["result_file"]).name).exists()
    assert services.access_log.for_session("sess1")[0].status == "succeeded"
    assert len(database.calls) == 1


async def test_rejected_sql_is_a_tool_error(server, tmp_path):
    base_url, services, database = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("query", {"sql": "DELETE FROM IHS_2025.T"})
    assert result.is_error
    assert "Only SELECT" in result.content[0].text
    assert database.calls == []


async def test_a_catalog_only_session_gets_the_catalog_tools_and_nothing_else(server, tmp_path):
    """Knowledge writing: the catalog tools work; every other tool is refused
    by DataLab, before anything reaches the database, the access log, the
    plan desk or the research helper."""
    from datalab.sessions.modes import CATALOG_TOOLS, DATA_TOOLS, MODES

    base_url, services, database = server
    token = data_token(services, tmp_path, tools=MODES["knowledge"].tools)
    calls = {
        "query": {"sql": "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA"},
        "check_workflow": {"text": "name: x\n"},
        "ask_research_helper": {"question": "What is a mixed model?"},
        "propose_plan": {
            "analysis_type": "describe_compare",
            "question_and_purpose": "q",
            "data_and_scope": "d",
            "checks_and_limitations": "c",
            "deliverables": "r",
        },
    }
    assert set(calls) == set(DATA_TOOLS) - CATALOG_TOOLS
    async with mcp_session(base_url, token) as session:
        hits = payload(await session.call_tool("search_catalog", {"query": "mood"}))
        described = payload(
            await session.call_tool("describe_table", {"table": "IHS_2025.VFITBITDAILYDATA"})
        )
        refused = {name: await session.call_tool(name, args) for name, args in calls.items()}
    assert hits[0]["table"] == "IHS_2025.VW_DAILY_MOOD" and described["columns"]
    for name, result in refused.items():
        assert result.is_error, name
        assert f"{name} isn't available in this mode" in result.content[0].text
    assert database.calls == []
    assert services.access_log.for_session("sess1") == []


async def test_the_modes_tool_list_names_every_tool(server, tmp_path):
    from datalab.sessions.modes import DATA_TOOLS

    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
    assert tools == set(DATA_TOOLS)


async def test_check_workflow_runs_datalabs_own_check(server, tmp_path):
    from tests.test_workflow_runner import WEEKLY

    base_url, services, database = server
    broken = WEEKLY.replace("inputs: { raw: extract }", "inputs: { raw: check_summary }")
    aliased = "name: x\nsteps: &s []\nmore: *s\n"
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        good = payload(await session.call_tool("check_workflow", {"text": WEEKLY}))
        bad = payload(await session.call_tool("check_workflow", {"text": broken}))
        alias = payload(await session.call_tool("check_workflow", {"text": aliased}))
        big = payload(await session.call_tool("check_workflow", {"text": "#" * 300_000}))
        yaml_error = payload(await session.call_tool("check_workflow", {"text": "a: [\n"}))
    assert good == {"valid": True, "problems": [], "note": good["note"]}
    assert bad["valid"] is False
    problem = next(p for p in bad["problems"] if p["where"] == "steps[2].inputs.raw")
    assert problem["message"] == "'check_summary' isn't an earlier step."
    assert (
        problem["line"]
        == WEEKLY.splitlines().index(
            next(line for line in WEEKLY.splitlines() if "inputs: { raw: extract }" in line)
        )
        + 1
    )
    assert alias["valid"] is False and "anchors or aliases" in alias["problems"][0]["message"]
    assert alias["problems"][0]["line"] == 2
    assert big["valid"] is False and "larger than" in big["problems"][0]["message"]
    assert yaml_error["valid"] is False
    assert "isn't valid YAML" in yaml_error["problems"][0]["message"]
    # A check reads nothing and runs nothing.
    assert database.calls == []


def test_requests_without_a_data_session_token_are_refused(server, tmp_path):
    base_url, services, _ = server
    research = data_token(services, tmp_path, kind="research")
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {research}"}):
        response = httpx.post(f"{base_url}/mcp", headers=headers, json={})
        assert response.status_code == 401


def test_revoked_tokens_stop_working(server, tmp_path):
    base_url, services, _ = server
    token = data_token(services, tmp_path)
    services.tokens.revoke_session("sess1")
    response = httpx.post(f"{base_url}/mcp", headers={"Authorization": f"Bearer {token}"}, json={})
    assert response.status_code == 401


def test_health(server):
    base_url, _, _ = server
    body = httpx.get(f"{base_url}/api/health").json()
    assert body["status"] == "ok" and body["catalog_tables"] == 3


async def test_a_question_asked_around_codex_is_never_shown_or_sent(server, tmp_path, monkeypatch):
    """Code in the container holds the session token and can call the tool and
    answer the approval request itself. No card appears (only Codex's own
    requests are shown), its "accept" counts for nothing, and nothing is sent."""
    from mcp.types import ElicitResult

    from datalab.sessions import helper as helper_module

    base_url, services, _ = server
    conversation = services.conversations.create(kind="data", mode="analysis", title="t", model="m")
    asked: list[str] = []

    async def fake_run(self, question):
        asked.append(question)
        return helper_module.HelperAnswer("answered", "x")

    monkeypatch.setattr(helper_module.ResearchHelper, "_run", fake_run)
    monkeypatch.setattr(helper_module, "APPROVAL_WAIT_SECONDS", 0.5)
    services.sessions.helper.turn_running = lambda conversation_id: True  # as if mid-turn

    async def rogue(context, params):
        return ElicitResult(action="accept", content={"question": "SYN25-0001 slept 5h"})

    token = services.tokens.issue(
        SessionAccess(session_id=conversation.id, kind="data", results_dir=tmp_path / "oracle")
    )
    async with mcp_session(base_url, token, elicitation_callback=rogue) as session:
        result = payload(
            await session.call_tool("ask_research_helper", {"question": "lme4 random slopes?"})
        )
    assert result["status"] == "declined" and asked == []
    events = services.conversations.events_of_types_after(
        conversation.id, 0, ("approval_requested", "helper_answered")
    )
    assert events == []


async def test_questions_are_refused_between_turns(server, tmp_path):
    base_url, services, _ = server
    conversation = services.conversations.create(kind="data", mode="analysis", title="t", model="m")
    token = services.tokens.issue(
        SessionAccess(session_id=conversation.id, kind="data", results_dir=tmp_path / "oracle")
    )
    async with mcp_session(base_url, token) as session:
        result = payload(await session.call_tool("ask_research_helper", {"question": "q?"}))
    assert result["status"] == "declined" and "during a turn" in result["note"]


async def test_hidden_characters_are_refused(server, tmp_path):
    base_url, services, _ = server
    hidden = "lme4?" + "".join(chr(0xE0000 + ord(c)) for c in "SYN25-0001")
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("ask_research_helper", {"question": hidden})
    assert result.is_error and "hidden" in result.content[0].text


PLAN_ARGS = {
    "analysis_type": "data_quality",
    "question_and_purpose": "How complete is Garmin coverage by month?",
    "data_and_scope": "IHS_2025 interns with a Garmin, July to June.",
    "checks_and_limitations": "Days with a sync but no data can't be told from no wear.",
    "deliverables": "A table of coverage by month.",
    "rationale": "The question is about how complete the data are.",
    "sections": {
        "expected_structure": "One row per intern-day.",
        "assessment": "Share of expected days present, by month.",
        "flag_handling": "Counted and reported; nothing dropped.",
    },
    "additional_sections": [{"title": "Devices", "content": "Garmin only."}],
}


async def test_a_well_formed_plan_gets_as_far_as_the_turn_check(server, tmp_path):
    """Between turns a plan isn't shown, but only after it's been checked."""
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        result = payload(await session.call_tool("propose_plan", PLAN_ARGS))
    assert result["status"] == "not approved" and "during a turn" in result["note"]
    description = tools["propose_plan"].description or ""
    assert "data_quality (Data quality or coverage)" in description


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"analysis_type": "causal"}, "analysis_type must be one of"),
        ({"sections": {"expected_structure": "x"}}, "Assessment method"),
        ({"sections": {**PLAN_ARGS["sections"], "question_and_purpose": "x"}}, "twice"),
        ({"deliverables": "Report\u200b"}, "hidden"),
    ],
)
async def test_a_plan_that_isnt_well_formed_is_refused_with_a_reason(
    server, tmp_path, change, says
):
    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("propose_plan", {**PLAN_ARGS, **change})
    assert result.is_error and says in result.content[0].text


async def test_a_revision_must_name_an_approved_plan(server, tmp_path):
    base_url, services, _ = server
    args = {**PLAN_ARGS, "revises": "pl_000000000000", "revision_reason": "Add a device."}
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("propose_plan", args)
    assert result.is_error and "no approved plan to revise" in result.content[0].text


async def test_an_approved_plan_carries_datalabs_record_of_what_ran_before_it(server, tmp_path):
    """End to end through the tool: a query returns data, a plan is proposed
    mid-turn and approved on the host, and the frozen plan says what ran."""
    from mcp.types import ElicitResult

    base_url, services, _ = server
    conversation = services.conversations.create(kind="data", mode="analysis", title="t", model="m")

    class Running:
        def done(self):
            return False

    services.sessions._turns[conversation.id] = Running()  # as if mid-turn

    async def person_approves(context, params):
        approval = json.loads(params.message)["approval"]
        services.sessions._approvals.get(approval, conversation.id).shown = True
        services.sessions.answer_approval(conversation.id, approval, True)
        return ElicitResult(action="accept", content={})

    token = services.tokens.issue(
        SessionAccess(session_id=conversation.id, kind="data", results_dir=tmp_path / "oracle")
    )
    async with mcp_session(base_url, token, elicitation_callback=person_approves) as session:
        payload(await session.call_tool("query", {"sql": "SELECT * FROM IHS_2025.VW_DAILY_MOOD"}))
        result = payload(await session.call_tool("propose_plan", PLAN_ARGS))
    services.sessions._turns.pop(conversation.id)
    assert result["status"] == "approved"
    assert result["plan"]["proposed_after"] == {
        "queries": 1,
        "tables": ["IHS_2025.VW_DAILY_MOOD"],
        "more_tables": 0,
    }


async def test_a_large_file_with_thousands_of_problems_is_checked_quickly(server, tmp_path):
    """8,000 steps naming a missing pipeline: 8,001 problems. At most 50 are
    reported, their lines found from one read of the file, off the event loop.
    (8,002 with the note on the missing pipeline.yaml, given once.)"""
    import time

    base_url, services, _ = server
    steps = "".join(f"  - id: s{i}\n    pipeline: p\n" for i in range(8000))
    big = "name: t\nreads: []\nsteps:\n" + steps
    assert 200_000 < len(big.encode()) < 256 * 1024
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        started = time.monotonic()
        found = payload(await session.call_tool("check_workflow", {"text": big}))
        took = time.monotonic() - started
        # The server answered other calls meanwhile, and still does.
        hits = payload(await session.call_tool("search_catalog", {"query": "mood"}))
    assert took < 15, took
    assert found["valid"] is False and len(found["problems"]) == 51
    assert found["problems"][0]["line"] == 5  # steps[0].pipeline
    assert found["problems"][-1]["message"].startswith(
        "And 7952 more problems"
    )  # 8,001 and the pipeline's own
    assert hits


async def test_a_slow_check_times_out_and_keeps_its_slot_until_it_ends(monkeypatch):
    import asyncio
    import threading

    from datalab.data import agent_tools

    monkeypatch.setattr(agent_tools, "CHECK_SECONDS", 0.2)
    release = threading.Event()
    slots = asyncio.Semaphore(1)

    def slow(text: str) -> None:
        release.wait(5)

    first = await agent_tools._checked_in_thread(slow, "name: x\n", slots)
    assert "took over" in first[0]["message"]
    # Still running: the next check waits for the slot, and says so.
    second = await agent_tools._checked_in_thread(slow, "name: x\n", slots)
    assert "busy checking" in second[0]["message"]
    release.set()
    for _ in range(50):
        if not slots.locked():
            break
        await asyncio.sleep(0.05)
    assert not slots.locked()
    assert await agent_tools._checked_in_thread(lambda _t: None, "name: x\n", slots) == []


async def test_a_pipeline_name_never_reads_or_names_anything_outside_the_package(server, tmp_path):
    """The agent names the pipeline. A path in its place must not make the host
    read a pipeline.yaml elsewhere (and report its keys), nor say where
    anything is, nor whether it exists."""
    base_url, services, _ = server
    outside = tmp_path / "outside" / "evil"
    outside.mkdir(parents=True)
    (outside / "pipeline.yaml").write_text("secret_key_name: 1\nanother_secret: 2\n")
    package = services.settings.data_dir / "workflows-local" / "ihsDataR"
    (package / "inst" / "pipelines").mkdir(parents=True)
    names = [
        str(outside),
        "x/../../../../../../" + str(outside).lstrip("/"),
        str(tmp_path / "outside" / "nope"),
        "weekly'.",
    ]
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        results = [
            payload(
                await session.call_tool(
                    "check_workflow",
                    {"text": f"name: t\nreads: []\nsteps:\n  - id: p\n    pipeline: {name!r}\n"},
                )
            )
            for name in names
        ]
    for name, result in zip(names, results, strict=True):
        assert result["valid"] is False
        # Only the agent's own name comes back, as it wrote it.
        text = json.dumps(result).replace(json.dumps(repr(name))[1:-1], "NAME")
        assert "secret_key_name" not in text and "another_secret" not in text
        assert str(tmp_path) not in text
        assert "isn't there" not in text  # nothing said about whether it exists
        assert "Pipeline names are lower case letters" in text
