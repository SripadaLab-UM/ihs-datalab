"""Exporting over the API: destinations, files from checkpoints, and reports."""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.exports import MANIFEST
from datalab.sessions import picker
from tests.conftest import FakeDatabase


def make_app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


@pytest.fixture
def real_settings(settings):
    return dataclasses.replace(settings, profile="real")


def prepare(app, client):
    cid = client.post("/api/conversations", json={"title": "Sleep study"}).json()["id"]
    work = app.state.services.sessions.paths(cid).work
    (work / "outputs").mkdir(parents=True)
    (work / "outputs" / "table.csv").write_text("a\n1\n")
    app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
    (work / "outputs" / "table.csv").write_text("changed after the checkpoint")
    return cid


def test_practice_exports_only_to_its_own_folder(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        [destination] = client.get("/api/export-destinations").json()
        assert destination["id"] == "practice"
        assert client.post("/api/export-destinations").status_code == 403
        cid = prepare(app, client)
        result = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "table.csv"}],
                "checkpoint": shown(client, cid),
                "report": {"html": "<h1>Hi</h1><a href='https://x'>x</a><script>1</script>"},
            },
        )
        assert result.status_code == 201
        # Files are exported only as a listing showed them, never "whatever is latest".
        unpinned = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "table.csv"}],
            },
        )
        assert unpinned.status_code == 422
        folder = settings.data_dir / "practice-exports"
        [export] = list(folder.iterdir())
        assert (export / "files" / "outputs" / "table.csv").read_text() == "a\n1\n"  # checkpoint
        report = (export / "conversation.html").read_text()
        assert "Content-Security-Policy" in report
        assert "https://x" not in report and "<script" not in report.split("</head>")[1]
        manifest = json.loads((export / MANIFEST).read_text())
        assert manifest["conversation"]["title"] == "Sleep study"
        assert manifest["contains_study_data"] is False  # practice data is synthetic
        assert manifest["checkpoint"] == shown(client, cid)
        events = client.get(f"/api/conversations/{cid}/events").json()
        assert events[-1]["type"] == "exported"


def test_what_names_an_export_carries_no_study_identifiers(settings, catalog):
    """A title typed by the person (or stored before titles were scrubbed)
    may name a participant: the folder, manifest, and report title don't."""
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = prepare(app, client)
        client.patch(f"/api/conversations/{cid}", json={"title": "P-0001 sleep, 2025-03-14"})
        result = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "table.csv"}],
                "checkpoint": shown(client, cid),
                "report": {"html": "<p>Hi</p>"},
            },
        )
        assert result.status_code == 201
        [export] = list((settings.data_dir / "practice-exports").iterdir())
        assert export.name.endswith(f" sleep {cid}")
        manifest = json.loads((export / MANIFEST).read_text())
        assert manifest["conversation"]["title"] == "sleep"
        assert manifest["conversation"]["id"] == cid
        assert "<title>sleep</title>" in (export / "conversation.html").read_text()


def test_real_destinations_come_from_the_picker(real_settings, catalog, monkeypatch, tmp_path):
    dropbox = tmp_path / "Dropbox" / "IHS"
    dropbox.mkdir(parents=True)
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())

    async def chosen(kind, **_):
        assert kind == "folder"
        return [dropbox]

    monkeypatch.setattr(picker, "pick", chosen)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        assert client.get("/api/export-destinations").json() == []
        added = client.post("/api/export-destinations")
        assert added.status_code == 201 and added.json()["path"] == str(dropbox)
        assert client.post("/api/export-destinations").status_code == 409  # already added
        cid = prepare(app, client)
        exported = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": added.json()["id"],
                "files": [{"root": "outputs", "path": "table.csv"}],
                "checkpoint": shown(client, cid),
            },
        ).json()
        assert exported["folder"].startswith(str(dropbox))
        manifest = json.loads((dropbox / exported["folder"].split("/")[-1] / MANIFEST).read_text())
        assert manifest["contains_study_data"] is True
        # Unknown files and made-up destinations are refused.
        missing = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": added.json()["id"],
                "files": [{"root": "outputs", "path": "nope.csv"}],
                "checkpoint": shown(client, cid),
            },
        )
        assert missing.status_code == 404
        bogus = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "dest_x",
                "files": [{"root": "outputs", "path": "table.csv"}],
                "checkpoint": shown(client, cid),
            },
        )
        assert bogus.status_code == 404
        assert client.delete(f"/api/export-destinations/{added.json()['id']}").status_code == 204
        assert (dropbox / exported["folder"].split("/")[-1]).exists()  # untouched


def test_datalab_folders_cant_be_destinations(real_settings, catalog, monkeypatch):
    async def chosen(kind, **_):
        return [real_settings.data_dir]

    monkeypatch.setattr(picker, "pick", chosen)
    real_settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        assert client.post("/api/export-destinations").status_code == 422


def test_a_destination_replaced_by_a_link_is_refused(real_settings, catalog, monkeypatch, tmp_path):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    monkeypatch.setattr("datalab.sessions.inputs._CONTAINERS_POSIX", ())

    async def chosen(kind, **_):
        return [dropbox]

    monkeypatch.setattr(picker, "pick", chosen)
    app = make_app(real_settings, catalog)
    with TestClient(app) as client:
        destination = client.post("/api/export-destinations").json()["id"]
        cid = prepare(app, client)
        dropbox.rmdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        dropbox.symlink_to(elsewhere)
        refused = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": destination,
                "files": [{"root": "outputs", "path": "table.csv"}],
                "checkpoint": shown(client, cid),
            },
        )
        assert refused.status_code == 422
        assert list(elsewhere.iterdir()) == []


def test_an_exported_page_keeps_its_own_images(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={}).json()["id"]
        outputs = app.state.services.sessions.paths(cid).work / "outputs"
        (outputs / "figures").mkdir(parents=True)
        (outputs / "figures" / "a.png").write_bytes(b"\x89PNG")
        (outputs / "report.html").write_text(
            "<img src='figures/a.png'><img src='../../etc/passwd.png'><img src='https://x/y.png'>"
        )
        app.state.services.sessions.checkpoints(cid).take("t")
        client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": "outputs", "path": "report.html"}],
                "checkpoint": shown(client, cid),
            },
        )
        [export] = list((settings.data_dir / "practice-exports").iterdir())
        page = (export / "files" / "outputs" / "report.html").read_text()
        assert page.count("data:image/png;base64,iVBORw") == 1
        assert "https://x" not in page and "passwd" not in page


def shown(client, cid: str) -> int:
    """The checkpoint the outputs listing shows, as the export dialog sends it."""
    return client.get(f"/api/conversations/{cid}/files").json()[0]["checkpoint"]


def test_scripts_are_exported_beside_the_outputs_under_every_export_rule(
    settings, catalog, monkeypatch
):
    """The Export dialog's Code group: /work/scripts as the checkpoint shown has them."""
    from datalab import exports

    flagged: list[str] = []
    monkeypatch.setattr(exports, "mark_downloaded", lambda path: flagged.append(path.name))
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"title": "Weekly steps"}).json()["id"]
        work = app.state.services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True)
        (work / "scripts").mkdir()
        (work / "outputs" / "summary.csv").write_text("week,n\n1,118\n")
        (work / "scripts" / "steps_by_week.R").write_text("x <- 1\n")
        (work / "scripts" / "run_all.sh").write_text("Rscript steps_by_week.R\n")
        app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
        (work / "scripts" / "steps_by_week.R").write_text("changed after the checkpoint\n")
        files = [
            {"root": "outputs", "path": "summary.csv"},
            {"root": "work", "path": "scripts/steps_by_week.R"},
            {"root": "work", "path": "scripts/run_all.sh"},
        ]
        result = client.post(
            f"/api/conversations/{cid}/exports",
            json={"destination_id": "practice", "files": files, "checkpoint": shown(client, cid)},
        )
        assert result.status_code == 201, result.text
        # Practice exports go only to the practice folder.
        [export] = list((settings.data_dir / "practice-exports").iterdir())
        scripts = export / "files" / "workspace" / "scripts"
        assert (scripts / "steps_by_week.R").read_text() == "x <- 1\n"  # the version shown
        # A shell script can't run when double-clicked: it's exported as text.
        assert (scripts / "run_all.sh.txt").read_text() == "Rscript steps_by_week.R\n"
        assert (export / "files" / "outputs" / "summary.csv").exists()
        manifest = json.loads((export / MANIFEST).read_text())
        by_from = {f["from"]: f for f in manifest["files"]}
        assert by_from["/work/scripts/steps_by_week.R"]["path"] == (
            "files/workspace/scripts/steps_by_week.R"
        )
        assert by_from["/work/scripts/run_all.sh"]["renamed_from"] == "workspace/scripts/run_all.sh"
        # Every exported file carries the quarantine flag, scripts included.
        assert {"steps_by_week.R", "run_all.sh.txt", "summary.csv"} <= set(flagged)
        # Only files the checkpoint saved, inside the workspace.
        for bad in ("scripts/../../settings.toml", "scripts/nope.R"):
            refused = client.post(
                f"/api/conversations/{cid}/exports",
                json={
                    "destination_id": "practice",
                    "files": [{"root": "work", "path": bad}],
                    "checkpoint": shown(client, cid),
                },
            )
            assert refused.status_code in (404, 422)


def test_a_notebook_leaves_without_its_outputs_and_only_scripts_leave_from_work(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"title": "Notebook"}).json()["id"]
        work = app.state.services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True)
        (work / "scripts").mkdir()
        leak = "participant P0009 slept 4h"
        notebook = {
            "metadata": {"kernelspec": {"language": "python"}, "widgets": {"state": leak}},
            "nbformat": 4,
            "cells": [
                {"cell_type": "markdown", "source": ["# Sleep"], "attachments": {"a.png": leak}},
                {
                    "cell_type": "code",
                    "source": ["df.head()"],
                    "execution_count": 7,
                    "outputs": [{"output_type": "stream", "text": [leak]}],
                },
            ],
        }
        (work / "scripts" / "explore.ipynb").write_text(json.dumps(notebook))
        (work / "joined.csv").write_text("pid\nP0001\n")
        (work / "scripts" / "rows.csv").write_text("pid\nP0001\n")
        app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
        checkpoint = client.get(f"/api/conversations/{cid}/files", params={"root": "work"}).json()
        number = checkpoint[0]["checkpoint"]

        def export(path):
            return client.post(
                f"/api/conversations/{cid}/exports",
                json={
                    "destination_id": "practice",
                    "files": [{"root": "work", "path": path}],
                    "checkpoint": number,
                },
            )

        # Joined data, or anything but a script, never leaves from /work.
        for path in ("joined.csv", "scripts/rows.csv", "outputs/../joined.csv"):
            assert export(path).status_code in (400, 422), path
        assert export("scripts/explore.ipynb").status_code == 201
        [folder] = list((settings.data_dir / "practice-exports").iterdir())
        exported = (folder / "files" / "workspace" / "scripts" / "explore.ipynb").read_text()
        assert "P0009" not in exported
        cells = json.loads(exported)["cells"]
        assert cells[1] == {
            "cell_type": "code",
            "id": "cell-2",
            "metadata": {},
            "source": ["df.head()"],
            "outputs": [],
            "execution_count": None,
        }
        assert cells[0]["source"] == ["# Sleep"]
        # The viewer shows it without outputs too.
        shown_nb = client.get(f"/api/conversations/{cid}/files/work/scripts/explore.ipynb")
        assert shown_nb.status_code == 200 and "P0009" not in shown_nb.text


MARK = "MARKER_P0042"
_OUT = [{"output_type": "stream", "text": [MARK]}]
# A marker in every place a notebook can carry something DataLab never shows.
LEAKY_NOTEBOOK = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "results": _OUT,
    "metadata": {
        "kernelspec": {
            "name": "python3",
            "language": "python",
            "display_name": MARK,
            "extra": MARK,
        },
        "language_info": {"name": "python", "nested": {"x": MARK}},
        "widgets": {"state": MARK},
        "papermill": {"parameters": {"pid": MARK}},
    },
    "cells": [
        {
            "cell_type": "code",
            "id": MARK,
            "source": ["df.head()"],
            "execution_count": 3,
            "outputs": _OUT,
            "output": _OUT,
            "metadata": {"cached": _OUT, "tags": [MARK]},
        },
        {
            "cell_type": "markdown",
            "source": "![a](attachment:a.png)",
            "attachments": {"a.png": MARK},
        },
        {"cell_type": "raw", "source": "raw", "outputs": _OUT},
        {"cell_type": "Code", "source": ["print(1)"], "outputs": _OUT},
    ],
}


@pytest.mark.parametrize("root", ["work", "outputs"])
def test_a_notebook_is_rebuilt_from_an_allowlist_wherever_it_is_seen_or_sent(
    settings, catalog, root
):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"title": "Notebook"}).json()["id"]
        work = app.state.services.sessions.paths(cid).work
        (work / "outputs").mkdir(parents=True)
        (work / "scripts").mkdir()
        where = work / ("scripts" if root == "work" else "outputs") / "leaky.ipynb"
        where.write_text(json.dumps(LEAKY_NOTEBOOK))
        (work / "outputs" / "old.ipynb").write_text(
            json.dumps({"nbformat": 3, "metadata": {}, "worksheets": [{"cells": []}]})
        )
        (work / "outputs" / "both.ipynb").write_text(
            json.dumps({"nbformat": 4, "metadata": {}, "cells": [], "worksheets": [_OUT]})
        )
        app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)
        number = shown(client, cid)
        path = "scripts/leaky.ipynb" if root == "work" else "leaky.ipynb"
        in_work = "scripts/leaky.ipynb" if root == "work" else "outputs/leaky.ipynb"

        viewer = client.get(f"/api/conversations/{cid}/files/{root}/{path}")
        version = client.get(f"/api/conversations/{cid}/code/version", params={"path": in_work})
        exported = client.post(
            f"/api/conversations/{cid}/exports",
            json={
                "destination_id": "practice",
                "files": [{"root": root, "path": path}],
                "checkpoint": number,
            },
        )
        assert viewer.status_code == 200 and MARK not in viewer.text
        assert version.status_code == 200 and MARK not in version.text
        assert exported.status_code == 201
        [folder] = list((settings.data_dir / "practice-exports").iterdir())
        target = "workspace/scripts" if root == "work" else "outputs"
        sent = (folder / "files" / target / "leaky.ipynb").read_text()
        assert MARK not in sent and MARK not in (folder / MANIFEST).read_text()
        # What leaves is exactly what the viewer showed, and only the allowlist.
        assert (
            json.loads(sent)
            == json.loads(viewer.text)
            == {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {
                    "kernelspec": {"name": "python3", "language": "python"},
                    "language_info": {"name": "python"},
                },
                "cells": [
                    {
                        "cell_type": "code",
                        "id": "cell-1",
                        "metadata": {},
                        "source": ["df.head()"],
                        "outputs": [],
                        "execution_count": None,
                    },
                    {
                        "cell_type": "markdown",
                        "id": "cell-2",
                        "metadata": {},
                        "source": "![a](attachment:a.png)",
                    },
                    {"cell_type": "raw", "id": "cell-3", "metadata": {}, "source": "raw"},
                    {"cell_type": "raw", "id": "cell-4", "metadata": {}, "source": ["print(1)"]},
                ],
            }
        )
        cells = version.json()["notebook"]["cells"]
        assert [(c["kind"], c["outputs"]) for c in cells] == [
            ("code", 1),
            ("markdown", 0),
            ("raw", 1),
            ("raw", 1),
        ]
        # Pre-v4 notebooks and ones with worksheets are refused, not guessed at.
        if root == "outputs":
            for name in ("old.ipynb", "both.ipynb"):
                assert (
                    client.get(f"/api/conversations/{cid}/files/outputs/{name}").status_code == 422
                )
                refused = client.post(
                    f"/api/conversations/{cid}/exports",
                    json={
                        "destination_id": "practice",
                        "files": [{"root": "outputs", "path": name}],
                        "checkpoint": number,
                    },
                )
                assert refused.status_code == 422, name
                unreadable = client.get(
                    f"/api/conversations/{cid}/code/version", params={"path": f"outputs/{name}"}
                ).json()
                assert unreadable["unreadable"] and unreadable["notebook"] is None
