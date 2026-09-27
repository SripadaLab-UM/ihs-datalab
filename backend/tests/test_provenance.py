"""Provenance: where each number in an answer appears, and how each output file was made."""

import json
from types import SimpleNamespace

import pytest

from datalab.sessions import provenance
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.provenance import (
    Version,
    answer_provenance,
    file_chain,
    files_named,
    output_evidence,
    turns_from_events,
)
from datalab.sessions.runtime import SessionRuntime
from datalab.sessions.tracing import Source, trace_sources

COMMAND = Source("command", "cmd-1")
QUERY = Source("query", "q_0001")


@pytest.fixture
def app(settings, catalog):
    from datalab.app import create_app
    from tests.conftest import FakeDatabase

    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


def test_how_was_this_made_over_http(app):
    """The router on a real app: a file, the turn that made it, and the query its script read."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from datalab.api.provenance import build_provenance_router

    services = app.state.services
    app.include_router(
        build_provenance_router(services.conversations, services.sessions, services.access_log)
    )
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        store = services.conversations
        store.append(cid, "user_message", {"text": "Plot mood."})
        store.append(cid, "command_started", {"id": "c1", "command": "python plot.py"})
        services.access_log.started(
            query_id="q_0001", session_id=cid, sql="SELECT 1 FROM dual", binds={}, tables=["X.T"]
        )
        services.access_log.finished(
            "q_0001", status="succeeded", row_count=1, result_path=Path("/data/oracle/q_0001.csv")
        )
        work = services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True, exist_ok=True)
        (work / "plot.py").write_text("read('/data/oracle/q_0001.csv'); save('outputs/mood.png')")
        (work / "outputs" / "mood.png").write_bytes(b"png")
        services.sessions.checkpoints(cid).take("After turn 1", turn=1)

        made = client.get(f"/api/conversations/{cid}/provenance/outputs/mood.png").json()
        missing = client.get(f"/api/conversations/{cid}/provenance/outputs/none.png").json()
        unsafe = client.get(f"/api/conversations/{cid}/provenance/..%2Fetc%2Fpasswd")
    assert made["turn"] == 1 and made["commands"][0]["via_script"] == "plot.py"
    assert made["queries"][0]["read_by"] == [{"kind": "script", "ref": "plot.py"}]
    assert missing["found"] is False
    assert unsafe.status_code == 404


def test_each_number_says_where_it_appears():
    evidence = [
        (COMMAND, "mean 7.214 h, 81 participants"),
        (QUERY, '{"query_id": "q_0001", "preview": [{"N": 81}]}'),
    ]
    claims = trace_sources("Mean sleep was 7.2 h in 81 participants (SD 1.3).", evidence)
    by_text = {c.text: c.sources for c in claims}
    assert by_text["7.2"] == (COMMAND,)  # rounding still counts
    assert by_text["81"] == (COMMAND, QUERY)
    assert by_text["1.3"] == ()  # appears nowhere: untraced


def test_a_number_lists_at_most_a_few_places():
    evidence = [(Source("command", f"c{i}"), "42.5") for i in range(10)]
    [claim] = trace_sources("It was 42.5.", evidence, limit=3)
    assert [s.ref for s in claim.sources] == ["c0", "c1", "c2"]


def test_an_answers_provenance_is_capped(monkeypatch):
    monkeypatch.setattr(provenance, "MAX_NUMBERS", 2)
    event = answer_provenance("Values 11.5, 12.5, and 13.5.", [(COMMAND, "11.5 12.5")], [])
    assert [n["text"] for n in event["numbers"]] == ["11.5", "12.5"]
    assert event["more_numbers"] == 1
    assert event["numbers"][0]["sources"] == [{"kind": "command", "ref": "cmd-1"}]


def test_the_files_an_answer_names():
    outputs = [
        "outputs/fig1.png",
        "outputs/sleep/table.csv",
        "outputs/mood/table.csv",
        "outputs/data.csv",
    ]
    answer = (
        "See ![](outputs/fig1.png) and outputs/sleep/table.csv. The file table.csv is in two "
        "places, so its name alone doesn't say which; a.csv is somewhere else."
    )
    assert files_named(answer, outputs) == ["outputs/fig1.png", "outputs/sleep/table.csv"]
    assert files_named("Written to data.csv.", outputs) == ["outputs/data.csv"]


def test_only_output_data_files_are_evidence():
    entries = {
        "outputs/table.csv": "a,b\n1,2",
        "outputs/report.md": "The mean was 7.2.",  # the agent's prose isn't evidence
        "analysis.csv": "9,9",  # not an output
    }
    evidence = output_evidence(entries, lambda entry, limit: entry.encode()[:limit])
    assert evidence == [(Source("file", "outputs/table.csv"), "a,b\n1,2")]


def test_the_runtime_keeps_where_each_piece_of_evidence_came_from():
    runtime = SimpleNamespace(_evidence=[], _sourced=[], _ran_commands=False, _reviewing=False)
    collect = SessionRuntime._collect_evidence
    collect(runtime, {"type": "commandExecution", "id": "cmd-7", "aggregatedOutput": "n = 81"})
    result = {
        "content": [{"type": "text", "text": json.dumps({"query_id": "q_0009", "row_count": 3})}]
    }
    collect(runtime, {"type": "mcpToolCall", "tool": "query", "result": result})
    collect(runtime, {"type": "mcpToolCall", "tool": "ask_research_helper", "result": result})
    assert [s for s, _ in runtime._sourced] == [
        Source("command", "cmd-7"),
        Source("query", "q_0009"),
    ]
    # A rigor review's commands aren't the turn's work.
    runtime._reviewing = True
    collect(runtime, {"type": "commandExecution", "id": "cmd-8", "aggregatedOutput": "n = 99"})
    assert len(runtime._sourced) == 2 and len(runtime._evidence) == 3


def event(type, created_at="2026-09-27T10:00:00", **data):
    return SimpleNamespace(type=type, created_at=created_at, data=data)


def test_turns_are_numbered_as_checkpoints_number_them():
    turns = turns_from_events(
        [
            event("title_changed", title="x"),
            event("user_message", "2026-09-27T10:00:00", text="first"),
            event("command_started", id="c1", command="python analysis.py"),
            event("command_output", id="c1", text="wrote outputs/fig1.png\n"),
            event("command_finished", id="c1", exit_code=0, output="wrote outputs/fig1.png\n"),
            event("files_changed", paths=["/work/analysis.py"]),
            event("review_started"),
            event("command_started", id="c9", command="cat outputs/fig1.png"),
            event("review_finished", status="completed"),
            event("user_message", "2026-09-27T11:00:00", text="second"),
        ]
    )
    assert sorted(turns) == [1, 2]
    first = turns[1]
    assert [c.id for c in first.commands] == ["c1"]  # not the review's
    assert first.commands[0].output == "wrote outputs/fig1.png\n"  # streamed, not doubled
    assert first.changed == ["analysis.py"] and first.ended_at == "2026-09-27T11:00:00"


# --- How a file was made, on real checkpoints --------------------------------


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    (work / "outputs").mkdir(parents=True)
    return work, Checkpoints(tmp_path / "checkpoints", work)


def versions_of(checkpoints):
    return [
        Version(c.number, c.turn, c.label, checkpoints.entries(c.number))
        for c in checkpoints.list()
    ]


def reader(checkpoints):
    import os

    def read(entry, limit):
        with os.fdopen(checkpoints.open_object(entry), "rb") as source:
            return source.read(limit)

    return read


def query(id, started_at, result, status="succeeded", tables=("IHS_2025.VW_DAILY_MOOD",)):
    return SimpleNamespace(
        id=id,
        started_at=started_at,
        status=status,
        tables=list(tables),
        row_count=26405,
        result_path=f"/data/oracle/{result}",
    )


def test_a_file_is_traced_to_its_turn_command_script_and_query(workspace):
    work, checkpoints = workspace
    (work / "analysis.py").write_text("import pandas\npandas.read_csv('/data/oracle/q_0001.csv')\n")
    (work / "outputs" / "fig1.png").write_bytes(b"first")
    checkpoints.take("After turn 1", turn=1)
    (work / "outputs" / "fig1.png").write_bytes(b"second")  # remade in turn 2
    (work / "analysis.py").write_text(
        "import pandas\npandas.read_csv('/data/oracle/q_0001.csv')\nsave('outputs/fig1.png')\n"
    )
    checkpoints.take("After turn 2", turn=2)
    checkpoints.take("After turn 3", turn=3)  # unchanged since
    turns = turns_from_events(
        [
            event("user_message", "2026-09-27T10:00:00"),
            event("user_message", "2026-09-27T11:00:00"),
            event("command_started", id="c1", command="ls /data/oracle"),
            event("command_finished", id="c1", exit_code=0, output="q_0001.csv\nSECRET ROW 1,2,3"),
            event("command_started", id="c2", command="python /work/analysis.py"),
            event("command_finished", id="c2", exit_code=0, output="done"),
            event("user_message", "2026-09-27T12:00:00"),
        ]
    )
    queries = [
        query("q_0001", "2026-09-27T10:30:00", "q_0001.csv"),  # an earlier turn, read by the script
        query("q_0002", "2026-09-27T11:10:00", "q_0002.csv"),  # this turn, not read
        query("q_0003", "2026-09-27T11:20:00", "q_0003.csv", status="rejected"),
    ]
    chain = file_chain(
        "/work/outputs/fig1.png", versions_of(checkpoints), turns, queries, reader(checkpoints)
    )
    assert (chain["turn"], chain["checkpoint"]) == (2, 2)
    assert chain["commands"][0] == {
        "id": "c2",
        "command": "python /work/analysis.py",
        "exit_code": 0,
        "names_file": False,  # the command doesn't name it; its script does
        "via_script": "analysis.py",
    }
    assert [s["path"] for s in chain["scripts"]] == ["analysis.py"]
    assert chain["scripts"][0]["names_file"] is True
    assert [q["id"] for q in chain["queries"]] == ["q_0001", "q_0002"]
    assert chain["queries"][0]["read_by"] == [{"kind": "script", "ref": "analysis.py"}]
    assert chain["queries"][1] == {**chain["queries"][1], "read_by": [], "in_turn": True}
    assert "No command names it, but one ran a script that does (analysis.py)." in chain["summary"]
    assert "DataLab doesn't see which command writes a file" in chain["summary"]
    # Nothing a command printed, or any row of data, is passed on.
    assert "SECRET ROW" not in json.dumps(chain)


def test_a_command_that_names_the_file_comes_first(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "table.csv").write_text("a\n1\n")
    checkpoints.take("After turn 1", turn=1)
    turns = turns_from_events(
        [
            event("user_message"),
            event("command_started", id="c1", command="echo hi"),
            event(
                "command_started",
                id="c2",
                command="Rscript -e 'write.csv(x, \"outputs/table.csv\")'",
            ),
        ]
    )
    chain = file_chain(
        "outputs/table.csv", versions_of(checkpoints), turns, [], reader(checkpoints)
    )
    assert [c["id"] for c in chain["commands"]] == ["c2", "c1"]
    assert (
        chain["commands"][0]["names_file"]
        and "One command in that turn names it." in chain["summary"]
    )


def test_a_file_no_turn_made_and_a_missing_file_say_so(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "old.csv").write_text("a\n")
    checkpoints.take("Restored to checkpoint 1")  # no turn: a restore
    versions = versions_of(checkpoints)
    restored = file_chain("outputs/old.csv", versions, {}, [], reader(checkpoints))
    assert restored["turn"] is None and "which no turn made" in restored["summary"]
    missing = file_chain("outputs/gone.csv", versions, {}, [], reader(checkpoints))
    assert missing["found"] is False


def test_a_long_turn_is_capped(workspace, monkeypatch):
    monkeypatch.setattr(provenance, "MAX_COMMANDS", 3)
    monkeypatch.setattr(provenance, "MAX_QUERIES", 2)
    work, checkpoints = workspace
    (work / "outputs" / "t.csv").write_text("a\n")
    checkpoints.take("After turn 1", turn=1)
    turns = turns_from_events(
        [event("user_message", "2026-09-27T10:00:00")]
        + [event("command_started", id=f"c{i}", command=f"step {i}") for i in range(10)]
    )
    queries = [query(f"q_{i:04}", f"2026-09-27T10:0{i}:00", f"q_{i:04}.csv") for i in range(5)]
    chain = file_chain(
        "outputs/t.csv", versions_of(checkpoints), turns, queries, reader(checkpoints)
    )
    assert (len(chain["commands"]), chain["more_commands"]) == (3, 7)
    assert (len(chain["queries"]), chain["more_queries"]) == (2, 3)
