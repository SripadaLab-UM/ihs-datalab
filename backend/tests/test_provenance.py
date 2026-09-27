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
    one_turn,
    output_evidence,
    record_turn,
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

    services = app.state.services  # the app registers the router itself
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


def test_the_first_places_in_evidence_order_are_kept_even_in_a_crowded_window():
    # From the review of PR #7: a query first, holding 100.4, then more commands
    # holding 99.6 than a window is searched through. "100" matches both.
    evidence = [(Source("query", "q_first"), "100.4")]
    evidence += [(Source("command", f"c{i}"), "99.6") for i in range(12_000)]
    [claim] = trace_sources("It was 100.", evidence, limit=3)
    assert [s.ref for s in claim.sources] == ["q_first", "c0", "c1"]


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


def chain_of(path, checkpoints, turns, queries=(), *, loads=None):
    """file_chain on real checkpoints, as the router calls it. `loads` records
    which checkpoints' files were read."""
    versions = [Version(c.number, c.turn, c.label, c.review) for c in checkpoints.list()]

    def entries(number):
        if loads is not None:
            loads.append(number)
        return checkpoints.entries(number)

    return file_chain(path, versions, entries, turns.get, list(queries), reader(checkpoints))


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
    chain = chain_of("/work/outputs/fig1.png", checkpoints, turns, queries)
    assert (chain["turn"], chain["checkpoint"], chain["in_review"]) == (2, 2, False)
    assert chain["commands"][0] == {
        "id": "c2",
        "command": "python /work/analysis.py",
        "exit_code": 0,
        "names_file": False,  # the command doesn't name it; its script does
        "via_script": "analysis.py",
        "seen_in_output": False,
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


def test_only_the_checkpoints_needed_are_read(workspace):
    work, checkpoints = workspace
    for turn in range(1, 6):  # the file changes in turn 4, then stays
        (work / "outputs" / "t.csv").write_text("new" if turn >= 4 else f"old {turn}")
        checkpoints.take(f"After turn {turn}", turn=turn)
    loads: list[int] = []
    chain = chain_of("outputs/t.csv", checkpoints, {}, loads=loads)
    assert chain["checkpoint"] == 4
    # Back from the latest until the content differed: never turns 1 and 2.
    assert sorted(set(loads)) == [3, 4, 5]


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
    chain = chain_of("outputs/table.csv", checkpoints, turns)
    assert [c["id"] for c in chain["commands"]] == ["c2", "c1"]
    assert chain["commands"][0]["names_file"] and "One command then names it." in chain["summary"]


def test_a_file_no_turn_made_and_a_missing_file_say_so(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "old.csv").write_text("a\n")
    checkpoints.take("Restored to checkpoint 1")  # no turn: a restore
    restored = chain_of("outputs/old.csv", checkpoints, {})
    assert restored["turn"] is None and "which no turn made" in restored["summary"]
    assert chain_of("outputs/gone.csv", checkpoints, {})["found"] is False


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
    chain = chain_of("outputs/t.csv", checkpoints, turns, queries)
    assert (len(chain["commands"]), chain["more_commands"]) == (3, 7)
    assert (len(chain["queries"]), chain["more_queries"]) == (2, 3)


def test_after_a_turn_its_answer_gets_a_provenance_event(settings, workspace):
    from datalab import db
    from datalab.sessions.provenance import record_turn
    from datalab.sessions.store import ConversationStore

    store = ConversationStore(db.connect(settings.database_file))
    cid = store.create(kind="data", mode="analysis", title="t", model="m").id
    work, checkpoints = workspace
    (work / "outputs" / "by_month.csv").write_text("month,hours\n2025-07,7.21\n")
    taken = checkpoints.take("After turn 1", turn=1).number
    since = store.append(cid, "user_message", {"text": "Sleep by month?"}).seq
    store.append(
        cid,
        "answer",
        {
            "id": "m1",
            "phase": "final_answer",
            "text": "Mean sleep was 7.2 h in 81 interns; see outputs/by_month.csv.",
        },
    )
    # The rigor review's own answer isn't the turn's.
    store.append(cid, "review_started", {})
    store.append(cid, "answer", {"id": "r1", "phase": "final_answer", "text": "Review: 99 issues."})
    data = record_turn(
        store, cid, since - 1, [(QUERY, '{"query_id": "q_0001", "n": 81}')], checkpoints, taken
    )
    assert data is not None and data["answer"] == "m1"
    by_text = {n["text"]: n["sources"] for n in data["numbers"]}
    assert by_text == {
        "7.2": [{"kind": "file", "ref": "outputs/by_month.csv"}],
        "81": [{"kind": "query", "ref": "q_0001"}],
    }
    assert data["files"] == ["outputs/by_month.csv"]
    [event] = [e for e in store.all_events_after(cid, 0) if e.type == "provenance"]
    assert event.data == data
    # An answer with nothing to trace adds nothing.
    quiet = store.append(cid, "user_message", {"text": "Thanks"}).seq
    store.append(cid, "answer", {"id": "m2", "phase": "final_answer", "text": "You're welcome."})
    assert record_turn(store, cid, quiet - 1, [], checkpoints, taken) is None


async def test_the_app_records_provenance_after_each_turn(app, monkeypatch):
    """The hook the app registers: the turn's sources and its own checkpoint."""
    from datalab.sessions.hooks import TurnInfo

    services = app.state.services
    store, sessions = services.conversations, services.sessions
    cid = store.create(kind="data", mode="analysis", title="t", model="m").id
    since = store.append(cid, "user_message", {"text": "How many?"}).seq - 1
    store.append(
        cid, "answer", {"id": "m1", "phase": "final_answer", "text": "There were 812 days."}
    )
    monkeypatch.setattr(sessions, "turn_sources", lambda c: [(COMMAND, "812 rows")])
    [hook] = [h for h in sessions._after_turn if h.__qualname__.endswith("after_turn")][-1:]
    await hook(cid, TurnInfo(turn=1, status="completed", since=since, checkpoint=None))
    [event] = [e for e in store.all_events_after(cid, 0) if e.type == "provenance"]
    assert event.data["numbers"] == [
        {"text": "812", "sources": [{"kind": "command", "ref": "cmd-1"}]}
    ]
    # A review run again has no new answer: nothing more is recorded.
    await hook(
        cid, TurnInfo(turn=1, status="completed", since=since, checkpoint=None, review_only=True)
    )
    assert len([e for e in store.all_events_after(cid, 0) if e.type == "provenance"]) == 1


def test_a_conversation_without_a_runtime_has_no_sources(app):
    assert app.state.services.sessions.turn_sources("no-such-conversation") == []


# --- From the review of PR #7 ------------------------------------------------


def test_a_file_the_rigor_review_wrote_is_credited_to_the_review(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "fig1.png").write_bytes(b"the turn's")
    checkpoints.take("After turn 1", turn=1)
    (work / "outputs" / "fig1.png").write_bytes(b"the review's")
    checkpoints.take("After turn 1's review", turn=1)
    turns = turns_from_events(
        [
            event("user_message"),
            event("command_started", id="c1", command="python plot.py"),
            event("command_started", id="c2", command="ls"),
            event("review_started"),
            event("command_started", id="r1", command="python fix.py --redo outputs/fig1.png"),
            event("review_finished", status="completed"),
        ]
    )
    assert [c.id for c in turns[1].review_commands] == ["r1"]
    chain = chain_of("outputs/fig1.png", checkpoints, turns)
    assert chain["in_review"] is True and [c["id"] for c in chain["commands"]] == ["r1"]
    assert "the checkpoint after turn 1's rigor review" in chain["summary"]
    assert "2 commands" not in chain["summary"]  # not the turn's own


def test_a_command_that_only_printed_the_name_isnt_said_to_name_it(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "fig1.png").write_bytes(b"x")
    checkpoints.take("After turn 1", turn=1)
    turns = turns_from_events(
        [
            event("user_message"),
            event("command_started", id="c1", command="ls outputs"),
            event("command_output", id="c1", text="fig1.png\n"),
            event("command_started", id="c2", command="python make.py"),
            event("command_started", id="c3", command="cp drafts/fig1.png /tmp/"),  # another file
        ]
    )
    chain = chain_of("outputs/fig1.png", checkpoints, turns)
    by_id = {c["id"]: c for c in chain["commands"]}
    assert by_id["c1"]["names_file"] is False and by_id["c1"]["seen_in_output"] is True
    assert by_id["c3"]["names_file"] is False
    assert [c["id"] for c in chain["commands"]] == ["c1", "c2", "c3"]  # seen, then the rest
    assert (
        "No command names it, so it was written by one of the 3 commands then" in (chain["summary"])
    )
    assert "One printed its name (listing or reading it, perhaps)." in chain["summary"]
    assert "not as it may be now" in chain["summary"]


def test_a_long_answer_and_lots_of_evidence_stay_quick():
    import time

    answer = " ".join(f"{i}.5" for i in range(500))
    evidence = [
        (Source("command", f"c{i}"), " ".join(f"{j}.5" for j in range(i, i + 20)))
        for i in range(5000)
    ]
    started = time.perf_counter()
    event = answer_provenance(answer, evidence, [])
    assert time.perf_counter() - started < 3
    assert len(event["numbers"]) == provenance.MAX_NUMBERS and event["more_numbers"] == 400
    assert len(event["numbers"][50]["sources"]) == provenance.MAX_SOURCES


def test_a_wide_querys_id_survives_being_cut():
    from datalab.sessions.runtime import _MAX_RESULT_TEXT, _query_id

    wide = json.dumps({"query_id": "q_0009", "preview": [{"X": "y" * 100}] * 5000}, indent=1)
    assert len(wide) > _MAX_RESULT_TEXT
    assert _query_id(wide[:_MAX_RESULT_TEXT]) == "q_0009"
    assert _query_id('{"row_count": 3}') == ""


def test_one_turn_is_parsed_on_its_own():
    turn = one_turn(
        [
            event("user_message", "2026-09-27T11:00:00"),
            event("command_started", id="c1", command="python a.py"),
            event("command_output", id="c1", text="a"),
            event("command_output", id="c1", text="b"),
            event("user_message", "2026-09-27T12:00:00"),  # the next turn: not read
            event("command_started", id="c9", command="rm -rf /"),
        ],
        2,
        "2026-09-27T12:00:00",
    )
    assert turn.number == 2 and [c.id for c in turn.commands] == ["c1"]
    assert turn.commands[0].output == "ab" and turn.ended_at == "2026-09-27T12:00:00"


CANARY = "CANARY-7f3e91"


def test_no_data_leaves_through_the_event_or_the_chain(app, settings):
    """A canary in every input that can hold data: streamed command output, a
    query's preview rows, an output data file, and a script. It must reach
    neither the provenance event nor the HTTP body."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    services = app.state.services
    store, sessions = services.conversations, services.sessions
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        since = store.append(cid, "user_message", {"text": "Sleep?"}).seq - 1
        store.append(cid, "command_started", {"id": "c1", "command": "python analysis.py"})
        store.append(cid, "command_output", {"id": "c1", "text": f"{CANARY} 7.21 outputs/fig1.png"})
        store.append(cid, "command_finished", {"id": "c1", "exit_code": 0, "output": CANARY})
        services.access_log.started(
            query_id="q_0001", session_id=cid, sql="SELECT 1 FROM dual", binds={}, tables=["X.T"]
        )
        services.access_log.finished(
            "q_0001", status="succeeded", row_count=1, result_path=Path("/data/oracle/q_0001.csv")
        )
        work = sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True, exist_ok=True)
        (work / "analysis.py").write_text(
            f"# {CANARY}\nread('q_0001.csv'); save('outputs/fig1.png')"
        )
        (work / "outputs" / "fig1.png").write_bytes(CANARY.encode())
        (work / "outputs" / "rows.csv").write_text(f"id,hours\n{CANARY},7.21\n")
        taken = sessions.checkpoints(cid).take("After turn 1", turn=1).number
        store.append(
            cid,
            "answer",
            {
                "id": "m1",
                "phase": "final_answer",
                "text": "Mean 7.2 h; see outputs/fig1.png and outputs/rows.csv.",
            },
        )
        sources = [
            (Source("command", "c1"), f"{CANARY} 7.21"),
            (
                Source("query", "q_0001"),
                json.dumps({"query_id": "q_0001", "preview": [{"ID": CANARY}]}),
            ),
        ]
        data = record_turn(store, cid, since, sources, sessions.checkpoints(cid), taken)
        body = client.get(f"/api/conversations/{cid}/provenance/outputs/fig1.png").text
    assert data is not None and data["numbers"] and data["files"]
    assert CANARY not in json.dumps(data)
    assert '"found":true' in body and CANARY not in body


def test_a_review_checkpoint_is_known_by_its_flag(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "fig1.png").write_bytes(b"the turn's")
    checkpoints.take("After turn 1", turn=1)
    (work / "outputs" / "fig1.png").write_bytes(b"the review's")
    taken = checkpoints.take("Saved", turn=1, review=True)  # whatever its label says
    assert checkpoints.get(taken.number).review is True
    turns = turns_from_events(
        [
            event("user_message"),
            event("command_started", id="c1", command="python plot.py"),
            event("review_started"),
            event("command_started", id="r1", command="python fix.py"),
            event("review_finished", status="completed"),
        ]
    )
    chain = chain_of("outputs/fig1.png", checkpoints, turns)
    assert chain["in_review"] and not chain["turn_not_saved"]
    assert [c["id"] for c in chain["commands"]] == ["r1"]


def test_when_the_turns_own_checkpoint_wasnt_saved_both_sets_of_commands_count(workspace):
    work, checkpoints = workspace
    (work / "outputs" / "fig1.png").write_bytes(b"made in the turn or its review")
    checkpoints.take("After turn 1's review", turn=1, review=True)  # the only one saved
    turns = turns_from_events(
        [
            event("user_message"),
            event("command_started", id="c1", command="python plot.py"),
            event("review_started"),
            event("command_started", id="r1", command="python fix.py"),
            event("review_finished", status="completed"),
        ]
    )
    chain = chain_of("outputs/fig1.png", checkpoints, turns)
    assert chain["in_review"] and chain["turn_not_saved"]
    assert [c["id"] for c in chain["commands"]] == ["c1", "r1"]
    assert "the turn's own checkpoint wasn't saved" in chain["summary"]
    assert "one of the 2 commands then" in chain["summary"]


def test_an_older_checkpoint_without_the_flag_is_known_by_its_label(workspace):
    from datalab.sessions.provenance import Version

    assert Version(2, 1, "After turn 1's review").after_review
    assert not Version(1, 1, "After turn 1").after_review


def test_the_chain_describes_the_version_being_looked_at(app):
    from fastapi.testclient import TestClient

    services = app.state.services
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"mode": "analysis"}).json()["id"]
        services.conversations.append(cid, "user_message", {"text": "One."})
        services.conversations.append(cid, "user_message", {"text": "Two."})
        work = services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True, exist_ok=True)
        checkpoints = services.sessions.checkpoints(cid)
        (work / "outputs" / "t.csv").write_text("first")
        checkpoints.take("After turn 1", turn=1)
        (work / "outputs" / "t.csv").write_text("second")
        checkpoints.take("After turn 2", turn=2)
        url = f"/api/conversations/{cid}/provenance/outputs/t.csv"
        latest = client.get(url).json()
        older = client.get(url, params={"checkpoint": 1}).json()
        missing = client.get(url, params={"checkpoint": 9})
    assert (latest["turn"], latest["as_of"]) == (2, 2)
    assert (older["turn"], older["as_of"]) == (1, 1)
    assert "as checkpoint 1 saved it" in older["summary"]
    assert missing.status_code == 404
