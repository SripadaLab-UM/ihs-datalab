"""The ihs-data tools, exercised over real HTTP with a real MCP client."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from datalab.app import create_app
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.oracle import QueryFailed, QueryTimedOut
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
    services,
    tmp_path: Path,
    kind: str = "data",
    tools: frozenset[str] | None = None,
    mode_label: str | None = None,
) -> str:
    access = SessionAccess(
        session_id="sess1",
        kind=kind,  # type: ignore[arg-type]
        results_dir=tmp_path / "oracle",
        tools=tools,
        mode_label=mode_label,
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
        "propose_sql",
        "suggest_kb_update",
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


def _with_survey_tables(catalog: Catalog) -> None:
    survey = [
        TableInfo(
            "IHS_2025",
            "VW_BASELINE_SURVEY",
            "VIEW",
            columns=[Column("PARTICIPANTIDENTIFIER", "VARCHAR2(15)"), Column("Bdate", "DATE")],
        ),
        TableInfo(
            "IHS_2025",
            "STG_SURVEYDICTIONARY",
            "TABLE",
            columns=[Column("SURVEYNAME", "VARCHAR2(2000)"), Column("QUESTIONTEXT", "CLOB")],
        ),
    ]
    catalog.replace(Catalog([*(catalog.get(n) for n in _SAMPLE_TABLES), *survey]))  # type: ignore[misc]


_SAMPLE_TABLES = (
    "IHS_2025.VFITBITDAILYDATA",
    "IHS_2024.VFITBITDAILYDATA",
    "IHS_2025.VW_DAILY_MOOD",
)


async def test_describe_marks_quoted_names_and_long_text(server, catalog, tmp_path):
    base_url, services, _ = server
    _with_survey_tables(catalog)
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        baseline = payload(
            await session.call_tool("describe_table", {"table": "IHS_2025.VW_BASELINE_SURVEY"})
        )
        dictionary = payload(
            await session.call_tool("describe_table", {"table": "IHS_2025.STG_SURVEYDICTIONARY"})
        )
        hits = payload(await session.call_tool("search_catalog", {"query": "bdate"}))
    plain, bdate = baseline["columns"]
    assert "write_as" not in plain and "sql_note" not in plain
    assert bdate["write_as"] == '"Bdate"'
    assert bdate["sql_note"] == 'quoted: exact case, always write it as "Bdate"'
    assert "TO_CHAR(SUBSTR(QUESTIONTEXT, 1, 1000))" in dictionary["columns"][1]["sql_note"]
    assert dictionary["columns"][1]["sql_note"].startswith("CLOB (long text)")
    assert hits[0]["matching_columns"] == ['"Bdate"']


async def test_who_refused_a_query_is_in_its_error(server, catalog, tmp_path, monkeypatch):
    base_url, services, database = server
    _with_survey_tables(catalog)

    def refuse(*args, **kwargs):
        raise QueryFailed("ORA-00932: inconsistent data types. Often a CLOB (long text) column")

    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        checked = await session.call_tool(
            "query", {"sql": "SELECT DISTINCT QUESTIONTEXT FROM IHS_2025.STG_SURVEYDICTIONARY"}
        )
        spelled = await session.call_tool(
            "query", {"sql": "SELECT Bdate FROM IHS_2025.VW_BASELINE_SURVEY"}
        )
        monkeypatch.setattr(database, "extract_to_csv", refuse)
        refused = await session.call_tool(
            "query", {"sql": "SELECT SURVEYNAME FROM IHS_2025.STG_SURVEYDICTIONARY"}
        )
    # (The MCP library puts "Error executing tool query: " in front.)
    assert checked.is_error and (
        "DataLab's SQL check refused this query, so it didn't run: QUESTIONTEXT is a CLOB column"
        in checked.content[0].text
    )
    assert (
        '"Bdate" is spelled with lower-case letters, so Oracle needs it in double quotes, '
        'exactly: "Bdate".\n[datalab-failure category=validation code=spelling query=q_'
    ) in spelled.content[0].text
    assert "[datalab-failure category=validation code=long_text query=q_" in checked.content[0].text
    assert (
        refused.is_error
        and (
            "The database refused this query: ORA-00932: inconsistent data types. "
            "Often a CLOB (long text) column\n[datalab-failure category=sql query=q_"
        )
        in refused.content[0].text
    )
    assert database.calls == []
    # The id is the Queries entry's.
    tagged = re.search(r"query=(q_\w+)\]", refused.content[0].text)
    assert tagged and services.access_log.for_session("sess1")[-1].id == tagged.group(1)


async def test_a_timed_out_query_says_what_to_do_next(server, tmp_path, monkeypatch):
    base_url, services, database = server

    def slow(*args, **kwargs):
        raise QueryTimedOut(
            "Oracle took longer than 120 s to answer one request (DPY-4024), so the query was "
            "cancelled.",
            reason="call_timeout",
            limit_seconds=120,
            code="DPY-4024",
        )

    monkeypatch.setattr(database, "extract_to_csv", slow)
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool(
            "query", {"sql": "SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA"}
        )
    text = result.content[0].text
    assert result.is_error and "The query timed out after 0 s: Oracle took longer than 120" in text
    assert "don't run the same query again" in text
    assert "[datalab-failure category=timeout code=call_timeout query=q_" in text
    logged = services.access_log.for_session("sess1")[-1]
    assert logged.status == "failed" and logged.elapsed_ms is not None


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
        "propose_sql": {"sql": "SELECT 1 FROM DUAL", "title": "t"},
        "suggest_kb_update": {
            "page": "sources/fitbit.md",
            "title": "t",
            "text": "x",
            "evidence_query_ids": ["q_1"],
            "reason": "r",
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


def test_a_tool_call_with_a_lone_surrogate_is_a_parse_error_not_a_crash(server, tmp_path):
    # The MCP SDK reads the JSON-RPC body strictly, so no tool sees one: the
    # call is refused as JSON-RPC's parse error, and the session goes on.
    base_url, services, _ = server
    headers = {
        "Authorization": f"Bearer {data_token(services, tmp_path)}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    url = f"{base_url}/mcp"

    def rpc(client: httpx.Client, method: str, params: dict | None, n: int | None):
        body: dict = {"jsonrpc": "2.0", "method": method}
        if n is not None:
            body["id"] = n
        if params is not None:
            body["params"] = params
        return client.post(url, content=json.dumps(body), headers=headers)

    def call(client: httpx.Client, name: str, arguments: dict, n: int) -> httpx.Response:
        return rpc(client, "tools/call", {"name": name, "arguments": arguments}, n)

    with httpx.Client(timeout=30) as client:
        hello = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }
        init = rpc(client, "initialize", hello, 1)
        assert init.status_code == 200
        headers["mcp-session-id"] = init.headers["mcp-session-id"]
        rpc(client, "notifications/initialized", None, None)
        free_text = {
            "search_catalog": {"query": "mood\ud800"},
            "describe_table": {"table": "IHS_2025.X\ud800"},
            "join_paths": {"first_table": "IHS_2025.\ud800", "second_table": "IHS_2025.X"},
            "find_concept": {"concept": "sleep\udfff"},
            "query": {"sql": "SELECT 1 FROM IHS_2025.VW_DAILY_MOOD", "binds": {"a": "\ud800"}},
            "check_workflow": {"text": "name: x\n# \ud800\n"},
            "propose_plan": {
                "analysis_type": "describe",
                "question_and_purpose": "q\ud800",
                "data_and_scope": "d",
                "checks_and_limitations": "c",
                "deliverables": "d",
            },
            "ask_research_helper": {"question": "what is \ud800"},
        }
        for n, (name, arguments) in enumerate(free_text.items(), start=2):
            refused = call(client, name, arguments, n)
            assert refused.status_code == 400, (name, refused.text)
            assert refused.json()["error"]["code"] == -32700, name
        # Written raw (bytes UTF-8 can't have), it's refused the same way.
        body = b'{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"find_concept",'
        body += b'"arguments":{"concept":"\xed\xa0\x80"}}}'
        assert client.post(url, headers=headers, content=body).status_code == 400
        assert call(client, "find_concept", {"concept": "mood"}, 21).status_code == 200


def test_check_workflow_says_text_that_isnt_utf8_is_a_problem():
    # The agent's JSON can carry a lone surrogate (the MCP client here can't
    # send one, so this is the tool's own function). Encoding it would raise.
    from datalab.data.agent_tools import _check_problems

    def check(text: str) -> None:
        raise AssertionError("not reached")

    [problem] = _check_problems(check, "name: x\n# \ud800\n")
    assert problem["line"] == 2 and "isn't text (U+D800" in problem["message"]
    [big] = _check_problems(check, "#" * 300_000)
    assert big["message"] == "The file is larger than 256 KB."


async def test_check_workflow_refuses_an_alias_bomb_at_once(server, tmp_path):
    import time

    from tests.test_safeyaml import BOMB

    base_url, services, _ = server
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        started = time.monotonic()
        found = payload(await session.call_tool("check_workflow", {"text": BOMB}))
    assert time.monotonic() - started < 5
    assert found["valid"] is False and found["problems"][0]["line"] == 2
    assert "anchors or aliases" in found["problems"][0]["message"]


async def test_suggest_kb_update_is_only_for_the_modes_that_name_it(server, tmp_path):
    """Analysis, Data extraction and Data engineering reach DataLab's checks
    (here: there's no turn running); every other mode is refused first."""
    from datalab.sessions.modes import MODES

    base_url, services, _ = server
    args = {
        "page": "sources/fitbit.md", "title": "Zero-step days", "text": "Treat 0 as missing.",
        "evidence_query_ids": ["q_1"], "reason": "r",
    }  # fmt: skip
    for mode_id, mode in MODES.items():
        if mode.kind != "data":
            continue
        token = data_token(services, tmp_path, tools=mode.allowed_tools)
        async with mcp_session(base_url, token) as session:
            result = await session.call_tool("suggest_kb_update", args)
        assert result.is_error, mode_id
        text = result.content[0].text
        if mode_id in ("analysis", "extraction", "engineering"):
            assert "during a turn" in text, (mode_id, text)
        else:
            assert "suggest_kb_update isn't available in this mode" in text, mode_id
    # A token without a list (all but the opt-in tools) doesn't have it either.
    async with mcp_session(base_url, data_token(services, tmp_path)) as session:
        result = await session.call_tool("suggest_kb_update", args)
    assert "isn't available in this mode" in result.content[0].text


async def test_remember_in_knowledge_turns_the_agents_update_into_a_draft_edit(
    settings, catalog, tmp_path
):
    """In a turn sent with Remember in Knowledge, the agent's suggestion needs
    no query and is accepted at once (here a stand-in for the Knowledge routes'
    accept, which tests/test_kb_edits.py covers); in any other turn it stays a
    card for the person to decide."""
    from datalab.sessions.modes import MODES

    app = create_app(
        settings, database=FakeDatabase(), catalog=catalog, manage_containers=False,
        protect_api=False,
    )  # fmt: skip
    services, suggestions = app.state.services, app.state.kb_suggestions
    suggestions.turn_running = lambda conversation_id: True
    accepted: list[str] = []

    def accept(conversation_id: str, suggestion_id: str) -> str:
        accepted.append(suggestion_id)
        return "edit_1"

    suggestions.accept = accept
    cid = services.conversations.create(kind="data", mode="analysis", title="t", model="m").id
    token = services.tokens.issue(
        SessionAccess(
            session_id=cid, kind="data", results_dir=tmp_path / "oracle",
            tools=MODES["analysis"].allowed_tools,
        )
    )  # fmt: skip
    args = {
        "page": "qc/zero-steps.md", "title": "Zero-step days", "text": "Treat 0 as missing.",
        "evidence_query_ids": [], "reason": "The person asked to keep it.",
    }  # fmt: skip
    with live_server(app) as base_url:
        services.conversations.append(cid, "user_message", {"text": "Why zeros?"})
        async with mcp_session(base_url, token) as session:
            refused = await session.call_tool("suggest_kb_update", args)
        assert refused.is_error and "needs evidence" in refused.content[0].text
        services.conversations.append(
            cid, "user_message", {"text": "Keep: zeros mean not synced.", "kb_request": True}
        )
        async with mcp_session(base_url, token) as session:
            made = payload(await session.call_tool("suggest_kb_update", args))
    assert made["status"] == "draft_edit" and "Save & share" in made["note"]
    assert accepted == [made["suggestion_id"]]


async def test_a_refused_tool_names_the_mode_it_isnt_in(server, tmp_path):
    """The refusal's words come from the conversation's mode, not Knowledge
    writing's for every mode."""
    from datalab.sessions.modes import MODES

    base_url, services, database = server
    workflows = MODES["workflows"]
    token = data_token(
        services, tmp_path, tools=workflows.allowed_tools, mode_label=workflows.label
    )
    plan = {
        "analysis_type": "describe_compare",
        "question_and_purpose": "q",
        "data_and_scope": "d",
        "checks_and_limitations": "c",
        "deliverables": "r",
    }
    async with mcp_session(base_url, token) as session:
        result = await session.call_tool("propose_plan", plan)
    assert result.is_error
    assert result.content[0].text.endswith("propose_plan isn't available in Workflow authoring.")
    assert "Knowledge writing" not in result.content[0].text
    knowledge = MODES["knowledge"]
    token = data_token(
        services, tmp_path, tools=knowledge.allowed_tools, mode_label=knowledge.label
    )
    async with mcp_session(base_url, token) as session:
        result = await session.call_tool("query", {"sql": "SELECT 1 FROM DUAL"})
    assert "query isn't available in Knowledge writing: it has the catalog tools only" in (
        result.content[0].text
    )
    assert database.calls == []
