"""propose_sql: the SQL Playground chat's proposed query, checked and recorded, never run."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from datalab.app import create_app
from datalab.data.sql_drafts import EVENT, DraftInvalid, ProposedBind
from datalab.sessions.modes import MODES
from datalab.sessions.tokens import SessionAccess
from tests.conftest import FakeDatabase, live_server
from tests.test_agent_tools import mcp_session, payload

STEPS = (
    "SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA\n"
    "WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')\n"
    "  AND RECORD_DATE < TO_DATE(:end_date, 'YYYY-MM-DD')"
)
MARCH = [
    {"name": "start_date", "value": "2025-03-01", "type": "date"},
    {"name": "end_date", "value": "2025-04-01", "type": "date"},
]


@pytest.fixture
def server(settings, catalog) -> Iterator[tuple[str, object, FakeDatabase, object, str]]:
    database = FakeDatabase()
    app = create_app(
        settings, database=database, catalog=catalog, manage_containers=False, protect_api=False
    )
    services = app.state.services
    drafts = app.state.sql_drafts
    drafts.turn_running = lambda conversation_id: True
    conversation = services.conversations.create(kind="data", mode="sql", title="SQL", model="m").id
    with live_server(app) as base_url:
        yield base_url, services, database, drafts, conversation


def token(services, tmp_path, conversation: str, mode: str = "sql") -> str:
    return services.tokens.issue(
        SessionAccess(
            session_id=conversation,
            kind="data",
            results_dir=tmp_path / "oracle",
            tools=MODES[mode].allowed_tools,
        )
    )


def ask(services, conversation: str, text: str, **extra) -> None:
    services.conversations.append(conversation, "user_message", {"text": text, **extra})


def finish(services, conversation: str, status: str = "completed") -> None:
    services.conversations.append(conversation, "turn_finished", {"status": status})
    services.conversations.append(conversation, "turn_done", {})


def proposals(base_url: str, conversation: str) -> list[dict]:
    response = httpx.get(f"{base_url}/api/sql/proposals/{conversation}")
    response.raise_for_status()
    return response.json()


async def test_a_proposal_is_checked_and_recorded_and_never_run(server, tmp_path):
    base_url, services, database, _, conversation = server
    editor = "\n\nThe query in the SQL editor:\n```sql\nSELECT 1 FROM DUAL\n```"
    ask(services, conversation, "Daily Fitbit steps for 2025 in March" + editor)
    async with mcp_session(base_url, token(services, tmp_path, conversation)) as session:
        described = await session.call_tool(
            "describe_table", {"table": "IHS_2025.VFITBITDAILYDATA"}
        )
        assert not described.is_error
        services.conversations.append(
            conversation,
            "tool_call",
            {
                "server": "ihs-data",
                "tool": "describe_table",
                "arguments": {"table": "IHS_2025.VFITBITDAILYDATA"},
            },
        )
        services.conversations.append(
            conversation, "command_started", {"command": "cat /work/kb/tables/fitbit.md"}
        )
        result = payload(
            await session.call_tool(
                "propose_sql",
                {
                    "sql": STEPS + ";",
                    "title": "Daily Fitbit steps, March 2025",
                    "binds": MARCH,
                    "assumptions": ["Steps are TRACKERSTEPS", "  "],
                    "tables": ["IHS_2025.VFITBITDAILYDATA"],
                    "knowledge": ["tables/fitbit"],
                },
            )
        )
    assert result["status"] == "proposed" and result["proposal_id"].startswith("sp_")
    assert result["tables"] == ["IHS_2025.VFITBITDAILYDATA"]
    assert "hasn't run" in result["note"]
    # Nothing ran: no database call, and nothing in the conversation's Queries.
    assert database.calls == []
    assert services.access_log.for_session(conversation) == []
    # Offered only once the turn has finished well.
    [running] = proposals(base_url, conversation)
    assert (running["turn_status"], running["turn_done"]) == ("running", False)
    finish(services, conversation)
    [shown] = proposals(base_url, conversation)
    assert shown["turn_status"] == "completed" and shown["turn_done"]
    assert shown["turn"] == 1 and shown["conversation_id"] == conversation
    assert shown["request"] == "Daily Fitbit steps for 2025 in March"
    assert shown["sql"] == STEPS  # the trailing semicolon dropped, as the check does
    assert shown["binds"] == MARCH
    assert shown["assumptions"] == ["Steps are TRACKERSTEPS"]
    assert shown["tables"] == ["IHS_2025.VFITBITDAILYDATA"]
    assert shown["knowledge"] == ["tables/fitbit"]
    assert shown["tables_described"] == ["IHS_2025.VFITBITDAILYDATA"]
    assert shown["kb_read"] == ["/work/kb/tables/fitbit.md"]
    assert shown["queries"] == []


async def test_the_latest_proposal_of_a_turn_wins_and_a_follow_up_is_a_new_one(server, tmp_path):
    base_url, services, database, _, conversation = server
    ask(services, conversation, "Daily steps in March")
    async with mcp_session(base_url, token(services, tmp_path, conversation)) as session:
        for title in ("First try", "Second try"):
            payload(
                await session.call_tool(
                    "propose_sql", {"sql": STEPS, "title": title, "binds": MARCH}
                )
            )
        # A query the agent ran in the turn is listed with the proposal.
        payload(
            await session.call_tool(
                "query", {"sql": "SELECT COUNT(*) FROM IHS_2025.VFITBITDAILYDATA"}
            )
        )
        finish(services, conversation)
        ask(services, conversation, "limit this to April 1-15")
        april = [
            {"name": "start_date", "value": "2025-04-01", "type": "date"},
            {"name": "end_date", "value": "2025-04-16", "type": "date"},
        ]
        payload(
            await session.call_tool(
                "propose_sql", {"sql": STEPS, "title": "April 1-15", "binds": april}
            )
        )
    finish(services, conversation)
    first, second = proposals(base_url, conversation)
    assert (first["turn"], first["title"]) == (1, "Second try")
    assert [q["tables"] for q in first["queries"]] == [["IHS_2025.VFITBITDAILYDATA"]]
    assert (second["turn"], second["title"], second["request"]) == (
        2,
        "April 1-15",
        "limit this to April 1-15",
    )
    assert second["binds"][0]["value"] == "2025-04-01"
    assert second["queries"] == []
    assert services.conversations.count(conversation, EVENT) == 3
    assert len(database.calls) == 1  # the count only


async def test_refused_sql_is_a_clean_tool_error_and_nothing_is_recorded(server, tmp_path):
    base_url, services, database, _, conversation = server
    ask(services, conversation, "delete everything")
    cases = {
        "DELETE FROM IHS_2025.VFITBITDAILYDATA": "Only SELECT",
        "SELECT NOPE FROM IHS_2025.VFITBITDAILYDATA": "refused this query",
        "SELECT * FROM SYS.USER$": "refused this query",
    }
    async with mcp_session(base_url, token(services, tmp_path, conversation)) as session:
        results = {
            sql: await session.call_tool("propose_sql", {"sql": sql, "title": "t"}) for sql in cases
        }
        missing_bind = await session.call_tool(
            "propose_sql", {"sql": STEPS, "title": "t", "binds": MARCH[:1]}
        )
        extra_bind = await session.call_tool(
            "propose_sql",
            {"sql": STEPS, "title": "t", "binds": [*MARCH, {"name": "x", "value": 1}]},
        )
        bad_date = await session.call_tool(
            "propose_sql",
            {
                "sql": STEPS,
                "title": "t",
                "binds": [{**MARCH[0], "value": "March 1"}, MARCH[1]],
            },
        )
        no_title = await session.call_tool(
            "propose_sql", {"sql": STEPS, "title": "  ", "binds": MARCH}
        )
    for sql, words in cases.items():
        assert results[sql].is_error, sql
        text = results[sql].content[0].text
        assert "The SQL check refused this query, so it wasn't proposed" in text
        assert words in text and "Traceback" not in text
    assert missing_bind.is_error and ":end_date" in missing_bind.content[0].text
    assert extra_bind.is_error and "doesn't use :x" in extra_bind.content[0].text
    assert bad_date.is_error and "YYYY-MM-DD" in bad_date.content[0].text
    assert no_title.is_error and "title" in no_title.content[0].text
    assert services.conversations.count(conversation, EVENT) == 0
    assert database.calls == []


async def test_only_the_sql_mode_may_propose_and_only_during_a_turn(server, tmp_path):
    base_url, services, _, drafts, conversation = server
    ask(services, conversation, "steps")
    args = {"sql": STEPS, "title": "t", "binds": MARCH}
    for mode in ("extraction", "analysis", "workflows", "knowledge"):
        async with mcp_session(base_url, token(services, tmp_path, conversation, mode)) as s:
            refused = await s.call_tool("propose_sql", args)
        assert refused.is_error, mode
        assert "propose_sql isn't available in this mode" in refused.content[0].text
    drafts.turn_running = lambda conversation_id: False
    async with mcp_session(base_url, token(services, tmp_path, conversation)) as session:
        idle = await session.call_tool("propose_sql", args)
    assert idle.is_error and "during a turn" in idle.content[0].text
    assert services.conversations.count(conversation, EVENT) == 0


def test_turn_status_and_continue(settings, catalog):
    """A stopped or failed turn's proposal says so (the editor ignores it), and
    Continue keeps the question it picks up."""
    app = create_app(settings, catalog=catalog, manage_containers=False, protect_api=False)
    services, drafts = app.state.services, app.state.sql_drafts
    drafts.turn_running = lambda conversation_id: True
    conversation = services.conversations.create(kind="data", mode="sql", title="SQL", model="m").id

    def propose(title: str) -> None:
        drafts.propose(
            conversation,
            sql=STEPS,
            title=title,
            binds=[ProposedBind(b["name"], b["value"], "date") for b in MARCH],
        )

    ask(services, conversation, "steps in March")
    propose("stopped")
    finish(services, conversation, "interrupted")
    ask(services, conversation, "Continue", continues=True)
    propose("continued")
    finish(services, conversation, "failed")
    stopped, continued = drafts.proposals(conversation)
    assert (stopped.status, stopped.done) == ("interrupted", True)
    assert (continued.status, continued.request) == ("failed", "steps in March")
    with pytest.raises(DraftInvalid, match="number"):
        drafts.propose(
            conversation,
            sql="SELECT TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA WHERE TRACKERSTEPS > :n",
            title="t",
            binds=[ProposedBind("n", "many", "number")],
        )
    drafts.propose(
        conversation,
        sql="SELECT TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA WHERE TRACKERSTEPS > :n",
        title="t",
        binds=[ProposedBind(":N", "1000", "number")],
    )
    assert drafts.proposals(conversation)[-1].proposal["binds"] == [
        {"name": "n", "value": 1000, "type": "number"}
    ]
