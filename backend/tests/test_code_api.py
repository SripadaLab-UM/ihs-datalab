"""The Code tab's API: code files across checkpoints, versions, diffs, and inline code."""

import json

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.sessions import code
from tests.conftest import FakeDatabase


@pytest.fixture
def app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


def repo_seed(conversation, staging):
    """A copy of a repository, as DataLab puts one into /work before the first turn."""
    (staging / "R").mkdir()
    (staging / "R" / "steps.R").write_text("steps <- function(x) x\n")
    (staging / "R" / "sleep.R").write_text("sleep <- function(x) x\n")
    (staging / ".git").mkdir()
    (staging / ".git" / "hook.sh").write_text("echo hi\n")
    (staging / "README.md").write_text("# Pipes\n")
    return "abc123"


def new_conversation(app, client, seed: bool = True):
    cid = client.post("/api/conversations", json={}).json()["id"]
    manager = app.state.services.sessions
    if seed:
        manager._seed_workspace(app.state.services.conversations.get(cid))
    work = manager.paths(cid).work
    work.mkdir(parents=True, exist_ok=True)
    return cid, work


def turn(app, cid, number):
    """What happens after every turn: a checkpoint of /work."""
    store = app.state.services.conversations
    store.append(cid, "user_message", {"text": f"turn {number}"})
    app.state.services.sessions.checkpoints(cid).take(f"After turn {number}", turn=number)


def listing(client, cid, **params):
    response = client.get(f"/api/conversations/{cid}/code", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_new_and_modified_code_is_listed_and_unchanged_repo_files_arent(app):
    app.state.services.sessions.register_workspace_seed("pipes", repo_seed, into="pipes")
    with TestClient(app) as client:
        cid, work = new_conversation(app, client)
        (work / "scripts").mkdir()
        (work / "scripts" / "steps_by_week.R").write_text("x <- 1\n")
        (work / "outputs").mkdir(exist_ok=True)
        (work / "outputs" / "report.html").write_text("<p>not code</p>")
        (work / "pipes" / "R" / "steps.R").write_text("steps <- function(x) x + 1\n")
        turn(app, cid, 1)
        (work / "scripts" / "steps_by_week.R").write_text("x <- 1\ny <- 2\n")
        (work / "query.sql").write_text("SELECT 1 FROM dual\n")
        turn(app, cid, 2)
        turn(app, cid, 3)  # nothing changed: no new version

        listed = listing(client, cid)
        by_path = {f["path"]: f for f in listed["files"]}
        assert set(by_path) == {"scripts/steps_by_week.R", "query.sql", "pipes/R/steps.R"}
        # Newest change first.
        assert [f["path"] for f in listed["files"]][:2] == ["query.sql", "scripts/steps_by_week.R"]
        script = by_path["scripts/steps_by_week.R"]
        assert script["status"] == "new" and script["language"] == "r" and script["current"]
        assert [(v["checkpoint"], v["turn"]) for v in script["versions"]] == [(1, 1), (2, 2)]
        assert script["size"] == len("x <- 1\ny <- 2\n")
        # Changed from the copy DataLab made: modified, with the copy as its first version.
        repo = by_path["pipes/R/steps.R"]
        assert repo["status"] == "modified"
        assert [v["checkpoint"] for v in repo["versions"]] == [0, 1]
        assert repo["versions"][0]["label"] == "As copied into the workspace"
        assert listed["unchanged"] == 1  # pipes/R/sleep.R, left out
        assert by_path["query.sql"]["language"] == "sql"

        everything = listing(client, cid, all=True)
        unchanged = [f for f in everything["files"] if f["status"] == "unchanged"]
        # Git's own files are never code the agent wrote.
        assert [f["path"] for f in unchanged] == ["pipes/R/sleep.R"]


def test_older_conversations_without_a_recorded_copy_treat_the_first_checkpoint_as_it(app):
    manager = app.state.services.sessions
    manager.register_workspace_seed("pipes", repo_seed, into="pipes")
    with TestClient(app) as client:
        cid, work = new_conversation(app, client)
        (manager.paths(cid).checkpoints / "baseline.json").unlink()
        turn(app, cid, 1)
        (work / "pipes" / "R" / "sleep.R").write_text("changed\n")
        turn(app, cid, 2)
        by_path = {f["path"]: f for f in listing(client, cid)["files"]}
        assert by_path["pipes/R/sleep.R"]["status"] == "modified"
        assert [v["checkpoint"] for v in by_path["pipes/R/sleep.R"]["versions"]] == [1, 2]
        assert "pipes/R/steps.R" not in by_path


def test_a_deleted_script_keeps_its_versions(app):
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        (work / "try.py").write_text("print(1)\n")
        turn(app, cid, 1)
        (work / "try.py").unlink()
        turn(app, cid, 2)
        [file] = listing(client, cid)["files"]
        assert file["status"] == "deleted" and not file["current"]
        version = client.get(
            f"/api/conversations/{cid}/code/version", params={"path": "try.py"}
        ).json()
        assert version["text"] == "print(1)\n" and not version["current"]


def test_versions_and_diffs(app):
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        (work / "a.py").write_text("import pandas\nx = 1\nprint(x)\n")
        turn(app, cid, 1)
        (work / "a.py").write_text("import pandas\nx = 2\nprint(x)\nprint('done')\n")
        turn(app, cid, 2)

        url = f"/api/conversations/{cid}/code"
        first = client.get(f"{url}/version", params={"path": "a.py", "checkpoint": 1}).json()
        assert first["text"].startswith("import pandas\nx = 1")
        assert first["version"]["turn"] == 1 and not first["current"]
        latest = client.get(f"{url}/version", params={"path": "a.py"}).json()
        assert latest["current"] and latest["version"]["checkpoint"] == 2
        # A checkpoint where it didn't change gives the version saved before it.
        turn(app, cid, 3)
        same = client.get(f"{url}/version", params={"path": "a.py", "checkpoint": 3}).json()
        assert same["version"]["checkpoint"] == 2 and same["current"]

        diff = client.get(f"{url}/diff", params={"path": "a.py", "base": 1}).json()
        assert diff["head"]["checkpoint"] == 2 and diff["head_current"]
        assert (diff["added"], diff["removed"]) == (2, 1)
        ops = [(line["op"], line["text"]) for line in diff["lines"]]
        assert ops[0][0] == "@"
        assert ("-", "x = 1") in ops and ("+", "x = 2") in ops and ("+", "print('done')") in ops
        removed = next(line for line in diff["lines"] if line["op"] == "-")
        assert (removed["old"], removed["new"]) == (2, None)
        back = client.get(f"{url}/diff", params={"path": "a.py", "base": 2, "head": 1}).json()
        assert (back["added"], back["removed"]) == (1, 2)


def test_a_file_too_large_to_show_is_listed_but_not_sent(app, monkeypatch):
    monkeypatch.setattr(code, "MAX_TEXT_BYTES", 10)
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        (work / "big.R").write_text("x <- 1\n" * 10)
        (work / "small.R").write_text("x <- 1\n")
        turn(app, cid, 1)
        (work / "big.R").write_text("x <- 2\n" * 10)
        turn(app, cid, 2)
        url = f"/api/conversations/{cid}/code"
        big = next(f for f in listing(client, cid)["files"] if f["path"] == "big.R")
        assert big["versions"][0]["too_large"]
        shown = client.get(f"{url}/version", params={"path": "big.R"}).json()
        assert shown["too_large"] and shown["text"] is None
        diff = client.get(f"{url}/diff", params={"path": "big.R", "base": 1}).json()
        assert diff["too_large"] and diff["lines"] == [] and diff["head_text"] is None
        assert client.get(f"{url}/version", params={"path": "small.R"}).json()["text"] == "x <- 1\n"


@pytest.mark.parametrize(
    "path",
    ["../settings.toml", "/etc/passwd", "a/../a.py", "a.py/", "", "outputs/report.html", "nope.py"],
)
def test_only_code_files_the_checkpoints_saved_can_be_asked_for(app, path):
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        (work / "a.py").write_text("x = 1\n")
        (work / "outputs").mkdir(exist_ok=True)
        (work / "outputs" / "report.html").write_text("<p>hi</p>")
        turn(app, cid, 1)
        url = f"/api/conversations/{cid}/code"
        assert client.get(f"{url}/version", params={"path": path}).status_code == 404
        assert client.get(f"{url}/diff", params={"path": path, "base": 1}).status_code == 404
        assert client.get("/api/conversations/nope/code").status_code == 404


def test_a_link_in_work_is_never_followed(app, tmp_path):
    secret = tmp_path / "secret.py"
    secret.write_text("TOKEN = 'x'\n")
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        (work / "linked.py").symlink_to(secret)
        turn(app, cid, 1)
        assert listing(client, cid)["files"] == []
        response = client.get(
            f"/api/conversations/{cid}/code/version", params={"path": "linked.py"}
        )
        assert response.status_code == 404


def notebook(outputs: list) -> str:
    return json.dumps(
        {
            "metadata": {"kernelspec": {"language": "R", "name": "ir"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Steps\n", "By week."]},
                {"cell_type": "code", "source": "summary(steps)", "outputs": outputs},
            ],
        }
    )


def test_notebooks_show_their_cells_and_never_their_outputs(app):
    with TestClient(app) as client:
        cid, work = new_conversation(app, client, seed=False)
        leak = {"output_type": "stream", "text": ["participant 12345 slept 4h"]}
        (work / "steps.ipynb").write_text(notebook([leak, leak]))
        turn(app, cid, 1)
        (work / "steps.ipynb").write_text(notebook([leak]).replace("summary", "mean"))
        turn(app, cid, 2)
        url = f"/api/conversations/{cid}/code"
        response = client.get(f"{url}/version", params={"path": "steps.ipynb", "checkpoint": 1})
        assert "12345" not in response.text
        shown = response.json()
        assert shown["text"] is None and shown["language"] == "r"
        assert [(c["kind"], c["outputs"]) for c in shown["notebook"]["cells"]] == [
            ("markdown", 0),
            ("code", 2),
        ]
        assert shown["notebook"]["outputs"] == 2
        assert shown["notebook"]["cells"][0]["source"] == "# Steps\nBy week."
        diff = client.get(f"{url}/diff", params={"path": "steps.ipynb", "base": 1})
        assert "12345" not in diff.text
        ops = [(line["op"], line["text"]) for line in diff.json()["lines"]]
        assert ("-", "summary(steps)") in ops and ("+", "mean(steps)") in ops
        (work / "broken.ipynb").write_text("{not json")
        turn(app, cid, 3)
        broken = client.get(f"{url}/version", params={"path": "broken.ipynb"}).json()
        assert broken["unreadable"] and broken["text"] is None and broken["notebook"] is None


def test_inline_code_comes_from_the_event_log(app):
    with TestClient(app) as client:
        cid, _ = new_conversation(app, client, seed=False)
        store = app.state.services.conversations
        events = [
            ("user_message", {"text": "one"}),
            ("command_started", {"id": "c1", "command": "/bin/bash -lc 'ls /work'"}),
            (
                "command_started",
                {"id": "c2", "command": "/bin/bash -lc 'python3 -c \"print(1+1)\"'"},
            ),
            ("command_finished", {"id": "c2", "exit_code": 0}),
            ("user_message", {"text": "two"}),
            (
                "command_started",
                {"id": "c3", "command": "bash -lc \"Rscript - <<'EOF'\nx <- 1\nprint(x)\nEOF\""},
            ),
            ("command_finished", {"id": "c3", "exit_code": 1}),
            (
                "command_started",
                {"id": "c4", "command": "bash -lc \"cat > /work/a.R <<'EOF'\nx\nEOF\""},
            ),
            ("command_started", {"id": "c5", "command": "Rscript /work/scripts/a.R"}),
            ("review_started", {}),
            ("command_started", {"id": "c6", "command": "python3 -c 'print(2)'"}),
            ("review_finished", {"status": "completed"}),
        ]
        for kind, data in events:
            store.append(cid, kind, data)
        inline = listing(client, cid)["inline"]
        assert [(i["id"], i["turn"], i["language"], i["exit_code"]) for i in inline] == [
            ("c3", 2, "r", 1),
            ("c2", 1, "python", 0),
        ]
        assert inline[0]["code"] == "x <- 1\nprint(x)" and inline[0]["step"] == "cmd-c3"
        assert inline[1]["code"] == "print(1+1)"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "python3 -c 'import pandas as pd; print(pd.__version__)'",
            ("python", "import pandas as pd; print(pd.__version__)"),
        ),
        ("cd /work && Rscript -e 'summary(1:10)'", ("r", "summary(1:10)")),
        ("python - <<'PY'\nprint(1)\nPY", ("python", "print(1)")),
        ("duckdb -c 'SELECT 42'", ("sql", "SELECT 42")),
        ("set -e\ncd /work\nls\nwc -l x.csv", ("shell", "set -e\ncd /work\nls\nwc -l x.csv")),
        ("ls -la /work", None),
        ("Rscript scripts/a.R", None),
        ("cat > /work/scripts/a.py <<'EOF'\nprint(1)\nEOF", None),
        ("tee /work/a.py <<'EOF'\nprint(1)\nEOF", None),
    ],
)
def test_which_commands_are_inline_code(command, expected):
    assert code.inline_code(command) == expected


def test_diff_lines_carry_line_numbers():
    diff = code.diff_texts("a\nb\nc\n", "a\nB\nc\nd\n")
    assert [(d.op, d.old, d.new, d.text) for d in diff.lines] == [
        ("@", None, None, "@@ -1,3 +1,4 @@"),
        (" ", 1, 1, "a"),
        ("-", 2, None, "b"),
        ("+", None, 2, "B"),
        (" ", 3, 3, "c"),
        ("+", None, 4, "d"),
    ]
    assert code.diff_texts("same\n", "same\n").lines == []


@pytest.mark.parametrize(
    ("command", "inner"),
    [
        # bash's double-quote escapes: \$ \` \" \\ and a backslash-newline.
        (
            "/bin/bash -lc \"Rscript -e 'stopifnot(all(x\\$n > 10))'\"",
            "Rscript -e 'stopifnot(all(x$n > 10))'",
        ),
        ('bash -lc "echo \\"hi\\" \\`date\\` a\\\\b \\q"', 'echo "hi" `date` a\\b \\q'),
        ('bash -c "one \\\ntwo"', "one two"),
        ("/bin/zsh -lc 'print($x)'", "print($x)"),
        ("bash -lc 'it'\"'\"'s'", "it's"),
        ('bash -lc "unclosed', 'bash -lc "unclosed'),
        ("ls -la", "ls -la"),
    ],
)
def test_the_command_inside_a_shell_wrapper_is_read_as_bash_reads_it(command, inner):
    assert code.unwrap_shell(command) == inner


def test_inline_r_from_a_double_quoted_wrapper_has_no_leftover_escapes():
    command = (
        '/bin/bash -lc "Rscript -e \'x <- readr::read_csv(\\"a.csv\\"); '
        "stopifnot(all(x\\$n > 10))'\""
    )
    assert code.inline_code(command) == (
        "r",
        'x <- readr::read_csv("a.csv"); stopifnot(all(x$n > 10))',
    )
