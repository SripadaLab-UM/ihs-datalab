"""The SQL Playground's API: check, run, stop, results, history, catalog, export."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.config import PlaygroundSettings
from datalab.exports import MANIFEST
from tests.conftest import FakeDatabase

SQL = "SELECT STUDY_PARTICIPANT_ID, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA"


def make_client(settings, catalog, database):
    app = create_app(
        settings, database=database, catalog=catalog, manage_containers=False, protect_api=False
    )
    return TestClient(app)


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(settings, catalog, database):
    with make_client(settings, catalog, database) as client:
        yield client


def run(client, sql=SQL, binds=None) -> dict:
    """Start a query and wait for it to finish."""
    started = client.post("/api/sql/runs", json={"sql": sql, "binds": binds or {}})
    assert started.status_code == 202
    return client.get(f"/api/sql/runs/{started.json()['id']}", params={"wait": 10}).json()


def files_under(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []


def playground_folder(settings, client) -> Path:
    return settings.data_dir / "playground" / client.get("/api/sql/status").json()["playground_id"]


def test_status_says_what_the_playground_is(client):
    status = client.get("/api/sql/status").json()
    assert status["available"] is True
    assert status["playground_id"].startswith("pg_")
    assert status["preview_rows"] == 200
    # The same id every time, and after a restart.
    assert client.get("/api/sql/status").json()["playground_id"] == status["playground_id"]


def test_the_playground_id_survives_a_restart(settings, catalog, database):
    with make_client(settings, catalog, database) as first:
        before = first.get("/api/sql/status").json()["playground_id"]
    with make_client(settings, catalog, database) as second:
        assert second.get("/api/sql/status").json()["playground_id"] == before


def test_check_passes_a_good_query_and_names_its_tables_and_binds(client, database):
    result = client.post("/api/sql/check", json={"sql": SQL + " WHERE RECORD_DATE >= :d"}).json()
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["tables"] == ["IHS_2025.VFITBITDAILYDATA"]
    assert result["binds"] == ["d"]
    # Checking runs nothing and logs nothing.
    assert database.calls == []
    assert client.get("/api/sql/history").json() == []


def test_check_says_where_the_problem_is(client):
    sql = "SELECT STUDY_PARTICIPANT_ID,\n       NOPE\nFROM IHS_2025.VFITBITDAILYDATA"
    result = client.post("/api/sql/check", json={"sql": sql}).json()
    assert result["ok"] is False
    [error] = result["errors"]
    assert "NOPE" in error["message"]
    assert error["severity"] == "error"
    assert error["position"] == {"line": 2, "column": 8, "end_line": 2, "end_column": 12}


def test_check_gives_parse_errors_a_position(client):
    result = client.post("/api/sql/check", json={"sql": "\nSELECT FROM WHERE"}).json()
    [error] = result["errors"]
    assert error["position"]["line"] == 2
    assert error["position"]["column"] == 13  # WHERE


def test_check_warnings_have_positions_too(client):
    result = client.post(
        "/api/sql/check", json={"sql": "SELECT * FROM IHS_2025.VFITBITDAILYDATA"}
    ).json()
    assert result["ok"] is True
    [warning] = result["warnings"]
    assert warning["severity"] == "warning"
    assert warning["position"] == {"line": 1, "column": 8, "end_line": 1, "end_column": 9}


def test_a_query_runs_and_its_result_is_kept_in_the_playground(settings, client, database):
    done = run(client)
    assert done["state"] == "succeeded"
    assert done["row_count"] == 2
    assert [c["name"] for c in done["columns"]] == ["STUDY_PARTICIPANT_ID", "TRACKERSTEPS"]
    assert done["elapsed_seconds"] is not None
    assert database.calls == [(SQL, {})]

    page = client.get(f"/api/sql/results/{done['query_id']}").json()
    assert page["rows"] == [["SYN001", "8123"], ["SYN002", "4500"]]
    assert page["row_count"] == 2
    assert page["has_more"] is False

    result = playground_folder(settings, client) / "results" / f"{done['query_id']}.csv"
    assert result.is_file()

    [item] = client.get("/api/sql/history").json()
    assert item["query_id"] == done["query_id"]
    assert item["status"] == "succeeded"
    assert item["sql"] == SQL
    assert item["has_result"] is True


def test_playground_queries_are_logged_with_their_origin(settings, client):
    done = run(client)
    services = client.app.state.services  # type: ignore[attr-defined]
    owner = client.get("/api/sql/status").json()["playground_id"]
    [record] = services.access_log.for_origin("playground", owner)
    assert record.id == done["query_id"]
    audit = (settings.data_dir / "logs" / "audit.jsonl").read_text().splitlines()
    assert json.loads(audit[-1])["origin"] == "playground"


def test_a_refused_query_never_runs_or_leaves_files(settings, client, database):
    done = run(client, sql="DELETE FROM IHS_2025.VFITBITDAILYDATA")
    assert done["state"] == "rejected"
    assert "Only SELECT" in done["message"]
    assert done["diagnostic"]["position"]["column"] == 1
    assert done["query_id"] is None
    assert database.calls == []
    assert files_under(playground_folder(settings, client) / "results") == []
    assert files_under(settings.data_dir / "sessions") == []
    [item] = client.get("/api/sql/history").json()
    assert item["status"] == "rejected"
    assert item["has_result"] is False
    assert client.get(f"/api/sql/results/{item['query_id']}").status_code == 404


def test_missing_bind_values_are_refused(client, database):
    done = run(client, sql=SQL + " WHERE RECORD_DATE >= :d")
    assert done["state"] == "rejected"
    assert "Missing values" in done["message"]
    assert database.calls == []
    assert run(client, sql=SQL + " WHERE RECORD_DATE >= :d", binds={"d": "x"})["state"] == (
        "succeeded"
    )


def test_a_running_query_can_be_stopped(settings, catalog):
    database = FakeDatabase(block=True)
    with make_client(settings, catalog, database) as client:
        started = client.post("/api/sql/runs", json={"sql": SQL}).json()
        assert started["state"] == "running"
        assert database.started.wait(5)
        stopped = client.post(f"/api/sql/runs/{started['id']}/stop").json()
        assert stopped["state"] == "stopped"
        assert client.post(f"/api/sql/runs/{started['id']}/stop").status_code == 409
        [item] = client.get("/api/sql/history").json()
        assert item["status"] == "cancelled"
        assert item["has_result"] is False
        assert files_under(playground_folder(settings, client) / "results") == []


def test_waiting_for_a_run_returns_while_it_is_still_going(settings, catalog):
    database = FakeDatabase(block=True)
    with make_client(settings, catalog, database) as client:
        started = client.post("/api/sql/runs", json={"sql": SQL}).json()
        still = client.get(f"/api/sql/runs/{started['id']}", params={"wait": 0.2}).json()
        assert still["state"] == "running"
        client.post(f"/api/sql/runs/{started['id']}/stop")


def test_an_unknown_run_is_not_found(client):
    assert client.get("/api/sql/runs/pgr_nope").status_code == 404
    assert client.post("/api/sql/runs/pgr_nope/stop").status_code == 404


def test_a_failed_query_says_why(settings, catalog):
    class Failing(FakeDatabase):
        def extract_to_csv(self, *args, **kwargs):
            from datalab.data.oracle import QueryFailed

            raise QueryFailed("ORA-00942: table or view does not exist")

    with make_client(settings, catalog, Failing()) as client:
        done = run(client)
        assert done["state"] == "failed"
        assert done["message"].startswith("ORA-00942")


def test_results_never_land_in_a_conversation_folder(settings, client):
    cid = client.post("/api/conversations", json={"title": "A study"}).json()["id"]
    sessions = client.app.state.services.sessions  # type: ignore[attr-defined]
    conversation_results = sessions.paths(cid).oracle_results
    done = run(client)
    assert done["state"] == "succeeded"
    assert files_under(conversation_results) == []
    assert files_under(settings.data_dir / "sessions") == []
    [result] = files_under(settings.data_dir / "playground")[-1:]
    assert result.is_relative_to(playground_folder(settings, client) / "results")


def test_other_owners_queries_are_never_the_playgrounds(settings, client, tmp_path):
    services = client.app.state.services  # type: ignore[attr-defined]
    cid = client.post("/api/conversations", json={"title": "A study"}).json()["id"]

    async def elsewhere():
        conversation = await services.data.run_query(
            session_id=cid,
            sql=SQL,
            binds=None,
            results_dir=services.sessions.paths(cid).oracle_results,
        )
        other_playground = await services.data.run_query(
            session_id="pg_000000000000",
            sql=SQL,
            binds=None,
            results_dir=tmp_path / "other",
            origin="playground",
        )
        return conversation, other_playground

    conversation, other = asyncio.run(elsewhere())
    mine = run(client)
    history = client.get("/api/sql/history").json()
    assert [h["query_id"] for h in history] == [mine["query_id"]]
    for query_id in (conversation.query_id, other.query_id):
        assert client.get(f"/api/sql/results/{query_id}").status_code == 404
        exported = client.post(
            f"/api/sql/results/{query_id}/export", json={"destination_id": "practice"}
        )
        assert exported.status_code == 404
    # And the conversation's panel doesn't list the Playground's query.
    assert [r.id for r in services.access_log.for_session(cid)] == [conversation.query_id]


def test_a_logged_result_path_outside_the_playground_is_not_served(settings, client):
    done = run(client)
    services = client.app.state.services  # type: ignore[attr-defined]
    elsewhere = settings.data_dir / "elsewhere.csv"
    elsewhere.write_text("SECRET\n1\n")
    services.access_log._db.execute(
        "UPDATE queries SET result_path = ? WHERE id = ?", (str(elsewhere), done["query_id"])
    )
    assert client.get(f"/api/sql/results/{done['query_id']}").status_code == 404


def test_results_are_paged_within_the_preview_limit(settings, catalog):
    database = FakeDatabase(rows=[[f"SYN{i:03}", i] for i in range(450)])
    settings = dataclasses.replace(settings, playground=PlaygroundSettings(preview_rows=200))
    with make_client(settings, catalog, database) as client:
        done = run(client)
        assert done["row_count"] == 450
        query_id = done["query_id"]
        first = client.get(f"/api/sql/results/{query_id}", params={"limit": 100}).json()
        assert len(first["rows"]) == 100
        assert first["has_more"] is True
        assert first["preview_limit"] == 200
        last = client.get(
            f"/api/sql/results/{query_id}", params={"offset": 150, "limit": 100}
        ).json()
        assert [r[0] for r in last["rows"]] == [f"SYN{i:03}" for i in range(150, 200)]
        assert last["has_more"] is False
        beyond = client.get(f"/api/sql/results/{query_id}", params={"offset": 300}).json()
        assert beyond["rows"] == []
        assert beyond["row_count"] == 450


def test_long_values_are_shortened_in_the_grid(settings, catalog):
    database = FakeDatabase(rows=[["SYN001", "x" * 5000]])
    with make_client(settings, catalog, database) as client:
        done = run(client)
        [[_, value]] = client.get(f"/api/sql/results/{done['query_id']}").json()["rows"]
        assert len(value) == 2001 and value.endswith("…")


def test_the_catalog_lists_every_cohort_with_columns_and_comments(client):
    cohorts = client.get("/api/sql/catalog").json()
    assert [c["schema_name"] for c in cohorts] == ["IHS_2024", "IHS_2025"]
    tables = {t["name"]: t for t in cohorts[1]["tables"]}
    fitbit = tables["VFITBITDAILYDATA"]
    assert fitbit["comment"] == "Fitbit daily summary"
    assert fitbit["columns"][2] == {
        "name": "TRACKERSTEPS",
        "type": "NUMBER",
        "nullable": True,
        "comment": "Steps from the tracker",
    }


def test_the_catalog_can_be_searched(client):
    hits = client.get("/api/sql/catalog/search", params={"q": "mood"}).json()
    assert hits[0]["schema_name"] == "IHS_2025"
    assert hits[0]["name"] == "VW_DAILY_MOOD"
    only_2024 = client.get(
        "/api/sql/catalog/search", params={"q": "fitbit", "schemas": ["IHS_2024"]}
    ).json()
    assert {h["schema_name"] for h in only_2024} == {"IHS_2024"}


def test_a_result_is_exported_with_a_manifest(settings, client):
    done = run(client)
    exported = client.post(
        f"/api/sql/results/{done['query_id']}/export", json={"destination_id": "practice"}
    )
    assert exported.status_code == 201
    folder = Path(exported.json()["folder"])
    assert folder.parent == settings.data_dir / "practice-exports"
    assert exported.json()["files"] == [f"files/query-results/{done['query_id']}.csv"]
    copied = folder / "files" / "query-results" / f"{done['query_id']}.csv"
    assert copied.read_text().startswith("STUDY_PARTICIPANT_ID,TRACKERSTEPS")
    manifest = json.loads((folder / MANIFEST).read_text())
    assert manifest["profile"] == "practice"
    assert manifest["contains_study_data"] is False
    assert manifest["query"]["id"] == done["query_id"]
    assert manifest["query"]["sql"] == SQL
    assert manifest["query"]["row_count"] == 2
    assert manifest["playground"]["id"].startswith("pg_")
    audit = (settings.data_dir / "logs" / "audit.jsonl").read_text().splitlines()
    assert json.loads(audit[-1])["event"] == "export"


def test_export_needs_a_known_folder_and_a_result(client):
    done = run(client)
    unknown = client.post(
        f"/api/sql/results/{done['query_id']}/export", json={"destination_id": "dest_x"}
    )
    assert unknown.status_code == 404
    refused = run(client, sql="DROP TABLE IHS_2025.VFITBITDAILYDATA")
    [item] = [h for h in client.get("/api/sql/history").json() if h["status"] == "rejected"]
    assert refused["state"] == "rejected"
    no_result = client.post(
        f"/api/sql/results/{item['query_id']}/export", json={"destination_id": "practice"}
    )
    assert no_result.status_code == 404


def test_the_routes_need_the_browser_session(settings, catalog, database):
    app = create_app(settings, database=database, catalog=catalog, manage_containers=False)
    with TestClient(app) as client:
        assert client.post("/api/sql/check", json={"sql": SQL}).status_code in (401, 403)
        assert client.post("/api/sql/runs", json={"sql": SQL}).status_code in (401, 403)
        assert client.get("/api/sql/catalog").status_code in (401, 403)
    assert database.calls == []
