"""Question 6: does the draft 0008 apply on top of 0001-0005, hold a real
run record, and still work with DestinationStore?

    python check_migration.py runs/<run_id>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MIGRATIONS = REPO / "backend/src/datalab/db/migrations"
sys.path.insert(0, str(REPO / "backend" / "src"))
from datalab.exports import DestinationStore  # noqa: E402


def main() -> None:
    record = json.loads((Path(sys.argv[1]) / "record.json").read_text())
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("PRAGMA foreign_keys = ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        db.executescript(path.read_text())
    db.executescript((HERE / "0008_workflow_runs.sql").read_text())
    print("applied:", ", ".join(p.name for p in sorted(MIGRATIONS.glob("*.sql"))), "+ draft 0008")

    store = DestinationStore(db)
    try:
        store.add("Practice folder", HERE / "runs" / "destinations" / "practice")
        print("DestinationStore.add after 0008: works")
    except sqlite3.OperationalError as error:
        print(f"DestinationStore.add after 0008: FAILS ({error}); its INSERT must name columns")
        db.execute(
            "INSERT INTO export_destinations (id, name, path, added_at, key) VALUES (?, ?, ?, ?, ?)",
            ("dest_x", "Practice folder", "/tmp/practice", "2026-09-27", "practice-folder"),
        )

    image = record["image"]
    db.execute(
        "INSERT INTO workflow_runs (id, workflow_name, mode, of_run, status, started_at, finished_at,"
        " started_by, repo_commit, workflow_path, workflow_blob, workflow_text, image_ref,"
        " image_digest, image_platform, r_packages_sha256, runner_version, params_json, seed,"
        " reads_json, run_dir) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["run_id"], record["workflow"]["name"], record["mode"], None, record["status"],
            record["started_at"], record["finished_at"], "Spike Runner <spike@example.org>",
            record["workflow"]["repo_commit"], record["workflow"]["path"], "(git blob id)",
            (Path(sys.argv[1]) / "workflow.yaml").read_text(), image["ref"], image["id"],
            "linux/arm64", image["r_packages_sha256"], record["runner"]["wrapper_sha256"],
            json.dumps(record["params"]), record["seed"], json.dumps(record["reads"]),
            f"runs/{record['run_id']}",
        ),
    )  # fmt: skip
    for step in record["steps"]:
        db.execute(
            "INSERT INTO workflow_run_steps (run_id, step_id, position, kind, status, seed,"
            " query_id, sql_text, binds_json, exit_code, inputs_json, outputs_json, result_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["run_id"], step["id"], step["position"], step["type"], step["status"],
                step.get("seed"), step.get("query_id"), step.get("sql"),
                json.dumps(step.get("binds")) if "binds" in step else None, step.get("exit_code"),
                json.dumps(step.get("inputs", {})), json.dumps(step.get("outputs", {})),
                json.dumps(step.get("result")) if "result" in step else None,
            ),
        )  # fmt: skip
    delivery = record.get("delivery") or {}
    if "folder" in delivery:
        db.execute(
            "INSERT INTO workflow_run_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dlv_1", record["run_id"], "practice-folder", "dest_x", delivery["destination_path"],
             delivery["folder"], json.dumps(delivery["files"]), delivery["manifest_sha256"],
             record["finished_at"]),
        )  # fmt: skip
    counts = [
        db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("workflow_runs", "workflow_run_steps", "workflow_run_deliveries")
    ]
    print(f"stored run {record['run_id']}: runs={counts[0]} steps={counts[1]} deliveries={counts[2]}")


if __name__ == "__main__":
    main()
