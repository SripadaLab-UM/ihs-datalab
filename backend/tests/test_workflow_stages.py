"""New workflow: a draft as Extract → Process & QC → Deliver, edits to it, and test runs.

Edits go through the workflow model (workflows/stages.py) and come back as a
new file, which the file check reads like any other. Test runs are
practice-only and never deliver. Practice keeps new workflows in its local
folder, beside its built-in ones. All data is synthetic (SYN-####).
"""

from __future__ import annotations

import textwrap
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.workflows import WorkflowServices, build_workflows_router
from datalab.config import WorkflowSettings
from datalab.exports import DestinationStore
from datalab.sessions.modes import MODES, WORKFLOW_AUTHORING
from datalab.workflows.model import load_workflow
from datalab.workflows.source import BUILTIN_DIR
from datalab.workflows.stages import (
    DROP_MARK,
    CheckEdit,
    DeliverEdit,
    NewCheck,
    NewDropColumns,
    ParameterEdit,
    StageEdits,
    StagesRefused,
    StepEdit,
    apply_edits,
    drop_columns_script,
    dropped_columns,
    read_model,
    stages_of,
)
from tests.test_workflow_runner import WEEKLY
from tests.workflow_fakes import FakeSandbox, Harness, weekly_summary

# What the Workflow authoring agent is asked to write for the user's example:
# Fitbit-style daily rows for a date range, a column dropped, one row per
# participant-day checked, delivered. Over the SQLite stand-in's table.
DAILY = """\
name: daily_clean
description: Daily wearable rows for a date range, without the device column.
parameters:
  start_date: { type: date, default: 2025-04-01, description: First day }
  end_date: { type: date, default: 2025-04-15, description: Day after the last }
reads: [IHS_2025.WEARABLE_DAILY]
steps:
  - id: extract
    sql: |
      SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS
      FROM IHS_2025.WEARABLE_DAILY
      WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND RECORD_DATE < TO_DATE(:end_date, 'YYYY-MM-DD')
    output: daily_raw.csv
deliver:
  destination: practice-exports
  folder: daily_clean
  files: [extract]
"""

SCHEMAS = frozenset({"IHS_2024", "IHS_2025"})


def check(text: str):
    return load_workflow(text, allowed_schemas=SCHEMAS)


# ------------------------------------------------------------------ the view


def test_the_three_stages_of_a_file():
    stages = stages_of(check(WEEKLY))
    assert [s.id for s in stages.extract] == ["extract"]
    assert stages.extract[0].tables == ["IHS_2025.WEARABLE_DAILY"]
    assert [(p.id, p.kind) for p in stages.process] == [
        ("check_raw", "check"),
        ("summary", "r"),
        ("check_summary", "check"),
    ]
    raw = stages.process[0]
    assert raw.file == "extract" and raw.min_rows == 1
    assert raw.unique_by == ["STUDY_PARTICIPANT_ID", "RECORD_DATE"]
    assert raw.other_rules == ["max_missing"]  # kept as it is, not shown as a field
    summary = stages.process[1]
    assert summary.drop_columns is None and summary.script and "read.csv" in summary.script
    small = stages.process[2].small_cells
    assert (
        small is not None and small.count_columns == ["n_participants"] and small.min == "$min_cell"
    )
    assert stages.deliver is not None and stages.deliver.destination == "practice-folder"
    assert [o.ref for o in stages.outputs] == ["extract", "summary"]
    assert [p.name for p in stages.parameters] == ["start_date", "end_date", "min_cell"]


def test_no_edits_writes_the_same_workflow_again():
    again = apply_edits(WEEKLY, StageEdits())
    assert check(again) == check(WEEKLY)
    # Written from the model, as a person would lay it out.
    assert "steps:\n  - id: extract\n    sql: |\n" in again
    assert "\n\n  - id: check_raw\n" in again


def test_every_builtin_workflow_survives_being_written_again():
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert check(apply_edits(text, StageEdits())) == check(text), path.name


# ------------------------------------------------------------------ edits


def test_the_users_example_built_from_edits():
    """Drop fields, check duplicate participant-days, deliver: all as edits."""
    edits = StageEdits(
        description="Daily rows for a date range, device column removed, checked.",
        parameters={"end_date": ParameterEdit(default="2025-04-22")},
        add_drop_columns=[NewDropColumns(input="extract", columns=["DEVICE"])],
        add_checks=[
            NewCheck(
                file="drop_extract",
                min_rows=1,
                required_columns=["STUDY_PARTICIPANT_ID", "RECORD_DATE"],
                unique_by=["STUDY_PARTICIPANT_ID", "RECORD_DATE"],
                description="One row per participant-day",
            )
        ],
        deliver=DeliverEdit(folder="daily_clean_2025"),
    )
    text = apply_edits(DAILY, edits)
    workflow = check(text)
    ids = [s.id for s in workflow.steps]
    assert ids == ["extract", "drop_extract", "check_drop_extract"]
    stages = stages_of(workflow)
    drop = stages.process[0]
    assert (
        drop.kind == "r" and drop.drop_columns == ["DEVICE"] and drop.inputs == {"raw": "extract"}
    )
    assert stages.process[1].unique_by == ["STUDY_PARTICIPANT_ID", "RECORD_DATE"]
    # The delivery now delivers the cleaned file, not the raw one.
    assert stages.deliver is not None and stages.deliver.files == ["drop_extract"]
    assert stages.deliver.folder == "daily_clean_2025"
    assert workflow.parameters["end_date"].default == "2025-04-22"

    # Changing the columns writes the script again from the template.
    more = apply_edits(
        text, StageEdits(steps={"drop_extract": StepEdit(drop_columns=["DEVICE", "STEPS"])})
    )
    assert stages_of(check(more)).process[0].drop_columns == ["DEVICE", "STEPS"]
    # Rules edited and taken out.
    fewer = apply_edits(
        more,
        StageEdits(checks={"check_drop_extract": CheckEdit(min_rows=5, remove=["unique_by"])}),
    )
    rule = stages_of(check(fewer)).process[1]
    assert rule.min_rows == 5 and rule.unique_by is None


def test_editing_the_sql_updates_reads():
    edited = apply_edits(
        DAILY,
        StageEdits(
            steps={
                "extract": StepEdit(
                    sql="SELECT COHORT FROM IHS_2025.PARTICIPANTS",
                    description="The cohort",
                )
            },
            no_deliver=True,
        ),
    )
    workflow = check(edited)
    assert workflow.read_objects == {"IHS_2025.PARTICIPANTS"}
    assert workflow.deliver is None and workflow.steps[0].description == "The cohort"


def test_edits_that_cant_be_made():
    with pytest.raises(StagesRefused, match="no step"):
        apply_edits(DAILY, StageEdits(steps={"nope": StepEdit(description="x")}))
    with pytest.raises(StagesRefused, match="isn't a drop-columns step"):
        apply_edits(WEEKLY, StageEdits(steps={"summary": StepEdit(drop_columns=["X"])}))
    with pytest.raises(StagesRefused, match="aren't column names"):
        injected = NewDropColumns(input="extract", columns=['a") ; system("x'])
        apply_edits(DAILY, StageEdits(add_drop_columns=[injected]))
    with pytest.raises(StagesRefused, match="no parameter"):
        apply_edits(DAILY, StageEdits(parameters={"nope": ParameterEdit(default=1)}))


def test_the_drop_columns_template_is_recognised_only_exactly():
    script = drop_columns_script("raw", ["BODYBMI", "BODYFAT", "BODYBMI"])
    assert script.startswith(DROP_MARK + "\n")
    assert dropped_columns(script, {"raw": "extract"}) == ["BODYBMI", "BODYFAT"]
    # Anything added by hand makes it a script like any other.
    assert dropped_columns(script + "x <- 1\n", {"raw": "extract"}) is None
    assert dropped_columns(script, {"raw": "a", "other": "b"}) is None


def test_a_file_the_model_cant_read_has_no_stages():
    from datalab.workflows.model import WorkflowInvalid

    with pytest.raises(WorkflowInvalid):
        read_model("name: x\nsteps: [{id: a}]\n")


# ------------------------------------------------------------------ the API


def _client(tmp_path: Path, profile: str = "practice"):
    h = Harness(
        tmp_path,
        sandbox=FakeSandbox({"summary": weekly_summary}),
        profile=profile,
    )
    # The default folders, as an installed DataLab has them (no [workflows] folder).
    settings = replace(h.settings, workflows=WorkflowSettings())
    services = WorkflowServices(settings, h.connection, h.data, h.access_log, sandbox=h.sandbox)
    app = FastAPI()
    app.include_router(build_workflows_router(services))
    return TestClient(app), h, settings


@pytest.fixture
def practice(tmp_path):
    client, h, settings = _client(tmp_path)
    with client:
        yield client, h, settings


def finished(client: TestClient, run_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        run = client.get(f"/api/workflows/runs/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.05)
    raise AssertionError("the run didn't finish")


def test_stages_route_shows_and_edits_a_draft(practice):
    client, _, settings = practice
    shown = client.post("/api/workflows/stages", json={"text": DAILY}).json()
    assert shown["valid"] is True and shown["text"] == DAILY
    assert shown["stages"]["extract"][0]["id"] == "extract"
    assert shown["target"]["kind"] == "local"
    # Practice's one export folder, under whatever key the file names.
    [choice] = shown["destinations"]
    assert choice["key"] == "practice-exports" and choice["name"] == "Practice exports"
    assert choice["path"] == str(settings.data_dir / "practice-exports")

    edited = client.post(
        "/api/workflows/stages",
        json={
            "text": DAILY,
            "edits": {
                "add_drop_columns": [{"input": "extract", "columns": ["DEVICE"]}],
                "add_checks": [
                    {"file": "drop_extract", "unique_by": ["STUDY_PARTICIPANT_ID", "RECORD_DATE"]}
                ],
            },
        },
    ).json()
    assert edited["valid"] is True, edited["problems"]
    assert DROP_MARK in edited["text"]
    assert [p["kind"] for p in edited["stages"]["process"]] == ["r", "check"]

    # A file the check refuses still shows its stages, with the problems.
    broken = client.post(
        "/api/workflows/stages", json={"text": DAILY.replace("files: [extract]", "files: [nope]")}
    ).json()
    assert broken["valid"] is False and broken["stages"] is not None
    assert broken["problems"][0]["path"] == "deliver.files[0]"
    # One the model can't read has none: it's fixed in the YAML view.
    unreadable = client.post("/api/workflows/stages", json={"text": "name: x\nsteps: 3\n"}).json()
    assert unreadable["stages"] is None and unreadable["valid"] is False
    refused = client.post(
        "/api/workflows/stages",
        json={"text": DAILY, "edits": {"steps": {"nope": {"description": "x"}}}},
    )
    assert refused.status_code == 422


def test_a_test_run_runs_the_draft_and_never_delivers(practice):
    client, _, settings = practice
    text = client.post(
        "/api/workflows/stages",
        json={
            "text": DAILY,
            "edits": {
                "add_checks": [
                    {"file": "extract", "unique_by": ["STUDY_PARTICIPANT_ID", "RECORD_DATE"]}
                ]
            },
        },
    ).json()["text"]
    started = client.post("/api/workflows/test-runs", json={"text": text})
    assert started.status_code == 201, started.text
    run = finished(client, started.json()["id"])
    assert run["status"] == "succeeded", run
    assert run["workflow_path"] == "draft:daily_clean"
    [check_step] = [s for s in run["steps"] if s["kind"] == "qc_builtin"]
    assert {c["id"] for c in check_step["result"]["checks"]} >= {"unique_by"}
    assert run["delivery_status"] == "skipped"
    assert "test run doesn't deliver" in run["delivery_message"]
    exports = settings.data_dir / "practice-exports"
    assert not exports.exists() or not any(exports.iterdir())
    # Nothing was saved, and a draft's run can't be run again or replayed into a delivery.
    assert "draft:daily_clean" not in {w["path"] for w in client.get("/api/workflows").json()}
    assert client.post(f"/api/workflows/runs/{run['id']}/again").status_code == 404
    replay = client.post(f"/api/workflows/runs/{run['id']}/replay", json={"deliver": True})
    assert replay.status_code == 409
    # A draft that fails the check doesn't run.
    bad = client.post("/api/workflows/test-runs", json={"text": DAILY.replace("[extract]", "[x]")})
    assert bad.status_code == 422


def test_test_runs_are_practice_only(tmp_path):
    client, _, _ = _client(tmp_path, profile="real")
    with client:
        refused = client.post("/api/workflows/test-runs", json={"text": DAILY})
        assert refused.status_code == 403 and "Practice DataLab" in refused.json()["detail"]


def test_practice_saves_locally_beside_its_builtin_workflows(practice):
    client, _, settings = practice
    status = client.get("/api/workflows/status").json()
    assert status["profile"] == "practice" and status["target"]["kind"] == "local"
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    builtin = sorted(p for p in listed if p.startswith("builtin/"))
    assert len(builtin) == 8 and all(listed[p]["builtin"] for p in builtin)
    assert all(listed[p]["valid"] for p in builtin), [listed[p]["problems"] for p in builtin]

    saved = client.post("/api/workflows/saves", json={"text": DAILY, "source": "authoring"})
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert (
        body["state"] == "saved" and body["shared"] is False and body["path"] == "daily_clean.yaml"
    )
    assert "on this computer" in body["message"]
    local = settings.data_dir / "workflows-local" / "daily_clean.yaml"
    assert local.read_text() == DAILY
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    assert listed["daily_clean.yaml"]["builtin"] is False

    # Run again with other parameters: a saved workflow runs and delivers to the practice folder.
    run_id = client.post(
        "/api/workflows/runs",
        json={"path": "daily_clean.yaml", "params": {"end_date": "2025-04-08"}},
    ).json()["id"]
    run = finished(client, run_id)
    assert run["status"] == "succeeded" and run["delivery_status"] == "delivered"
    assert run["delivery_message"] == "1 file saved locally."
    assert run["params"]["end_date"] == "2025-04-08"
    again = finished(client, client.post(f"/api/workflows/runs/{run_id}/again").json()["id"])
    assert again["status"] == "succeeded"

    # A built-in name can't be saved over, whatever the text.
    clash = DAILY.replace("name: daily_clean", "name: fitbit_daily_2025")
    assert client.post("/api/workflows/saves", json={"text": clash}).status_code == 409


def test_the_agent_is_shown_the_drop_columns_template_exactly():
    """Workflow authoring's instructions give the template as DataLab writes it,
    so the agent's drop step shows as an editable list of columns."""
    block = WORKFLOW_AUTHORING.split("  - id: drop_body_composition\n", 1)[1].split("```", 1)[0]
    script = textwrap.dedent(block.split("    r: |\n", 1)[1].split("    inputs:", 1)[0])
    assert script == drop_columns_script("raw", ["BODYBMI", "BODYFAT"])
    assert MODES["workflows"].question == "What should this workflow do?"


def test_oracle_column_names_with_dollar_and_hash_are_dropped_as_written():
    script = drop_columns_script("raw", ["PAY$RATE", "ROW#", "PLAIN"])
    assert 'drop <- c("PAY$RATE", "ROW#", "PLAIN")\n' in script
    assert dropped_columns(script, {"raw": "extract"}) == ["PAY$RATE", "ROW#", "PLAIN"]
    # In a file, and read back from it.
    text = apply_edits(
        DAILY, StageEdits(add_drop_columns=[NewDropColumns(input="extract", columns=["A$B", "C#"])])
    )
    assert stages_of(check(text)).process[0].drop_columns == ["A$B", "C#"]
    with pytest.raises(StagesRefused):
        drop_columns_script("raw", ['A\\"B'])


def test_the_yaml_never_has_anchors_or_aliases():
    """The same object twice (a shared list) is written out twice, never as
    an alias, which the file check would refuse."""
    from datalab.workflows.stages import _Dumper

    shared = ["STUDY_PARTICIPANT_ID", "RECORD_DATE"]
    written = yaml.dump({"a": shared, "b": shared}, Dumper=_Dumper, sort_keys=False)
    assert "&" not in written and "*" not in written
    text = apply_edits(
        DAILY,
        StageEdits(
            add_checks=[NewCheck(file="extract", required_columns=shared, unique_by=shared)]
        ),
    )
    assert "&id" not in text and check(text).steps[1].qc.unique_by == tuple(shared)  # type: ignore[union-attr]


# ------------------------------------------------------------------ export folders (real profile)

REAL_DAILY = DAILY + (
    "  without_small_cells:\n"
    "    extract: Row-level daily rows as asked for, with no counts of people.\n"
)


@pytest.fixture
def real(tmp_path):
    client, h, settings = _client(tmp_path, profile="real")
    store = DestinationStore(h.connection)
    with client:
        yield client, h, settings, store


def _folder(tmp_path: Path, store: DestinationStore, name: str, *, exists: bool = True):
    path = tmp_path / name.replace(" ", "_")
    if exists:
        path.mkdir()
    return store.add(name, path)


def test_the_review_never_maps_a_folder_and_offers_only_folders_that_are_there(real, tmp_path):
    client, _, settings, store = real
    lab = _folder(tmp_path, store, "IHS 2025 exports")
    _folder(tmp_path, store, "Gone", exists=False)
    # A lab workflow already names ihs-2025-exports.
    folder = settings.data_dir / "workflows-local"
    (folder / "lab.yaml").write_text(
        REAL_DAILY.replace("practice-exports", "ihs-2025-exports").replace("daily_clean", "lab")
    )
    edits = {"deliver": {"destination": "whatever"}}
    shown = client.post("/api/workflows/stages", json={"text": REAL_DAILY, "edits": edits}).json()
    # The folder named like the lab's key is offered under another key, never the lab's.
    [choice] = shown["destinations"]
    assert choice["destination_id"] == lab.id and choice["mapped"] is False
    assert choice["key"] == "ihs-2025-exports-2" and choice["used_by"] == []
    # Nothing was mapped by reviewing, even with a folder chosen.
    assert all(d.key is None for d in store.list())


def test_a_new_folder_is_mapped_when_the_workflow_is_saved(real, tmp_path):
    client, _, _, store = real
    picked = _folder(tmp_path, store, "My exports")
    text = REAL_DAILY.replace("practice-exports", "my-exports")
    saved = client.post(
        "/api/workflows/saves",
        json={"text": text, "source": "authoring", "map_destination": picked.id},
    )
    assert saved.status_code == 201, saved.text
    assert store.by_key("my-exports") is not None and store.by_key("my-exports").id == picked.id  # type: ignore[union-attr]


def test_a_key_other_workflows_use_is_mapped_only_after_a_confirm_naming_them(real, tmp_path):
    client, _, settings, store = real
    picked = _folder(tmp_path, store, "New folder")
    folder = settings.data_dir / "workflows-local"
    (folder / "lab.yaml").write_text(
        REAL_DAILY.replace("practice-exports", "ihs-2025-exports").replace("daily_clean", "lab")
    )
    text = REAL_DAILY.replace("practice-exports", "ihs-2025-exports")
    body = {"text": text, "source": "authoring", "map_destination": picked.id}
    refused = client.post("/api/workflows/saves", json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["used_by"] == ["lab.yaml"]
    # Nothing saved, nothing mapped.
    assert not (folder / "daily_clean.yaml").exists() and store.by_key("ihs-2025-exports") is None
    confirmed = client.post(
        "/api/workflows/saves", json={**body, "confirm_key_used_by": ["lab.yaml"]}
    )
    assert confirmed.status_code == 201, confirmed.text
    assert store.by_key("ihs-2025-exports").id == picked.id  # type: ignore[union-attr]
    # A folder that's gone, or a key that means another folder already, isn't mapped.
    gone = _folder(tmp_path, store, "Gone", exists=False)
    again = {"text": text.replace("daily_clean", "other"), "map_destination": gone.id}
    assert client.post("/api/workflows/saves", json=again).status_code == 409
    other = _folder(tmp_path, store, "Other")
    taken = {"text": text.replace("daily_clean", "third"), "map_destination": other.id}
    assert client.post("/api/workflows/saves", json=taken).status_code == 409


# ------------------------------------------------------------------ test runs are cleaned up


def test_a_drafts_test_runs_keep_the_newest_three_and_go_when_its_saved(practice):
    client, _, settings = practice
    ids = []
    for _ in range(5):
        started = client.post("/api/workflows/test-runs", json={"text": DAILY})
        assert started.status_code == 201, started.text
        ids.append(started.json()["id"])
        finished(client, ids[-1])
    listed = [r["id"] for r in client.get("/api/workflows/runs").json()]
    assert sorted(listed) == sorted(ids[2:])  # the newest three
    for gone in ids[:2]:
        assert client.get(f"/api/workflows/runs/{gone}").status_code == 404
        assert not (settings.data_dir / "runs" / gone).exists()
    assert (settings.data_dir / "runs" / ids[-1]).is_dir()
    # Saved: all of them go.
    assert client.post("/api/workflows/saves", json={"text": DAILY}).status_code == 201
    assert [
        r
        for r in client.get("/api/workflows/runs").json()
        if r["workflow_path"].startswith("draft:")
    ] == []
    assert not any((settings.data_dir / "runs" / i).exists() for i in ids)


def test_a_discarded_drafts_test_runs_are_removed(practice):
    client, _, settings = practice
    run_id = client.post("/api/workflows/test-runs", json={"text": DAILY}).json()["id"]
    finished(client, run_id)
    other = client.post(
        "/api/workflows/test-runs", json={"text": DAILY.replace("daily_clean", "other")}
    )
    finished(client, other.json()["id"])
    assert (
        client.delete("/api/workflows/test-runs", params={"name": "daily_clean"}).status_code == 204
    )
    assert client.get(f"/api/workflows/runs/{run_id}").status_code == 404
    assert not (settings.data_dir / "runs" / run_id).exists()
    # Only that draft's.
    assert client.get(f"/api/workflows/runs/{other.json()['id']}").status_code == 200
    assert client.delete("/api/workflows/test-runs", params={"name": "../x"}).status_code == 422
