import asyncio
import json
from pathlib import Path

import pytest

from datalab import db
from datalab.config import QueryLimits
from datalab.data.access_log import AccessLog
from datalab.data.oracle import QueryCancelled
from datalab.data.service import DataService
from datalab.data.sqlcheck import SqlRejected
from tests.conftest import COHORTS, FakeDatabase

SQL = (
    "SELECT STUDY_PARTICIPANT_ID, TRACKERSTEPS FROM IHS_2025.VFITBITDAILYDATA "
    "WHERE RECORD_DATE >= :d"
)


@pytest.fixture
def log(tmp_path: Path) -> AccessLog:
    return AccessLog(db.connect(tmp_path / "db.sqlite"), tmp_path / "logs" / "audit.jsonl")


def service(database, log, **limits) -> DataService:
    return DataService(database, log, QueryLimits(**limits), COHORTS)


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
