"""Run the prototype's routines and its daily_metrics_2025 pipeline, headless.

This runs with the *prototype's* Python, in a `--no-local` clone of it (never
the prototype's own checkout), and imports its code: `RoutineStore` for the
8 default routines and `IhsDataRPackageExecutor` for the package pipeline,
each with its real R executor (the no-network `lab-ai-routine-r:local`
container). parity.py starts it; see README.md.

Two modes:

- `live`: SQL goes through the prototype's own Oracle broker (its FastAPI
  app and live executor, on 127.0.0.1:<broker_port>), pointed at the
  synthetic database with LAB_AI_ORACLE_* settings. This is the prototype
  end to end, extraction included.
- `extracts`: no database. The broker is replaced by a stand-in that hands
  back v1's extract for the step, byte for byte, so both sides' R code runs
  on exactly the same input.

Delivery uses the prototype's `DropboxDeliveryService` with a client that
copies into the output folder instead of uploading, so the prototype's own
path rules pick where each file goes.

Nothing here prints a row or a cell: only names, counts and statuses.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

CONFIG = json.loads(Path(sys.argv[1]).read_text())
CLONE = Path(CONFIG["clone"])
STATE = Path(CONFIG["state"])
OUT = Path(CONFIG["out"])
MODE = CONFIG["mode"]
PORT = int(CONFIG.get("broker_port", 8812))

STATE.mkdir(parents=True, exist_ok=True)
(STATE / "tmp").mkdir(exist_ok=True)
os.environ.update(
    {
        "TMPDIR": str(STATE / "tmp"),
        "LAB_AI_ORACLE_ENABLED": "1",
        "LAB_AI_ORACLE_HOST": "127.0.0.1",
        "LAB_AI_ORACLE_PORT": "1522",
        "LAB_AI_ORACLE_SERVICE": "FREEPDB1",
        "LAB_AI_ORACLE_USER": "DATALAB_RO",
        "LAB_AI_ORACLE_PASSWORD_SOURCE": "env",
        "LAB_AI_ORACLE_BROKER_ENABLED": "1",
        "LAB_AI_ORACLE_BROKER_EXECUTOR": "live",
        "LAB_AI_ORACLE_BROKER_LIVE_APPROVED": "1",
        "LAB_AI_ORACLE_BROKER_FULL_EXPORT_APPROVED": "1",
        "LAB_AI_ORACLE_BROKER_HOST": "127.0.0.1",
        "LAB_AI_ORACLE_BROKER_PORT": str(PORT),
        "LAB_AI_ORACLE_BROKER_ALLOWED_SCHEMAS": "IHS_2025",
        "LAB_AI_ORACLE_BROKER_CATALOG_EXPORT_DIR": str(CLONE / "config" / "oracle_catalog"),
        "LAB_AI_ORACLE_BROKER_RESULTS_DIR": str(STATE / "broker_results"),
        "LAB_AI_ORACLE_EXPORTS_DIR": str(STATE / "oracle_exports"),
        "LAB_AI_ROUTINES_DB": str(STATE / "routines.sqlite3"),
        "LAB_AI_ROUTINE_RUNS_DIR": str(STATE / "routine_runs"),
        "LAB_AI_DEFAULT_ROUTINES_BUNDLE": str(CLONE / "config" / "default_routines.json"),
        "LAB_AI_DROPBOX_DESTINATIONS_CONFIG": str(CLONE / "config" / "dropbox_destinations.json"),
        "LAB_AI_IHS_DATA_R_ENABLED": "1",
        "LAB_AI_IHS_DATA_R_HOST_PATH": str(CLONE / "r" / "ihsDataR"),
    }
)
if MODE != "live":
    # The stand-in broker never connects, so no password is needed or passed.
    os.environ.pop("LAB_AI_ORACLE_PASSWORD", None)

from lab_ai.agent_server.config import AgentServerSettings  # noqa: E402
from lab_ai.agent_server.dropbox_delivery import DropboxDeliveryService  # noqa: E402
from lab_ai.agent_server.ihs_data_r_execution import IhsDataRPackageExecutor  # noqa: E402
from lab_ai.agent_server.routines import RoutineError, RoutineStore  # noqa: E402

SETTINGS = AgentServerSettings.from_env()
RESULTS = Path(SETTINGS.oracle_broker_results_dir)
RESULTS.mkdir(parents=True, exist_ok=True)


class CopyingDropboxClient:
    """Stands in for the Dropbox API: `upload_file` copies into the output folder."""

    def __init__(self) -> None:
        self.target: Path | None = None

    def upload_file(self, dropbox_path: str, artifact_path: Path) -> dict[str, Any]:
        assert self.target is not None
        relative = dropbox_path.lstrip("/")
        destination = self.target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact_path, destination)
        return {"rev": "local"}

    def list_folder(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


def start_broker() -> None:
    import uvicorn
    from lab_ai.agent_server.oracle_broker_app import create_oracle_broker_app

    app = create_oracle_broker_app(SETTINGS)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.1)
    raise SystemExit(f"The prototype broker didn't start on port {PORT}.")


_FROM = re.compile(r"\bFROM\s+(?:%s|IHS_2025)\.([A-Z0-9_$#]+)", re.IGNORECASE)


def stand_in_broker(files: dict[str, str]):
    """A broker that answers each export with one of v1's extracts.

    `files` maps an Oracle object (or `*`, for a routine's one SQL step) to
    the CSV to hand back.
    """

    def request(method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if (method, path) != ("POST", "/exports"):
            raise RoutineError(f"The stand-in broker has no {method} {path}.")
        found = _FROM.search(str(payload.get("sql", "")))
        source = files.get("*") or (files.get(found.group(1).upper()) if found else None)
        if source is None:
            raise RoutineError("The stand-in broker has no extract for this query.")
        export_id = f"oracle_export_{uuid.uuid4().hex}"
        folder = RESULTS / export_id
        folder.mkdir(parents=True)
        shutil.copyfile(source, folder / "result.csv")
        with open(source, "rb") as handle:
            rows = max(sum(1 for _ in handle) - 1, 0)
        return {"export_id": export_id, "row_count": rows, "truncated": False}

    return request


def run_routines(store: RoutineStore, client: CopyingDropboxClient) -> None:
    by_name = {r["name"]: r for r in store.list()}
    for name in CONFIG.get("routines", []):
        target = OUT / name
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        status: dict[str, Any] = {"name": name, "side": "prototype", "mode": MODE}
        routine = by_name.get(name)
        if routine is None:
            status.update(status="missing", error="No such default routine.")
            (target / "status.json").write_text(json.dumps(status, indent=2))
            continue
        if MODE != "live":
            extract = CONFIG["extracts"].get(name)
            if extract is None:
                status.update(status="skipped", error="v1 has no extract for this routine.")
                (target / "status.json").write_text(json.dumps(status, indent=2))
                continue
            store._broker_request = stand_in_broker({"*": extract})
        client.target = target / "delivered"
        started = time.monotonic()
        try:
            run, _reused = store.run(routine["id"], {"reuse_existing": False})
            status["status"] = "succeeded"
        except RoutineError as error:
            run = store.get_run(error.routine_run_id) if error.routine_run_id else None
            status.update(status="failed", error=str(error)[:300])
        status["seconds"] = round(time.monotonic() - started, 1)
        if run is not None:
            collect_routine(run, routine, target, status)
        (target / "status.json").write_text(json.dumps(status, indent=2))
        print(f"prototype {name}: {status['status']}", flush=True)


def collect_routine(
    run: dict[str, Any], routine: dict[str, Any], target: Path, status: dict[str, Any]
) -> None:
    run_dir = Path(SETTINGS.routine_runs_dir) / run["id"]
    definition = routine["definition"]
    qc: list[dict[str, Any]] = []
    status["steps"] = []
    for step in run.get("steps", []):
        status["steps"].append({"id": step["id"], "type": step["type"], "state": step["state"]})
        spec = next((s for s in definition["steps"] if s["id"] == step["id"]), {})
        if step["type"] == "sql":
            folder = target / "extract"
        elif step["type"] in {"r", "ihs_data_r"}:
            folder = target / "outputs"
        elif step["type"] == "qc":
            report = run_dir / spec.get("output_filename", "")
            if report.is_file():
                data = json.loads(report.read_text())
                qc.append(
                    {"step": step["id"], "passed": data["passed"], "row_count": data["row_count"]}
                )
            continue
        else:
            continue
        folder.mkdir(exist_ok=True)
        for name in step.get("outputs", []):
            source = run_dir / name
            if source.is_file():
                shutil.copyfile(source, folder / name)
    (target / "qc.json").write_text(json.dumps(qc, indent=2))


def run_pipelines() -> None:
    for name in CONFIG.get("pipelines", []):
        target = OUT / name
        shutil.rmtree(target, ignore_errors=True)
        (target / "extract").mkdir(parents=True)
        (target / "outputs").mkdir()
        status: dict[str, Any] = {"name": name, "side": "prototype", "mode": MODE}
        broker = None
        if MODE != "live":
            files = CONFIG.get("pipeline_extracts", {}).get(name)
            if not files:
                status.update(status="skipped", error="v1 has no extracts for this pipeline.")
                (target / "status.json").write_text(json.dumps(status, indent=2))
                continue
            broker = stand_in_broker(files)
        executor = IhsDataRPackageExecutor(SETTINGS, broker_request=broker)
        started = time.monotonic()
        try:
            result = executor.execute(name)
            status["status"] = "succeeded"
            out_dir = executor.runs_dir / result["execution_id"]
            for item in result["files"]:
                shutil.copyfile(out_dir / item["name"], target / "outputs" / item["name"])
            for source in result["sources"]:
                csv_path = RESULTS / source["export_id"] / "result.csv"
                shutil.copyfile(csv_path, target / "extract" / f"IHS_2025.{source['object']}.csv")
        except Exception as error:
            status.update(status="failed", error=f"{type(error).__name__}: {str(error)[:300]}")
            traceback.print_exc()
        status["seconds"] = round(time.monotonic() - started, 1)
        (target / "status.json").write_text(json.dumps(status, indent=2))
        print(f"prototype {name}: {status['status']}", flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if MODE == "live":
        start_broker()
    client = CopyingDropboxClient()
    dropbox = DropboxDeliveryService(SETTINGS, client=client)  # type: ignore[arg-type]
    store = RoutineStore(SETTINGS, dropbox_service=dropbox)
    run_routines(store, client)
    run_pipelines()
