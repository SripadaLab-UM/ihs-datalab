from __future__ import annotations

import csv
import os
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from datalab import app, credentials, datalock
from datalab.config import OracleSettings, QueryLimits, Settings
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.oracle import ExtractResult, QueryCancelled

COHORTS = frozenset({"IHS_2024", "IHS_2025"})


@pytest.fixture(autouse=True)
def no_real_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach U-M GPT with the real key from this computer's
    keychain (conversation titles, for one, would ask the model)."""
    real = credentials._from_keychain
    monkeypatch.delenv("DATALAB_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(
        credentials,
        "_from_keychain",
        lambda service, account: (
            None if service == credentials.MODEL_KEY_SERVICE else real(service, account)
        ),
    )


# The real check, for the test that it's there.
REQUIRE_DATA_FOLDER_LOCK = app.require_data_folder_lock


@pytest.fixture(autouse=True)
def lock_data_folders_for_create_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app needs its data folder locked, as the CLI does before calling
    it. In tests, it takes the lock itself (released after each test);
    test_app_needs_the_data_folder_lock checks the real requirement."""
    monkeypatch.setattr(
        app,
        "require_data_folder_lock",
        lambda settings: datalock.refuse_second_instance(settings.data_dir, settings.profile),
    )


class MemoryKeychain:
    """Stands in for the OS keychain, for the GitHub sign-in."""

    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], str] = {}
        self.writes = 0

    def get_password(self, service: str, account: str) -> str | None:
        return self.saved.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.writes += 1
        self.saved[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        from keyring.errors import PasswordDeleteError

        if self.saved.pop((service, account), None) is None:
            raise PasswordDeleteError("not saved")


@pytest.fixture(autouse=True)
def github_keychain(monkeypatch: pytest.MonkeyPatch) -> MemoryKeychain:
    """No test reads or writes a real GitHub sign-in in this computer's keychain."""
    from datalab.repos import github

    fake = MemoryKeychain()
    monkeypatch.setattr(github, "keyring", fake)
    return fake


@pytest.fixture(autouse=True)
def release_data_folder_locks() -> Iterator[None]:
    """No test keeps a data folder locked for the rest of the run."""
    yield
    datalock.release_all()


class FakeDatabase:
    """Stands in for Oracle: returns fixed rows, or blocks until cancelled."""

    def __init__(self, rows: list[list[Any]] | None = None, *, block: bool = False) -> None:
        self.rows = rows if rows is not None else [["SYN001", 8123], ["SYN002", 4500]]
        self.block = block
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = threading.Event()

    def extract_to_csv(
        self,
        sql: str,
        binds: Mapping[str, Any],
        out_path: Path,
        *,
        max_rows: int,
        max_bytes: int,
        preview_rows: int,
        cancel: threading.Event,
    ) -> ExtractResult:
        self.calls.append((sql, dict(binds)))
        self.started.set()
        if self.block:
            cancel.wait(timeout=10)
            raise QueryCancelled("The query was stopped.")
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["STUDY_PARTICIPANT_ID", "TRACKERSTEPS"])
            writer.writerows(self.rows)
        return ExtractResult(
            columns=["STUDY_PARTICIPANT_ID", "TRACKERSTEPS"],
            preview=[[str(v) for v in row] for row in self.rows[:preview_rows]],
            row_count=len(self.rows),
            bytes_written=out_path.stat().st_size,
            elapsed_seconds=0.01,
        )


@pytest.fixture
def catalog() -> Catalog:
    return sample_catalog()


def sample_catalog() -> Catalog:
    return Catalog(
        [
            TableInfo(
                "IHS_2025",
                "VFITBITDAILYDATA",
                "VIEW",
                "Fitbit daily summary",
                [
                    Column("STUDY_PARTICIPANT_ID", "VARCHAR2(64)", False, "Participant"),
                    Column("RECORD_DATE", "DATE", False),
                    Column("TRACKERSTEPS", "NUMBER", True, "Steps from the tracker"),
                ],
            ),
            TableInfo(
                "IHS_2024",
                "VFITBITDAILYDATA",
                "VIEW",
                "",
                [Column("STUDY_PARTICIPANT_ID", "VARCHAR2(64)"), Column("STEPS", "NUMBER")],
            ),
            TableInfo(
                "IHS_2025",
                "VW_DAILY_MOOD",
                "VIEW",
                "Daily mood ratings",
                [Column("MOOD", "NUMBER", True, "Mood score 1-10")],
            ),
        ]
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        profile="practice",
        data_dir=tmp_path / "data",
        oracle=OracleSettings(
            host="127.0.0.1",
            port=1522,
            service="FREEPDB1",
            user="DATALAB_RO",
            keychain_service="test",
            read_only_roles=("IHS_2025_RO",),
            allowed_schemas=COHORTS,
        ),
        limits=QueryLimits(max_concurrent_queries=1),
    )


@contextmanager
def live_server(app) -> Iterator[str]:
    """Run `app` on a real local port, for tests that need real HTTP behaviour."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # In CI (Linux), containers reach the host through the Docker bridge.
    host = os.environ.get("DATALAB_HOST", "127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
