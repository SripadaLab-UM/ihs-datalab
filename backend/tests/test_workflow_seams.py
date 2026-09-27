"""What the workflow runner changed in shared code: migration 0008, export
destinations with keys, the data service's allowed tables, and the step
container's flags."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from datalab import db
from datalab.config import QueryLimits
from datalab.data.access_log import AccessLog
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from datalab.db import rollback
from datalab.exports import DestinationStore
from datalab.workflows import sandbox as sandbox_module
from datalab.workflows.records import RunStore
from datalab.workflows.sandbox import Bind, ContainerStep, DockerSandbox, StepLimits
from tests.conftest import COHORTS, FakeDatabase, sample_catalog
from tests.test_backups import ALL, open_database, use_migrations


def test_destinations_can_be_added_after_0008_adds_a_column(tmp_path):
    """The spike found `INSERT INTO export_destinations VALUES (?, ?, ?, ?)`
    failing once 0008 added `key` ("5 columns but 4 values")."""
    connection = db.connect(tmp_path / "datalab.sqlite")
    columns = [r[1] for r in connection.execute("PRAGMA table_info(export_destinations)")]
    assert columns == ["id", "name", "path", "added_at", "key"]
    store = DestinationStore(connection)
    first = store.add("Dropbox", tmp_path / "dropbox")
    second = store.add("Box", tmp_path / "box")
    assert store.get(first.id) == first and first.key is None
    assert store.set_key(first.id, "dropbox-ihs-2025")
    assert store.by_key("dropbox-ihs-2025").id == first.id  # type: ignore[union-attr]
    # A key names one folder per computer: moving it takes it off the other.
    assert store.set_key(second.id, "dropbox-ihs-2025")
    assert store.by_key("dropbox-ihs-2025").id == second.id  # type: ignore[union-attr]
    assert store.get(first.id).key is None  # type: ignore[union-attr]
    assert not store.set_key("dest_missing", "other")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE export_destinations SET key = 'dropbox-ihs-2025'")


def test_0008_holds_a_run_its_steps_and_a_delivery(tmp_path):
    connection = db.connect(tmp_path / "datalab.sqlite")
    store = RunStore(connection)
    fields = {
        "id": "run_20260927T110059_4c7e20",
        "workflow_name": "weekly",
        "mode": "run",
        "status": "running",
        "started_at": "now",
        "started_by": "T <t@example.org>",
        "workflow_path": "weekly.yaml",
        "workflow_source": "file",
        "workflow_blob": "sha256:00",
        "workflow_text": "name: weekly",
        "image_ref": "datalab-agent:dev",
        "image_digest": "sha256:ab",
        "image_platform": "linux/arm64",
        "host_platform": "linux/arm64",
        "r_packages_sha256": "ff",
        "runner_version": "datalab 0.1.0",
        "runtime": {"env": {"TZ": "Etc/UTC"}},
        "params": {"min_cell": 11},
        "seed": 1,
        "reads": ["IHS_2025.X"],
        "run_dir": "runs/run_x",
    }
    store.create_run(fields, [("extract", 0, "sql"), ("check", 1, "qc_builtin")])
    store.update_step(
        fields["id"], "extract", status="succeeded", outputs={"final": {"file": "a.csv"}}
    )
    store.add_delivery(
        {
            "id": "dl_1",
            "run_id": fields["id"],
            "destination_key": "k",
            "destination_path": "/x",
            "folder": "/x/y",
            "files": [],
            "manifest_sha256": "00",
            "delivered_at": "now",
        }
    )
    run = store.get_run(fields["id"])
    assert (
        run is not None and run["params"] == {"min_cell": 11} and run["delivery_status"] == "none"
    )
    assert [s["status"] for s in store.steps(fields["id"])] == ["succeeded", "pending"]
    assert store.mark_interrupted() == [fields["id"]]
    assert [s["status"] for s in store.steps(fields["id"])] == ["succeeded", "skipped"]
    with pytest.raises(sqlite3.IntegrityError):
        store.create_run({**fields, "id": "run_2", "mode": "rerun"}, [])
    with pytest.raises(KeyError):
        store.update_run(fields["id"], nonsense=1)


async def test_a_query_outside_the_allowed_tables_is_refused_and_logged(tmp_path: Path):
    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    database = FakeDatabase()
    service = DataService(database, log, QueryLimits(), COHORTS, sample_catalog())
    sql = "SELECT STUDY_PARTICIPANT_ID, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA"
    with pytest.raises(SqlRejected, match="doesn't declare"):
        await service.run_query(
            session_id="run_1",
            sql=sql,
            binds={},
            results_dir=tmp_path,
            origin="run",
            allowed_tables=frozenset({"IHS_2025.VW_DAILY_MOOD"}),
        )
    assert database.calls == []
    [record] = log.for_origin("run", "run_1")
    assert record.status == "rejected"
    outcome = await service.run_query(
        session_id="run_1",
        sql=sql,
        binds={},
        results_dir=tmp_path,
        origin="run",
        allowed_tables=frozenset({"IHS_2025.VFITBITDAILYDATA"}),
    )
    assert outcome.tables == ["IHS_2025.VFITBITDAILYDATA"]


def test_step_containers_get_the_spike_flags(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    sandbox = DockerSandbox(
        profile="practice",
        instance="0123456789abcdef",
        limits=StepLimits(memory="2g", cpus="1", pids=128),
        cache_dir=tmp_path,
    )
    step = ContainerStep(
        run_id="run_20260927T110059_4c7e20",
        name="summary",
        image="sha256:" + "ab" * 32,
        binds=[Bind(tmp_path / "in, odd", "/run/in/raw"), Bind(tmp_path, "/run/out", False)],
        watch=tmp_path,
        max_bytes=1,
    )
    argv = sandbox.argv(step)
    joined = " ".join(argv)
    for flag in (
        "--rm --pull never",
        "--network none",
        "--read-only",
        "--tmpfs /tmp:rw,size=1g,mode=1777,nosuid,nodev,noexec",
        "--user 10004:10004",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--init",
        "--pids-limit 128",
        "--memory 2g --memory-swap 2g",
        "--cpus 1",
        "--label datalab.run=run_20260927T110059_4c7e20",
        "--label datalab.profile=practice",
        "--label datalab.instance=0123456789abcdef",
        "--env TZ=Etc/UTC",
        "--env OPENBLAS_NUM_THREADS=1",
        "--env R_DATATABLE_NUM_THREADS=1",
    ):
        assert flag in joined, flag
    assert "-v" not in argv
    # CSV-quoted, so a comma in a folder name can't add a mount option.
    assert f'type=bind,"source={tmp_path}/in, odd",target=/run/in/raw,readonly' in argv
    assert f"type=bind,source={tmp_path},target=/run/out" in argv
    assert argv[-4:] == ["sha256:" + "ab" * 32, "Rscript", "--vanilla", "/run/datalab/run_step.R"]


def run_fields(run_id: str, **more) -> dict:
    return {
        "id": run_id,
        "workflow_name": "weekly",
        "mode": "run",
        "status": "succeeded",
        "started_at": "2026-09-27T12:00:00",
        "started_by": "T <t@example.org>",
        "workflow_path": "weekly.yaml",
        "workflow_source": "file",
        "workflow_blob": "sha256:00",
        "workflow_text": "name: weekly",
        "image_ref": "datalab-agent:dev",
        "image_digest": "sha256:ab",
        "image_platform": "linux/arm64",
        "host_platform": "linux/arm64",
        "r_packages_sha256": "ff",
        "runner_version": "datalab 0.1.0",
        "runtime": {},
        "params": {},
        "seed": 1,
        "reads": [],
        "run_dir": f"runs/{run_id}",
        **more,
    }


def test_deleting_a_replayed_run_keeps_its_replays(tmp_path):
    connection = db.connect(tmp_path / "datalab.sqlite")
    store = RunStore(connection)
    store.create_run(run_fields("run_a"), [("extract", 0, "sql")])
    store.create_run(run_fields("run_b", mode="replay", of_run="run_a"), [])
    connection.execute("DELETE FROM workflow_runs WHERE id = 'run_a'")
    replay = store.get_run("run_b")
    assert replay is not None and replay["of_run"] is None
    assert store.steps("run_a") == []


def test_rolling_back_before_0008_lists_runs_and_deliveries_as_dropped(tmp_path, monkeypatch):
    path = tmp_path / "datalab.sqlite"
    before = next(i for i, m in enumerate(ALL) if m.name.startswith("0008"))
    older = use_migrations(monkeypatch, before)
    open_database(path, version="0.1.0").close()
    use_migrations(monkeypatch, None)
    connection = open_database(path, version="0.2.0")
    connection.row_factory = sqlite3.Row
    store = RunStore(connection)
    store.create_run(run_fields("run_a"), [("extract", 0, "sql")])
    store.add_delivery(
        {
            "id": "dl_1",
            "run_id": "run_a",
            "destination_key": "k",
            "destination_path": "/x",
            "folder": "/x/y",
            "files": [],
            "manifest_sha256": "00",
            "delivered_at": "now",
        }
    )
    connection.close()
    losses = {loss.table: loss for loss in rollback.plan(path, older).losses}
    assert losses["workflow_runs"].added == 1
    assert losses["workflow_run_steps"].added == 1
    assert losses["workflow_run_deliveries"].added == 1
    assert "record.json" in losses["workflow_runs"].examples[0]
    lines = rollback.describe(list(losses.values()))
    assert "workflow runs: 1 new" in lines and "workflow run deliveries: 1 new" in lines


async def test_a_run_query_must_say_which_tables_it_may_read(tmp_path: Path):
    log = AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")
    service = DataService(FakeDatabase(), log, QueryLimits(), COHORTS, sample_catalog())
    with pytest.raises(ValueError, match="declared tables"):
        await service.run_query(
            session_id="run_1",
            sql="SELECT STUDY_PARTICIPANT_ID FROM IHS_2025.VFITBITDAILYDATA",
            binds={},
            results_dir=tmp_path,
            origin="run",
        )


def test_steps_never_run_as_root(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(sandbox_module.os, "getuid", lambda: 0)
    assert sandbox_module._user() == "10004:10004"
    monkeypatch.setattr(sandbox_module.os, "getuid", lambda: 1001)
    monkeypatch.setattr(sandbox_module.os, "getgid", lambda: 1001)
    assert sandbox_module._user() == "1001:1001"
