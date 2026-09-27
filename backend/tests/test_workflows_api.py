"""The Workflows endpoints, over the fake sandbox and SQLite stand-in."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.workflows import WorkflowServices, build_workflows_router
from tests.test_workflow_runner import WEEKLY
from tests.workflow_fakes import FakeSandbox, Harness, weekly_summary


@pytest.fixture
def api(tmp_path):
    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": weekly_summary}))
    h.write("weekly_steps.yaml", WEEKLY)
    h.write("broken.yaml", WEEKLY.replace("files: [summary]", "files: [nothing]"))
    app = FastAPI()
    services = WorkflowServices(h.settings, h.connection, h.data, h.access_log, sandbox=h.sandbox)
    app.include_router(build_workflows_router(services))
    with TestClient(app) as client:
        yield client, h


def finished(client: TestClient, run_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        run = client.get(f"/api/workflows/runs/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.05)
    raise AssertionError("the run didn't finish")


def test_list_and_validate(api):
    client, _ = api
    assert client.get("/api/workflows/status").json()["available"] is True
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    assert listed["weekly_steps.yaml"]["valid"] is True
    assert [s["kind"] for s in listed["weekly_steps.yaml"]["steps"]] == [
        "sql", "qc_builtin", "r", "qc_builtin"
    ]  # fmt: skip
    assert listed["broken.yaml"]["valid"] is False
    # Where each problem is in the file, for the editor's marks.
    line = WEEKLY.splitlines().index("  files: [summary]") + 1
    assert listed["broken.yaml"]["problems"] == [
        {
            "path": "deliver.files[0]",
            "message": "'nothing' isn't an earlier step.",
            "line": line,
            "column": 11,  # at "nothing"
        }
    ]
    draft = client.post("/api/workflows/validate", json={"text": "name: x\nsteps: []\n"}).json()
    assert draft["valid"] is False
    # A missing key has nowhere in the file to be.
    missing = {"path": "reads", "message": "This is required.", "line": None, "column": None}
    assert missing in draft["problems"]
    assert client.post("/api/workflows/validate", json={}).status_code == 422


def test_run_watch_replay_and_run_again(api):
    client, h = api
    started = client.post(
        "/api/workflows/runs",
        json={"path": "weekly_steps.yaml", "params": {"min_cell": 11}, "seed": 5},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    with client.stream("GET", f"/api/workflows/runs/{run_id}/stream") as stream:
        events = [line for line in stream.iter_lines() if line.startswith("event:")]
    assert events[-1] == "event: end" and "event: run" in events
    run = finished(client, run_id)
    assert run["status"] == "succeeded" and len(run["steps"]) == 4
    assert run["steps"][0]["sql_text"].lstrip().startswith("SELECT")
    steps = client.get(f"/api/workflows/runs/{run_id}/steps").json()
    assert [s["step_id"] for s in steps] == ["extract", "check_raw", "summary", "check_summary"]
    delivery = client.get(f"/api/workflows/runs/{run_id}/delivery").json()
    assert delivery["status"] == "delivered" and len(delivery["deliveries"]) == 1

    check = client.get(f"/api/workflows/runs/{run_id}/replay").json()
    assert check == {"exact": True, "reasons": [], "blocking": []}
    replay = client.post(f"/api/workflows/runs/{run_id}/replay", json={})
    assert replay.status_code == 201
    replayed = finished(client, replay.json()["id"])
    assert replayed["reproduced"] is True and replayed["delivery_status"] == "skipped"

    again = client.post(f"/api/workflows/runs/{run_id}/again")
    assert again.status_code == 201 and again.json()["mode"] == "run_again"
    finished(client, again.json()["id"])
    listed = client.get("/api/workflows/runs", params={"path": "weekly_steps.yaml"}).json()
    assert {r["mode"] for r in listed} == {"run", "replay", "run_again"}

    h.sandbox.images = {}
    refused = client.post(f"/api/workflows/runs/{run_id}/replay", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["reasons"]


def test_text_and_last_run(api):
    client, _ = api
    text = client.get("/api/workflows/text", params={"path": "weekly_steps.yaml"}).json()
    assert text["text"] == WEEKLY and text["source"] == "file"
    assert text["blob"].startswith("sha256:")
    assert client.get("/api/workflows/text", params={"path": "../x.yaml"}).status_code == 404
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    assert listed["weekly_steps.yaml"]["last_run"] is None
    run_id = client.post("/api/workflows/runs", json={"path": "weekly_steps.yaml"}).json()["id"]
    finished(client, run_id)
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    assert listed["weekly_steps.yaml"]["last_run"]["id"] == run_id
    assert listed["broken.yaml"]["last_run"] is None


def test_errors(api):
    client, _ = api
    assert client.get("/api/workflows/runs/run_nope").status_code == 404
    assert client.post("/api/workflows/runs/run_nope/stop").status_code == 404
    bad = client.post("/api/workflows/runs", json={"path": "broken.yaml"})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"]
    assert client.post("/api/workflows/runs", json={"path": "../x.yaml"}).status_code == 404
    wrong = client.post(
        "/api/workflows/runs", json={"path": "weekly_steps.yaml", "params": {"start_date": "soon"}}
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["problems"][0]["path"] == "params.start_date"


def test_destination_keys_in_practice(api):
    client, _ = api
    [key] = client.get("/api/workflows/destinations").json()
    assert key["key"] == "practice-folder" and key["available"] is True
    assert key["used_by"] == ["weekly_steps.yaml"] and key["name"] == "Practice exports"
    put = client.put("/api/workflows/destinations/practice-folder", json={"destination_id": "x"})
    assert put.status_code == 403
