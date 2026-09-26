from __future__ import annotations

import csv
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from datalab.config import OracleSettings, QueryLimits, Settings
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.oracle import ExtractResult, QueryCancelled

COHORTS = frozenset({"IHS_2024", "IHS_2025"})


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
        with out_path.open("w", newline="") as handle:
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
