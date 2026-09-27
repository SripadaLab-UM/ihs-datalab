"""Save as workflow (SQL Playground) and Turn this into a workflow (a
conversation): drafts from SQL, checked, and saved by the person.

Locally over the workflow fakes (SQLite for Oracle, a fake sandbox); shared
over the pipelines repo's fakes (a local bare repo for GitHub).
"""

# `lab` is test_pipelines's fixture, used here by name.
# ruff: noqa: F401, F811

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import db
from datalab.api.workflows import WorkflowServices, build_workflows_router
from datalab.data.access_log import AccessLog
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from datalab.workflows.drafts import DraftQuery, draft_workflow
from datalab.workflows.model import load_workflow, sql_tables
from tests.test_pipelines import Lab, lab, lab_settings, synced
from tests.test_workflows_api import finished
from tests.workflow_fakes import FakeSandbox, Harness, SqliteDatabase, catalog

# Upper case, as Oracle names an unquoted alias (SQLite, standing in, keeps its case).
BY_DEVICE = """\
SELECT DEVICE, COUNT(DISTINCT STUDY_PARTICIPANT_ID) AS N_PARTICIPANTS
FROM IHS_2025.WEARABLE_DAILY
WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
  AND DEVICE <> :skip_device
GROUP BY DEVICE
ORDER BY DEVICE;
"""

RAW = """\
SELECT w.STUDY_PARTICIPANT_ID, w.RECORD_DATE, w.STEPS
FROM IHS_2025.WEARABLE_DAILY w
WHERE w.STUDY_PARTICIPANT_ID IN (SELECT p.STUDY_PARTICIPANT_ID FROM IHS_2025.PARTICIPANTS p
                                 WHERE p.COHORT = :cohort)
  AND w.STEPS > :min_steps
"""


@pytest.fixture
def api(tmp_path):
    h = Harness(tmp_path, sandbox=FakeSandbox())
    app = FastAPI()
    services = WorkflowServices(
        h.settings, h.connection, h.data, h.access_log, sandbox=h.sandbox, catalog=catalog()
    )
    app.include_router(build_workflows_router(services))
    with TestClient(app) as client:
        yield client, h


def drafted(client: TestClient, **body) -> dict:
    response = client.post("/api/workflows/drafts", json={"name": "steps_by_device", **body})
    assert response.status_code == 200, response.text
    return response.json()


def playground(sql: str, **binds) -> list[dict]:
    return [{"sql": sql, "binds": binds}]


# Drafting ---------------------------------------------------------------------


def test_a_query_the_sql_check_refuses_makes_no_draft(api):
    client, _ = api
    for sql in (
        "DELETE FROM IHS_2025.WEARABLE_DAILY",
        "SELECT * FROM IHS_2026.WEARABLE_DAILY",  # a cohort this DataLab may not read
        "SELECT NO_SUCH_COLUMN FROM IHS_2025.WEARABLE_DAILY",  # checked against the catalog
        "SELECT 1 FROM IHS_2025.WEARABLE_DAILY; SELECT 2 FROM IHS_2025.WEARABLE_DAILY",
    ):
        response = client.post(
            "/api/workflows/drafts", json={"name": "x", "queries": playground(sql)}
        )
        assert response.status_code == 422, sql
        assert response.json()["detail"].startswith("The SQL check refuses it"), sql
    empty = client.post("/api/workflows/drafts", json={"name": "x", "queries": []})
    assert empty.status_code == 422


def test_binds_become_parameters_with_the_values_they_ran_with(api):
    client, _ = api
    draft = drafted(
        client,
        queries=playground(
            RAW + " AND w.RECORD_DATE < TO_DATE(:END_DATE, 'YYYY-MM-DD') AND w.DEVICE = :device",
            cohort="SYN-0042",
            min_steps="1000",
            END_DATE="2025-05-01",
            device="",
        ),
    )
    parameters = yaml.safe_load(draft["text"])["parameters"]
    assert parameters == {
        # A value shaped like an identifier isn't kept, and a note says why.
        "cohort": {"type": "string"},
        "device": {"type": "string"},
        "end_date": {"type": "date", "default": "2025-05-01"},
        "min_steps": {"type": "integer", "default": 1000},
    }
    assert any(":cohort looks like it could identify someone" in n for n in draft["notes"])
    assert any(":device had no value" in n for n in draft["notes"])
    # Every bind is a declared parameter (Oracle's binds ignore case), so it passes.
    assert draft["valid"] is True, draft["problems"]
    assert draft["target"]["kind"] == "local"


def test_reads_are_the_tables_the_sql_names_as_the_file_check_finds_them(api):
    client, _ = api
    sql = "WITH recent AS (SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.PARTICIPANTS) " + RAW.replace(
        "IHS_2025.PARTICIPANTS p", "recent p"
    ).replace("WHERE p.COHORT = :cohort", "")
    draft = drafted(client, queries=playground(sql, min_steps=1000))
    reads = yaml.safe_load(draft["text"])["reads"]
    # The CTE's own name isn't an object; the table it reads is.
    assert reads == ["IHS_2025.PARTICIPANTS", "IHS_2025.WEARABLE_DAILY"]
    assert set(reads) == sql_tables(sql)
    assert draft["valid"] is True, draft["problems"]


def test_an_aggregate_gets_a_small_cells_check_over_its_counts(api):
    client, _ = api
    draft = drafted(client, queries=playground(BY_DEVICE, start_date="2025-04-01"))
    workflow = yaml.safe_load(draft["text"])
    [extract, check] = workflow["steps"]
    assert extract["sql"] == BY_DEVICE.strip().rstrip(";") + "\n"
    assert check["qc"] == {
        "file": "extract",
        "min_rows": 1,
        "required_columns": ["DEVICE", "N_PARTICIPANTS"],
        "small_cells": {"count_columns": ["N_PARTICIPANTS"], "min": 11},
    }
    # Row-level output: no small-cells check, and no columns required after `*`.
    raw = yaml.safe_load(drafted(client, queries=playground(RAW, cohort="a"))["text"])
    assert "small_cells" not in raw["steps"][1]["qc"]
    star = drafted(client, queries=playground("SELECT * FROM IHS_2025.WEARABLE_DAILY"))
    assert yaml.safe_load(star["text"])["steps"][1]["qc"] == {"file": "extract", "min_rows": 1}


def test_a_count_it_cant_name_is_left_for_the_person_and_the_file_check_says_so(api):
    client, _ = api
    draft = drafted(
        client,
        queries=playground("SELECT DEVICE, COUNT(*) FROM IHS_2025.WEARABLE_DAILY GROUP BY DEVICE"),
    )
    assert yaml.safe_load(draft["text"])["steps"][1]["qc"]["small_cells"]["count_columns"] == []
    assert any("COUNT(*) AS n" in n for n in draft["notes"])
    assert draft["valid"] is False
    assert [p["path"] for p in draft["problems"]] == ["steps[1].qc.small_cells.count_columns"]
    # A count of a CTE's count: the draft can't tell, so it asks (and doesn't guess none).
    cte = (
        "WITH c AS (SELECT DEVICE, COUNT(*) AS n FROM IHS_2025.WEARABLE_DAILY GROUP BY DEVICE) "
        "SELECT DEVICE, n FROM c"
    )
    asked = drafted(client, queries=playground(cte))
    assert any("couldn't tell which columns are counts" in n for n in asked["notes"])


def test_the_real_profile_wants_small_cells_on_whatever_is_delivered():
    text = draft_workflow(
        [DraftQuery(RAW, {"cohort": "a", "min_steps": 5})],
        name="raw_steps",
        destination="dropbox-ihs-2025",
        allowed_schemas=frozenset({"IHS_2025"}),
        columns=None,
    ).text
    with pytest.raises(Exception, match="needs a small_cells check"):
        load_workflow(text, require_small_cells=True)
    counted = draft_workflow(
        [DraftQuery(BY_DEVICE, {"start_date": "2025-04-01", "skip_device": "x"})],
        name="by_device",
        destination="dropbox-ihs-2025",
        allowed_schemas=frozenset({"IHS_2025"}),
        columns=None,
    ).text
    assert load_workflow(counted, require_small_cells=True).deliver is not None


# Saving on this computer -------------------------------------------------------


def test_saved_locally_it_is_listed_and_runs(api):
    client, h = api
    draft = drafted(
        client,
        description="Participants by device",
        destination="practice-folder",
        queries=playground(BY_DEVICE, start_date="2025-04-01", skip_device="apple"),
    )
    assert draft["valid"] is True, draft["problems"]
    assert "isn't shared" in draft["target"]["message"]
    saved = client.post("/api/workflows/saves", json={"text": draft["text"]})
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert (body["state"], body["shared"], body["path"]) == ("saved", False, "steps_by_device.yaml")
    assert (h.folder / "steps_by_device.yaml").read_text() == draft["text"]
    listed = {w["path"]: w for w in client.get("/api/workflows").json()}
    assert listed["steps_by_device.yaml"]["valid"] is True
    assert listed["steps_by_device.yaml"]["reads"] == ["IHS_2025.WEARABLE_DAILY"]

    started = client.post("/api/workflows/runs", json={"path": "steps_by_device.yaml"})
    assert started.status_code == 201, started.text
    run = finished(client, started.json()["id"])
    assert run["status"] == "succeeded", (run["message"], [s["message"] for s in run["steps"]])
    assert run["delivery_status"] == "delivered"
    assert run["params"] == {"start_date": "2025-04-01", "skip_device": "apple"}

    # Never replaced; and a file that doesn't pass isn't saved.
    again = client.post("/api/workflows/saves", json={"text": draft["text"]})
    assert again.status_code == 409 and "already" in again.json()["detail"]
    broken = client.post("/api/workflows/saves", json={"text": "name: x\nsteps: []\n"})
    assert broken.status_code == 422 and broken.json()["detail"]["problems"]


def test_small_cells_stop_a_saved_workflow_before_delivery(api):
    client, _ = api
    # Apple Watch is rare in the synthetic rows: its count is small.
    draft = drafted(
        client,
        destination="practice-folder",
        queries=playground(BY_DEVICE, start_date="2025-04-01", skip_device="none"),
    )
    assert client.post("/api/workflows/saves", json={"text": draft["text"]}).status_code == 201
    run_id = client.post("/api/workflows/runs", json={"path": "steps_by_device.yaml"}).json()["id"]
    run = finished(client, run_id)
    assert run["status"] == "failed" and run["delivery_status"] != "delivered"
    assert [s["status"] for s in run["steps"]] == ["succeeded", "failed"]


def test_turn_a_conversations_queries_into_a_workflow(api):
    client, h = api
    conversation = "c_" + "a" * 12
    for sql, binds in (
        (BY_DEVICE, {"start_date": "2025-04-01", "skip_device": "apple"}),
        ("SELECT NOPE FROM IHS_2025.WEARABLE_DAILY", {}),  # refused: never ran
        (RAW, {"cohort": "A", "min_steps": 1000}),
    ):
        with contextlib.suppress(SqlRejected):
            asyncio.run(
                h.data.run_query(
                    session_id=conversation, sql=sql, binds=binds, results_dir=h.settings.data_dir
                )
            )
    log = h.access_log.for_session(conversation)
    assert [r.status for r in log] == ["succeeded", "rejected", "succeeded"]
    draft = drafted(
        client, name="from_chat", conversation_id=conversation, query_ids=[log[0].id, log[2].id]
    )
    workflow = yaml.safe_load(draft["text"])
    assert [s["id"] for s in workflow["steps"]] == ["extract_1", "check_1", "extract_2", "check_2"]
    assert workflow["reads"] == ["IHS_2025.PARTICIPANTS", "IHS_2025.WEARABLE_DAILY"]
    assert set(workflow["parameters"]) == {"start_date", "skip_device", "cohort", "min_steps"}
    assert draft["valid"] is True, draft["problems"]
    # Only queries that ran, and only this conversation's.
    for ids in ([log[1].id], ["q_nope"]):
        refused = client.post(
            "/api/workflows/drafts",
            json={"name": "x", "conversation_id": conversation, "query_ids": ids},
        )
        assert refused.status_code == 422
    other = client.post(
        "/api/workflows/drafts",
        json={"name": "x", "conversation_id": "c_" + "b" * 12, "query_ids": [log[0].id]},
    )
    assert other.status_code == 422
    # Drafting saved nothing: only the person's Save does.
    assert client.get("/api/workflows").json() == []


# Saving with Save & share --------------------------------------------------------


@pytest.fixture
def shared(lab: Lab):
    settings = lab_settings(lab)
    connection = db.connect(settings.database_file)
    access_log = AccessLog(connection, settings.data_dir / "logs" / "audit.jsonl")
    data = DataService(
        SqliteDatabase(), access_log, settings.limits, frozenset({"IHS_2025"}), catalog()
    )
    app = FastAPI()
    app.include_router(
        build_workflows_router(
            WorkflowServices(
                settings,
                connection,
                data,
                access_log,
                sandbox=FakeSandbox(),
                catalog=catalog(),
                pipelines=lab.pipelines,
            )
        )
    )
    with TestClient(app) as client:
        yield client
    connection.close()


def settled_save(client: TestClient, save_id: str) -> dict:
    for _ in range(200):
        found = client.get(f"/api/workflows/saves/{save_id}").json()
        if found["state"] != "saving":
            return found
        time.sleep(0.05)
    raise AssertionError("still saving")


def test_with_the_pipelines_repo_it_is_saved_with_save_and_share(lab, shared):
    client = shared
    body = {
        "name": "steps_by_device",
        "destination": "dropbox-ihs-2025",
        "queries": playground(BY_DEVICE, start_date="2025-04-01", skip_device="apple"),
    }
    # Until the clone's first sync, there's nowhere to share it.
    waiting = client.post("/api/workflows/drafts", json=body).json()
    assert waiting["target"]["kind"] == "unavailable"
    assert client.post("/api/workflows/saves", json={"text": waiting["text"]}).status_code == 409

    synced(lab)
    draft = client.post("/api/workflows/drafts", json=body).json()
    assert draft["target"]["kind"] == "share" and draft["valid"] is True, draft
    before = lab.remote.head()
    started = client.post("/api/workflows/saves", json={"text": draft["text"]})
    assert started.status_code == 201, started.text
    assert started.json()["state"] == "saving" and started.json()["shared"] is True
    done = settled_save(client, started.json()["id"])
    assert done["state"] == "saved", done
    assert done["path"] == "workflows/steps_by_device.yaml"
    assert lab.remote.show("workflows/steps_by_device.yaml") == draft["text"].rstrip("\n")
    assert done["commit"] == lab.remote.head() != before
    [line] = lab.remote.log("%an|%s")[:1]
    assert line == "Yu Fang|Workflows: steps_by_device.yaml"
    message = "\n".join(lab.remote.log("%B"))
    assert "DataLab-Workflow-From: SQL Playground" in message
    assert f"DataLab-Tests: {done['test']}" in message
    assert len(lab.sandbox.steps) == 1  # the package's tests ran on the change first
    # The clone follows, so the Workflows tab lists it at once.
    listed = {w["path"] for w in client.get("/api/workflows").json()}
    assert "workflows/steps_by_device.yaml" in listed
    # A file already in the repo isn't replaced.
    again = client.post("/api/workflows/saves", json={"text": draft["text"]})
    assert again.status_code == 409 and "already has" in again.json()["detail"]


def test_what_may_be_participant_data_waits_for_the_person_to_confirm(lab, shared):
    client = shared
    synced(lab)
    draft = client.post(
        "/api/workflows/drafts",
        json={
            "name": "by_device",
            "destination": "dropbox-ihs-2025",
            # A participant's id typed into the SQL itself: it would go to GitHub.
            "queries": playground(
                BY_DEVICE.replace("GROUP BY", "AND STUDY_PARTICIPANT_ID <> 'SYN-0042'\nGROUP BY"),
                start_date="2025-04-01",
                skip_device="apple",
            ),
        },
    ).json()
    before = lab.remote.head()
    first = client.post("/api/workflows/saves", json={"text": draft["text"]}).json()
    held = settled_save(client, first["id"])
    assert held["state"] == "check_failed" and held["findings"], held
    assert "Confirm each one" in held["message"] and "agent" not in held["message"]
    assert lab.remote.head() == before
    confirmed = [f["id"] for f in held["findings"]]
    second = client.post(
        "/api/workflows/saves", json={"text": draft["text"], "confirmed": confirmed}
    ).json()
    assert settled_save(client, second["id"])["state"] == "saved"


def test_failing_package_tests_share_nothing(lab, shared):
    client = shared
    synced(lab)
    lab.sandbox.failing = 1
    draft = client.post(
        "/api/workflows/drafts",
        json={
            "name": "by_device",
            "queries": playground(BY_DEVICE, start_date="2025-04-01", skip_device="apple"),
        },
    ).json()
    before = lab.remote.head()
    started = client.post("/api/workflows/saves", json={"text": draft["text"]}).json()
    done = settled_save(client, started["id"])
    assert done["state"] == "tests_failed" and lab.remote.head() == before
    assert client.get("/api/workflows/saves/ws_nope").status_code == 404
