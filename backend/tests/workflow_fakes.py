"""Test doubles for the workflow runner: a SQLite "Oracle" over synthetic-like
rows, and a sandbox that runs Python stand-ins for R steps.

All data is invented: participant ids are SYN-####.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import sqlite3
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from datalab import db
from datalab.config import OracleSettings, QueryLimits, Settings, WorkflowSettings
from datalab.data.access_log import AccessLog
from datalab.data.catalog import Catalog, Column, TableInfo
from datalab.data.oracle import ExtractResult, QueryCancelled
from datalab.data.service import DataService
from datalab.exports import DestinationStore
from datalab.workflows.records import RunStore
from datalab.workflows.runner import WorkflowRunner
from datalab.workflows.sandbox import ContainerOutcome, ContainerStep, ImageFacts
from datalab.workflows.source import WorkflowFolder

TABLE = "IHS_2025.WEARABLE_DAILY"
COLUMNS = ["STUDY_PARTICIPANT_ID", "RECORD_DATE", "DEVICE", "STEPS"]
DIGEST = "sha256:" + "ab" * 32


def synthetic_rows(participants: int = 40, days: int = 28, seed: int = 7) -> list[list[Any]]:
    """Daily steps for made-up participants (SYN-####), a few days missing."""
    rng = random.Random(seed)
    rows = []
    for p in range(participants):
        # Apple Watch is rare, so its weekly cells are small.
        share = p / participants
        device = "fitbit" if share < 0.6 else "garmin" if share < 0.9 else "apple"
        for d in range(days):
            if rng.random() < 0.05:
                continue
            steps = "" if rng.random() < 0.03 else str(rng.randint(1500, 16000))
            rows.append([f"SYN-{p + 1:04d}", f"2025-04-{d + 1:02d} 00:00:00", device, steps])
    return rows


def catalog() -> Catalog:
    return Catalog(
        [
            TableInfo(
                "IHS_2025",
                "WEARABLE_DAILY",
                "VIEW",
                "Synthetic daily wearable data",
                [
                    Column("STUDY_PARTICIPANT_ID", "VARCHAR2(16)"),
                    Column("RECORD_DATE", "DATE"),
                    Column("DEVICE", "VARCHAR2(16)"),
                    Column("STEPS", "NUMBER"),
                ],
            ),
            TableInfo(
                "IHS_2025",
                "PARTICIPANTS",
                "VIEW",
                "",
                [Column("STUDY_PARTICIPANT_ID", "VARCHAR2(16)"), Column("COHORT", "VARCHAR2(8)")],
            ),
        ]
    )


class SqliteDatabase:
    """Stands in for Oracle: runs the checked SQL in SQLite over synthetic rows,
    and writes the CSV the way oracle.py does ("\\r\\n" line ends)."""

    def __init__(self, rows: list[list[Any]] | None = None) -> None:
        self.rows = rows if rows is not None else synthetic_rows()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.block = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.execute("ATTACH ':memory:' AS IHS_2025")
        connection.create_function("TO_DATE", 2, lambda s, _fmt: f"{s} 00:00:00")
        connection.execute(f"CREATE TABLE {TABLE} ({', '.join(COLUMNS)})")
        connection.executemany(
            f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?)",
            ([None if v == "" else v for v in row] for row in self.rows),
        )
        connection.execute("CREATE TABLE IHS_2025.PARTICIPANTS (STUDY_PARTICIPANT_ID, COHORT)")
        return connection

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
        with self._lock:
            self.calls.append((sql, dict(binds)))
        if self.block:
            cancel.wait(timeout=10)
            raise QueryCancelled("The query was stopped.")
        connection = self._connect()
        cursor = connection.execute(sql, dict(binds))
        columns = [d[0] for d in cursor.description]
        count = 0
        preview: list[list[str]] = []
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in cursor:
                values = ["" if v is None else str(v) for v in row]
                writer.writerow(values)
                if len(preview) < preview_rows:
                    preview.append(values)
                count += 1
        return ExtractResult(
            columns=columns,
            preview=preview,
            row_count=count,
            bytes_written=out_path.stat().st_size,
            elapsed_seconds=0.01,
        )


Handler = Callable[["FakeRun"], int]


class FakeRun:
    """One container step as a Python handler sees it: host paths for the contract."""

    def __init__(self, step: ContainerStep) -> None:
        self.step = step
        self.mounts = {b.target: b.source for b in step.binds}
        spec_dir = self.mounts.get("/run/step")
        self.spec: dict[str, Any] = (
            json.loads((spec_dir / "step.json").read_text()) if spec_dir else {}
        )
        self.script = (spec_dir / "script.R").read_text() if spec_dir else ""

    def host(self, container_path: str) -> Path:
        for target, source in sorted(self.mounts.items(), key=lambda m: -len(m[0])):
            if container_path == target or container_path.startswith(target + "/"):
                return source / container_path[len(target) :].lstrip("/")
        raise KeyError(container_path)

    def input(self, name: str) -> Path:
        return self.host(self.spec["inputs"][name]["path"])

    def output(self, name: str) -> Path:
        return self.host(self.spec["outputs"][name])

    def result(self, status: str = "ok", checks: list | None = None, **extra: Any) -> None:
        body = {"status": status, "step": self.spec.get("step"), "counts": {}, "messages": []}
        body["checks"] = checks or []
        body.update(extra)
        (self.mounts["/run/result"] / "result.json").write_text(json.dumps(body))


def copy_input(run: FakeRun) -> int:
    """A stand-in R step: its one output is its first input."""
    first = next(iter(run.spec["inputs"]))
    run.output("final").write_bytes(run.input(first).read_bytes())
    run.result()
    return 0


class FakeSandbox:
    def __init__(self, handlers: Mapping[str, Handler] | None = None) -> None:
        self.handlers = dict(handlers or {})
        self.images = {"datalab-agent:dev": DIGEST, DIGEST: DIGEST}
        self.platform = "linux/arm64"
        self.steps: list[ContainerStep] = []
        self.removed: list[str] = []
        self.started = asyncio.Event()

    async def image(self, ref: str) -> ImageFacts | None:
        digest = self.images.get(ref)
        if digest is None:
            return None
        return ImageFacts(ref, digest, "linux/arm64", (), "R version 4.6.1", "f" * 64)

    async def host_platform(self) -> str:
        return self.platform

    async def run(self, step: ContainerStep) -> ContainerOutcome:
        self.steps.append(step)
        self.started.set()
        handler = self.handlers.get(step.name, copy_input)
        run = FakeRun(step)
        result = handler(run)
        if asyncio.iscoroutine(result):
            result = await result
        return ContainerOutcome(exit_code=int(result), log=b"log\n")

    async def remove_run(self, run_id: str) -> None:
        self.removed.append(run_id)


def settings_for(
    tmp_path: Path,
    profile: str = "practice",
    agent_image: str = "datalab-agent:dev",
    **workflows: Any,
) -> Settings:
    return Settings(
        profile=profile,  # type: ignore[arg-type]
        data_dir=tmp_path / "data",
        agent_image=agent_image,
        oracle=OracleSettings(
            host="127.0.0.1",
            port=1522,
            service="FREEPDB1",
            user="DATALAB_RO",
            keychain_service="test",
            read_only_roles=("IHS_2025_RO",),
            allowed_schemas=frozenset({"IHS_2024", "IHS_2025"}),
        ),
        limits=QueryLimits(max_concurrent_queries=1, min_free_disk_bytes=0),
        workflows=WorkflowSettings(folder=str(tmp_path / "workflows"), **workflows),
    )


class Harness:
    """A runner over a SQLite database, a fake sandbox, and a workflows folder."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        sandbox: Any = None,
        database: SqliteDatabase | None = None,
        profile: str = "practice",
        agent_image: str = "datalab-agent:dev",
        **workflows: Any,
    ) -> None:
        self.settings = settings_for(tmp_path, profile, agent_image, **workflows)
        self.folder = tmp_path / "workflows"
        self.folder.mkdir(exist_ok=True)
        self.connection = db.connect(self.settings.database_file)
        self.access_log = AccessLog(
            self.connection, self.settings.data_dir / "logs" / "audit.jsonl"
        )
        self.database = database or SqliteDatabase()
        self.data = DataService(
            self.database,
            self.access_log,
            self.settings.limits,
            frozenset({"IHS_2024", "IHS_2025"}),
            catalog(),
        )
        self.sandbox = sandbox(self.settings) if callable(sandbox) else sandbox or FakeSandbox()
        self.destinations = DestinationStore(self.connection)
        self.runner = self.make_runner()

    def make_runner(self) -> WorkflowRunner:
        return WorkflowRunner(
            settings=self.settings,
            store=RunStore(self.connection),
            data=self.data,
            access_log=self.access_log,
            sandbox=self.sandbox,
            folder=WorkflowFolder(self.folder),
            destinations=self.destinations,
            started_by="Test Person <test@example.org>",
        )

    def write(self, name: str, text: str) -> str:
        (self.folder / name).write_text(text)
        return name

    async def run(self, path: str, params: dict | None = None, **kwargs: Any) -> dict:
        run_id = await self.runner.start(path, params or {}, **kwargs)
        await self.runner.wait(run_id)
        detail = self.runner.detail(run_id)
        assert detail is not None
        return detail

    async def finish(self, run_id: str) -> dict:
        await self.runner.wait(run_id)
        detail = self.runner.detail(run_id)
        assert detail is not None
        return detail

    def run_dir(self, detail: dict) -> Path:
        return self.settings.data_dir / detail["run_dir"]

    def output(self, detail: dict, step_id: str, name: str = "final") -> Path:
        step = next(s for s in detail["steps"] if s["step_id"] == step_id)
        return self.run_dir(detail) / "steps" / step_id / "outputs" / step["outputs"][name]["file"]


def weekly_summary(run: FakeRun, *, suppress: bool = True, minimum: int = 11) -> int:
    """A Python stand-in for the R summary step: participants and mean steps per
    device and week, with a seeded bootstrap column, small cells suppressed."""
    rng = random.Random(run.spec["seed"])
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    with run.input("raw").open(newline="") as handle:
        for row in csv.DictReader(handle):
            day = int(row["RECORD_DATE"][8:10])
            week = f"W{(day - 1) // 7 + 1}"
            groups.setdefault((row["DEVICE"], week), []).append(
                (row["STUDY_PARTICIPANT_ID"], row["STEPS"])
            )
    with run.output("final").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["DEVICE", "week", "n_participants", "mean_steps", "boot"])
        for (device, week), items in sorted(groups.items()):
            people = len({p for p, _ in items})
            values = [int(s) for _, s in items if s]
            mean = sum(values) / len(values)
            boot = sum(rng.choice(values) for _ in range(50)) / 50
            if suppress and people < minimum:
                writer.writerow([device, week, "", "", ""])
            else:
                writer.writerow([device, week, people, f"{mean:.3f}", f"{boot:.3f}"])
    run.result(counts={"groups": len(groups)})
    return 0
