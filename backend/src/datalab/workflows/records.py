"""Run records: workflow runs, their steps, and deliveries (migration 0008).

The run folder has a copy of all of it (`record.json`), so a folder explains
itself even without the database. Records are written as each step starts
and finishes, so a crash leaves an `interrupted` run, not a lost one.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

_RUN_COLUMNS = frozenset(
    """
    id workflow_name mode of_run set_id status started_at finished_at started_by
    workflow_path workflow_source repo_commit workflow_blob workflow_text pipelines_json
    image_ref image_digest image_platform host_platform r_packages_sha256 runner_version
    runtime_json params_json seed reads_json run_dir inputs_kept replay_exact
    replay_notes_json reproduced delivery_status delivery_message message
    """.split()  # noqa: SIM905
)
_STEP_COLUMNS = frozenset(
    """
    run_id step_id position kind status started_at finished_at seed query_id sql_text
    binds_json queries_json exit_code elapsed_ms inputs_json outputs_json result_json message
    """.split()  # noqa: SIM905
)
_DELIVERY_COLUMNS = frozenset(
    """
    id run_id destination_key destination_id destination_path folder files_json
    manifest_sha256 delivered_at destination_name sync_provider
    """.split()  # noqa: SIM905
)
# What a delivery a previous DataLab left under way says: it can't be known.
UNKNOWN_DELIVERY = (
    "Unknown: DataLab stopped during this delivery, so some or all of its files may be in "
    "the destination. Check the destination folder."
)
FINISHED = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
# Held for every write to the run tables, by every RunStore, and by Storage
# (storage.py) while it decides whether a run's files can go: DataLab's
# stores share one SQLite connection, so a transaction alone can't keep the
# two apart.
WRITE_LOCK = threading.RLock()


class InputsGone(RuntimeError):
    """A Replay's original run no longer has its kept inputs: nothing was recorded."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = WRITE_LOCK

    # ------------------------------------------------------------ writes

    def create_run(
        self,
        fields: dict[str, Any],
        steps: list[tuple[str, int, str]],
        *,
        needs_inputs_of: str | None = None,
    ) -> None:
        """A new run, and each of its steps as pending: (step id, position, kind).

        `needs_inputs_of`: a Replay's original run. The row goes in only if
        that run still has its kept inputs, checked in the insert itself, so
        Storage can't remove them between a Replay's check and its start.
        Raises InputsGone otherwise.
        """
        row = _encode(fields, _RUN_COLUMNS)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if needs_inputs_of is None:
                    self._insert("workflow_runs", row)
                elif not self._insert_if_inputs_kept(row, needs_inputs_of):
                    raise InputsGone(needs_inputs_of)
                for step_id, position, kind in steps:
                    self._insert(
                        "workflow_run_steps",
                        {
                            "run_id": fields["id"],
                            "step_id": step_id,
                            "position": position,
                            "kind": kind,
                            "status": "pending",
                        },
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def update_run(self, run_id: str, **fields: Any) -> None:
        row = _encode(fields, _RUN_COLUMNS)
        with self._lock:
            self._update("workflow_runs", row, "id = ?", (run_id,))

    def update_step(self, run_id: str, step_id: str, **fields: Any) -> None:
        row = _encode(fields, _STEP_COLUMNS)
        with self._lock:
            self._update("workflow_run_steps", row, "run_id = ? AND step_id = ?", (run_id, step_id))

    def add_delivery(self, fields: dict[str, Any]) -> None:
        with self._lock:
            self._insert("workflow_run_deliveries", _encode(fields, _DELIVERY_COLUMNS))

    def mark_interrupted(self) -> list[str]:
        """Runs a previous DataLab left going (it stopped or crashed).

        A run still queued or running is `interrupted`, and nothing was
        delivered. A run whose steps had all finished but which never got its
        `finished_at` stopped during, or just after, its delivery: its status
        stays, and a delivery still pending is marked as failed with an
        unknown outcome, since files may already be in the destination.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT id, status FROM workflow_runs "
                "WHERE status IN ('queued', 'running') OR finished_at IS NULL ORDER BY id"
            ).fetchall()
            for run_id, status in rows:
                if status in ("queued", "running"):
                    self._db.execute(
                        "UPDATE workflow_runs SET status = 'interrupted', finished_at = ?, "
                        "message = 'DataLab stopped while this run was going.', "
                        "delivery_status = CASE delivery_status WHEN 'pending' THEN 'skipped' "
                        "ELSE delivery_status END WHERE id = ?",
                        (now(), run_id),
                    )
                else:
                    self._db.execute(
                        "UPDATE workflow_runs SET finished_at = ?, "
                        "delivery_message = CASE delivery_status WHEN 'pending' THEN ? "
                        "ELSE delivery_message END, "
                        "delivery_status = CASE delivery_status WHEN 'pending' THEN 'failed' "
                        "ELSE delivery_status END WHERE id = ?",
                        (now(), UNKNOWN_DELIVERY, run_id),
                    )
                self._db.execute(
                    "UPDATE workflow_run_steps SET status = CASE status "
                    "WHEN 'running' THEN 'cancelled' ELSE 'skipped' END "
                    "WHERE run_id = ? AND status IN ('pending', 'running')",
                    (run_id,),
                )
        return [row[0] for row in rows]

    # ------------------------------------------------------------- reads

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
        return _decode(row) if row else None

    def list_runs(self, workflow_path: str | None = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM workflow_runs"
        args: tuple[Any, ...] = ()
        if workflow_path is not None:
            query += " WHERE workflow_path = ?"
            args = (workflow_path,)
        query += " ORDER BY started_at DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(query, (*args, limit)).fetchall()
        return [_decode(r) for r in rows]

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM workflow_run_steps WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        return [_decode(r) for r in rows]

    def deliveries(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM workflow_run_deliveries WHERE run_id = ? ORDER BY delivered_at",
                (run_id,),
            ).fetchall()
        return [_decode(r) for r in rows]

    # ----------------------------------------------------------- helpers

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self._db.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))

    def _insert_if_inputs_kept(self, row: dict[str, Any], original: str) -> bool:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        inserted = self._db.execute(
            f"INSERT INTO workflow_runs ({columns}) SELECT {marks} WHERE EXISTS "
            "(SELECT 1 FROM workflow_runs WHERE id = ? AND inputs_kept = 1)",
            (*row.values(), original),
        )
        return inserted.rowcount == 1

    def _update(self, table: str, row: dict[str, Any], where: str, args: tuple) -> None:
        if not row:
            return
        sets = ", ".join(f"{column} = ?" for column in row)
        self._db.execute(f"UPDATE {table} SET {sets} WHERE {where}", (*row.values(), *args))


def _encode(fields: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """Field values as columns: `x` -> `x_json` for dicts and lists. Only known columns."""
    row: dict[str, Any] = {}
    for key, value in fields.items():
        column = key if key in allowed else f"{key}_json"
        if column not in allowed:
            raise KeyError(f"No run-record column {key!r}")
        if column.endswith("_json"):
            value = None if value is None else json.dumps(value, sort_keys=True, default=str)
        elif isinstance(value, bool):
            value = int(value)
        row[column] = value
    return row


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():  # noqa: SIM118 (sqlite3.Row isn't iterable by key)
        value = row[key]
        if key.endswith("_json"):
            out[key[: -len("_json")]] = json.loads(value) if value is not None else None
        else:
            out[key] = value
    return out
