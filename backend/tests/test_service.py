import asyncio
import json
import threading
from pathlib import Path

import pytest

from datalab import db
from datalab.config import QueryLimits
from datalab.data.access_log import AccessLog
from datalab.data.oracle import QueryCancelled, QueryFailed
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from tests.conftest import COHORTS, FakeDatabase, sample_catalog

SQL = (
    "SELECT STUDY_PARTICIPANT_ID, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA "
    "WHERE RECORD_DATE >= :d"
)


@pytest.fixture
def log(tmp_path: Path) -> AccessLog:
    return AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")


def service(database, log, **limits) -> DataService:
    return DataService(database, log, QueryLimits(**limits), COHORTS, sample_catalog())


def audit_entries(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "logs" / "audit.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


async def test_successful_query_is_saved_and_logged(tmp_path, log):
    database = FakeDatabase()
    outcome = await service(database, log).run_query(
        session_id="s1", sql=SQL, binds={"D": "2025-04-01"}, results_dir=tmp_path / "results"
    )
    assert outcome.row_count == 2
    assert outcome.result_path.exists()
    assert outcome.tables == ["IHS_2025.VFITBITDAILYDATA"]

    [record] = log.for_session("s1")
    assert record.status == "succeeded"
    assert record.sql_text.startswith("SELECT")
    assert record.binds == {"D": "2025-04-01"}


async def test_audit_log_holds_metadata_only(tmp_path, log):
    await service(FakeDatabase(), log).run_query(
        session_id="s1", sql=SQL, binds={"d": "2025-04-01"}, results_dir=tmp_path / "r"
    )
    [entry] = audit_entries(tmp_path)
    assert entry["tables"] == ["IHS_2025.VFITBITDAILYDATA"]
    assert entry["bind_names"] == ["d"]
    text = json.dumps(entry)
    assert "SELECT" not in text and "2025-04-01" not in text
    assert len(entry["sql_sha256"]) == 64


async def test_rejected_query_never_reaches_the_database(tmp_path, log):
    database = FakeDatabase()
    with pytest.raises(SqlRejected):
        await service(database, log).run_query(
            session_id="s1", sql="DELETE FROM IHS_2025.T", binds=None, results_dir=tmp_path
        )
    assert database.calls == []
    [record] = log.for_session("s1")
    assert record.status == "rejected"


async def test_missing_and_extra_binds_are_rejected(tmp_path, log):
    svc = service(FakeDatabase(), log)
    with pytest.raises(SqlRejected, match="Missing"):
        await svc.run_query(session_id="s", sql=SQL, binds={}, results_dir=tmp_path)
    with pytest.raises(SqlRejected, match="doesn't use"):
        await svc.run_query(session_id="s", sql=SQL, binds={"d": 1, "x": 2}, results_dir=tmp_path)


async def test_stopping_a_query_cancels_the_database_call(tmp_path, log):
    database = FakeDatabase(block=True)
    task = asyncio.create_task(
        service(database, log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path
        )
    )
    await asyncio.to_thread(database.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [record] = log.for_session("s1")
    assert record.status == "cancelled"


async def test_database_cancellation_is_reported(tmp_path, log):
    class Cancelling(FakeDatabase):
        def extract_to_csv(self, *args, **kwargs):
            raise QueryCancelled("The query was stopped.")

    with pytest.raises(QueryCancelled):
        await service(Cancelling(), log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path
        )
    assert log.for_session("s1")[0].status == "cancelled"


async def test_concurrency_is_limited(tmp_path, log):
    database = FakeDatabase(block=True)
    svc = service(database, log, max_concurrent_queries=1)
    first = asyncio.create_task(
        svc.run_query(session_id="a", sql=SQL, binds={"d": 1}, results_dir=tmp_path)
    )
    await asyncio.to_thread(database.started.wait, 5)
    second = asyncio.create_task(
        svc.run_query(session_id="b", sql=SQL, binds={"d": 1}, results_dir=tmp_path)
    )
    await asyncio.sleep(0.2)
    assert len(database.calls) == 1  # the second query is waiting for a slot
    for task in (first, second):
        task.cancel()
    await asyncio.gather(first, second, return_exceptions=True)


async def test_queries_are_listed_by_origin_and_owner(tmp_path, log):
    svc = service(FakeDatabase(), log)
    await svc.run_query(session_id="c_1", sql=SQL, binds={"d": 1}, results_dir=tmp_path)
    await svc.run_query(
        session_id="pg_1", sql=SQL, binds={"d": 1}, results_dir=tmp_path, origin="playground"
    )
    with pytest.raises(SqlRejected):
        await svc.run_query(
            session_id="run_1",
            sql="DELETE FROM X",
            binds=None,
            results_dir=tmp_path,
            origin="run",
            allowed_tables=frozenset(),
        )
    # The conversation's panel shows only its own queries, as before.
    [mine] = log.for_session("c_1")
    assert mine.origin == "conversation"
    assert log.for_origin("conversation", "c_1") == [mine]
    [playground] = log.for_origin("playground", "pg_1")
    assert (playground.origin, playground.status) == ("playground", "succeeded")
    [run] = log.for_origin("run", "run_1")
    assert (run.origin, run.status) == ("run", "rejected")
    assert log.for_origin("run", "run_2") == []
    # The audit log says what each query was for.
    assert [e["origin"] for e in audit_entries(tmp_path)] == ["conversation", "playground", "run"]


@pytest.mark.parametrize(
    ("origin", "owner"),
    [("playground", "c_1"), ("run", "pg_1"), ("conversation", "run_1"), ("conversation", "pg_x")],
)
async def test_an_owner_that_doesnt_match_its_origin_is_refused_before_anything_runs(
    tmp_path, log, origin, owner
):
    database = FakeDatabase()
    with pytest.raises(ValueError, match=r"owner id|can't have"):
        await service(database, log).run_query(
            session_id=owner, sql=SQL, binds={"d": 1}, results_dir=tmp_path, origin=origin
        )
    assert database.calls == []
    assert not (tmp_path / "logs" / "audit.jsonl").exists() or audit_entries(tmp_path) == []
    with pytest.raises(ValueError):
        log.for_origin(origin, owner)


async def test_a_database_that_cant_be_reached_fails_the_query(tmp_path, log, monkeypatch):
    """A refused connection is a failed query, never one left running."""
    import oracledb

    from datalab.config import PRACTICE_ORACLE
    from datalab.data.oracle import OracleDatabase

    def refuse(**kwargs):
        raise oracledb.OperationalError("DPY-6005: cannot connect to database")

    monkeypatch.setattr(oracledb, "connect", refuse)
    database = OracleDatabase(PRACTICE_ORACLE, "x", QueryLimits())
    with pytest.raises(QueryFailed, match="Couldn't connect"):
        await service(database, log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path / "r"
        )
    [record] = log.for_session("s1")
    assert record.status == "failed"
    assert list((tmp_path / "r").iterdir()) == []


async def test_an_unexpected_error_is_logged_as_failed(tmp_path, log):
    class Broken(FakeDatabase):
        def extract_to_csv(self, *args, **kwargs):
            raise RuntimeError("a bug")

    with pytest.raises(RuntimeError):
        await service(Broken(), log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path
        )
    [record] = log.for_session("s1")
    assert record.status == "failed"


class SlowToStop(FakeDatabase):
    """Oracle taking its time to cancel: the call ends only when `release` is set,
    and (with `finish`) writes its whole result even though it was stopped."""

    def __init__(self, finish: bool = False) -> None:
        super().__init__()
        self.release = threading.Event()
        self.finish = finish

    def extract_to_csv(self, sql, binds, out_path, *, cancel, **kwargs):
        if self.calls:  # every query after the first runs at once
            return super().extract_to_csv(sql, binds, out_path, cancel=cancel, **kwargs)
        self.calls.append((sql, dict(binds)))
        self.started.set()
        cancel.wait(timeout=10)
        self.release.wait(timeout=10)
        if self.finish:
            self.calls.pop()
            return super().extract_to_csv(sql, binds, out_path, cancel=cancel, **kwargs)
        raise QueryCancelled("The query was stopped.")


async def test_stopping_twice_keeps_the_slot_until_oracle_has_stopped(tmp_path, log):
    database = SlowToStop()
    svc = service(database, log, max_concurrent_queries=1)
    first = asyncio.create_task(
        svc.run_query(session_id="a", sql=SQL, binds={"d": 1}, results_dir=tmp_path)
    )
    await asyncio.to_thread(database.started.wait, 5)
    second = asyncio.create_task(
        svc.run_query(session_id="b", sql=SQL, binds={"d": 1}, results_dir=tmp_path)
    )
    for _ in range(2):  # Stop, and Stop again while Oracle is still cancelling
        first.cancel()
        await asyncio.sleep(0.1)
    assert not first.done()
    assert len(database.calls) == 1  # the second query still waits for the slot
    assert log.for_session("a")[0].status == "running"
    database.release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await second).row_count == 2
    assert log.for_session("a")[0].status == "cancelled"


async def test_a_stop_that_races_the_end_keeps_no_result(tmp_path, log):
    database = SlowToStop(finish=True)
    task = asyncio.create_task(
        service(database, log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path
        )
    )
    await asyncio.to_thread(database.started.wait, 5)
    task.cancel()
    database.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    [record] = log.for_session("s1")
    assert record.status == "cancelled"
    assert list(tmp_path.glob("*.csv")) == []


def test_queries_a_previous_run_left_running_are_ended(tmp_path, log):
    log.started(query_id="q1", session_id="c1", sql="SELECT 1", binds={}, tables=[])
    log.started(
        query_id="q2", session_id="pg_1", sql="SELECT 1", binds={}, tables=[], origin="playground"
    )
    assert log.end_cut_off_queries() == 2
    [chat] = log.for_session("c1")
    [playground] = log.for_origin("playground", "pg_1")
    for record in (chat, playground):
        assert record.status == "cancelled"
        assert "DataLab stopped" in (record.message or "")
    assert log.end_cut_off_queries() == 0


def test_a_result_written_just_before_datalab_stopped_is_removed(tmp_path, log):
    """Cut off after the result was written but before it was logged: the
    result goes, so the agent can't read what the log says wasn't kept."""
    result = tmp_path / "q_20260101T000000_aaaaaa.csv"
    partial = tmp_path / "q_20260101T000001_bbbbbb.csv.partial"
    log.started(
        query_id=result.stem, session_id="c1", sql="S", binds={}, tables=[], result_path=result
    )
    log.started(
        query_id="q_20260101T000001_bbbbbb",
        session_id="c1",
        sql="S",
        binds={},
        tables=[],
        result_path=partial.with_suffix(""),
    )
    result.write_text("A\n1\n")
    partial.write_text("A\n")
    log.end_cut_off_queries()
    assert not result.exists() and not partial.exists()
    for record in log.for_session("c1"):
        assert record.message == "DataLab stopped before this query finished. Nothing was kept."
        assert record.result_path is None


async def test_the_audit_log_tells_a_stop_from_datalab_stopping(tmp_path, log):
    database = FakeDatabase(block=True)
    task = asyncio.create_task(
        service(database, log).run_query(
            session_id="s1", sql=SQL, binds={"d": 1}, results_dir=tmp_path
        )
    )
    await asyncio.to_thread(database.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    log.started(query_id="q2", session_id="s1", sql="S", binds={}, tables=[])
    log.end_cut_off_queries()
    stopped, cut_off = audit_entries(tmp_path)
    assert (stopped["status"], stopped["reason"]) == ("cancelled", "stopped")
    assert (cut_off["status"], cut_off["reason"]) == ("cancelled", "datalab_stopped")


def test_a_running_query_has_no_result_file_in_its_panel(settings, catalog):
    """The planned result path of a running query isn't shown as its result."""
    from fastapi.testclient import TestClient

    from datalab.app import create_app

    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )
    with TestClient(app) as client:
        cid = client.post("/api/conversations", json={"title": "A study"}).json()["id"]
        client.app.state.services.access_log.started(  # type: ignore[attr-defined]
            query_id="q1",
            session_id=cid,
            sql="S",
            binds={},
            tables=[],
            result_path=tmp_path_of(settings),
        )
        [entry] = client.get(f"/api/conversations/{cid}/data-accessed").json()
        assert entry["status"] == "running"
        assert entry["result_file"] is None


def tmp_path_of(settings):
    return settings.data_dir / "sessions" / "x" / "oracle" / "q1.csv"
