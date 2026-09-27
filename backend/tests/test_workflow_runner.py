"""The workflow runner, with a SQLite stand-in for Oracle and a fake sandbox.

The real container steps are in test_workflow_docker.py (`-m docker`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest

from datalab import exports
from datalab.workflows import runner as runner_module
from datalab.workflows.model import WorkflowInvalid
from datalab.workflows.runner import ReplayNotExact, RunRefused
from datalab.workflows.source import git_blob_id
from tests.workflow_fakes import FakeRun, FakeSandbox, Harness, copy_input, weekly_summary


def copy_input_and_result(run: FakeRun) -> int:
    return copy_input(run)


WEEKLY = """\
name: weekly_steps
description: Weekly steps by device, small cells suppressed.
reads:
  - IHS_2025.WEARABLE_DAILY
parameters:
  start_date: { type: date, default: 2025-04-01 }
  end_date:   { type: date, default: 2025-05-01 }
  min_cell:   { type: integer, default: 11 }
steps:
  - id: extract
    sql: |
      SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS
      FROM IHS_2025.WEARABLE_DAILY
      WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND RECORD_DATE <  TO_DATE(:end_date, 'YYYY-MM-DD')
      ORDER BY STUDY_PARTICIPANT_ID, RECORD_DATE
    output: daily.csv
  - id: check_raw
    qc:
      file: extract
      min_rows: 1
      required_columns: [STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS]
      unique_by: [STUDY_PARTICIPANT_ID, RECORD_DATE]
      max_missing: { STEPS: 0.2 }
  - id: summary
    r: |
      x <- read.csv(inputs$raw)
      write.csv(x, outputs$final, row.names = FALSE)
    inputs: { raw: extract }
    output: weekly.csv
  - id: check_summary
    qc:
      file: summary
      small_cells: { count_column: n_participants, min: $min_cell }
deliver:
  destination: practice-folder
  folder: weekly_steps
  files: [summary]
"""


def harness(tmp_path: Path, *, suppress: bool = True, **kwargs) -> Harness:
    sandbox = FakeSandbox({"summary": lambda run: weekly_summary(run, suppress=suppress)})
    h = Harness(tmp_path, sandbox=sandbox, **kwargs)
    h.write("weekly_steps.yaml", WEEKLY)
    return h


async def test_a_run_extracts_summarises_checks_and_delivers(tmp_path):
    h = harness(tmp_path)
    run = await h.run("weekly_steps.yaml", seed=42)
    assert run["status"] == "succeeded", run
    assert [s["status"] for s in run["steps"]] == ["succeeded"] * 4
    extract, _, summary, check = run["steps"]

    # The SQL step went through the data service, owned by the run.
    [query] = h.access_log.for_origin("run", run["id"])
    assert query.id == extract["query_id"] and query.status == "succeeded"
    assert extract["binds"] == {"start_date": "2025-04-01", "end_date": "2025-05-01"}
    assert extract["outputs"]["final"]["rows"] == query.row_count
    # The extract is kept in the run folder: it's what a Replay reruns on.
    assert h.output(run, "extract").read_text().startswith("STUDY_PARTICIPANT_ID,")

    # The R step's contract.
    [container] = h.sandbox.steps
    spec = json.loads((h.run_dir(run) / "steps/summary/spec/step.json").read_text())
    assert spec["inputs"]["raw"]["path"] == "/run/in/raw/daily.csv"
    assert spec["inputs"]["raw"]["sha256"] == extract["outputs"]["final"]["sha256"]
    assert spec["outputs"] == {"final": "/run/out/weekly.csv"}
    assert spec["seed"] == summary["seed"] and spec["params"]["min_cell"] == 11
    assert container.image == "sha256:" + "ab" * 32
    readonly = {b.target: b.readonly for b in container.binds}
    assert readonly == {
        "/run/datalab": True,
        "/run/step": True,
        "/run/in/raw": True,
        "/run/out": False,
        "/run/result": False,
    }
    assert summary["inputs"]["raw"]["sha256"] == extract["outputs"]["final"]["sha256"]
    assert check["result"]["status"] == "ok"

    # The record: definition, environment, randomness.
    text = (h.folder / "weekly_steps.yaml").read_bytes()
    assert run["workflow_source"] == "file" and run["repo_commit"] is None
    assert run["workflow_blob"].startswith("sha256:") and run["workflow_text"] == text.decode()
    assert run["image_digest"] == "sha256:" + "ab" * 32 and run["image_platform"] == "linux/arm64"
    assert run["seed"] == 42 and run["runtime"]["env"]["TZ"] == "Etc/UTC"
    assert run["runtime"]["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert run["runtime"]["env"]["R_DATATABLE_NUM_THREADS"] == "1"
    assert run["runtime"]["rng_kind"] == ["Mersenne-Twister", "Inversion", "Rejection"]
    assert run["reads"] == ["IHS_2025.WEARABLE_DAILY"]
    assert run["started_by"] == "Test Person <test@example.org>"
    record = json.loads((h.run_dir(run) / "record.json").read_text())
    assert record["id"] == run["id"] and len(record["steps"]) == 4

    # Delivered to the practice folder, with a manifest; audited without contents.
    assert run["delivery_status"] == "delivered"
    [delivery] = run["deliveries"]
    folder = Path(delivery["folder"])
    assert folder.parent == h.settings.data_dir / "practice-exports" / "weekly_steps"
    manifest = json.loads((folder / "datalab-export.json").read_text())
    assert manifest["run"]["id"] == run["id"] and manifest["contains_study_data"] is False
    assert [f["path"] for f in manifest["files"]] == ["files/weekly.csv"]
    assert (folder / "files/weekly.csv").read_bytes() == h.output(run, "summary").read_bytes()
    audit = (h.settings.data_dir / "logs/audit.jsonl").read_text().splitlines()
    export = json.loads(audit[-1])
    assert export["event"] == "export" and export["session_id"] == run["id"]


async def test_a_small_cell_stops_the_run_before_delivery(tmp_path):
    h = harness(tmp_path, suppress=False)
    run = await h.run("weekly_steps.yaml")
    assert run["status"] == "failed"
    statuses = {s["step_id"]: s["status"] for s in run["steps"]}
    assert statuses["check_summary"] == "failed"
    check = next(s for s in run["steps"] if s["step_id"] == "check_summary")
    [small] = [c for c in check["result"]["checks"] if c["id"] == "small_cells"]
    assert small["status"] == "fail" and small["observed"] > 0
    assert "SYN-" not in json.dumps(check["result"])  # counts only
    assert run["delivery_status"] == "skipped" and run["deliveries"] == []
    assert not (h.settings.data_dir / "practice-exports").exists()


async def test_a_failed_step_skips_the_rest(tmp_path):
    h = harness(tmp_path)
    h.write("weekly_steps.yaml", WEEKLY.replace("min_rows: 1", "min_rows: 100000"))
    run = await h.run("weekly_steps.yaml")
    assert [s["status"] for s in run["steps"]] == ["succeeded", "failed", "skipped", "skipped"]
    assert "min_rows" in run["steps"][1]["message"]
    assert h.sandbox.steps == []  # the R step never started


async def test_replay_is_byte_identical_with_the_same_pins(tmp_path):
    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml", seed=7)
    queries = len(h.database.calls)
    check = await h.runner.replay_check(first["id"])
    assert check.exact and not check.reasons and not check.blocking
    replay = await h.finish(await h.runner.replay(first["id"]))
    assert replay["status"] == "succeeded" and replay["mode"] == "replay"
    assert replay["of_run"] == first["id"] and replay["reproduced"] == 1
    assert replay["replay_exact"] == 1 and replay["replay_notes"] == []
    assert len(h.database.calls) == queries  # no new extract
    for step in ("extract", "summary"):
        assert h.output(replay, step).read_bytes() == h.output(first, step).read_bytes()
    assert h.run_dir(replay) != h.run_dir(first)  # a new folder, never the old one
    # Replays don't deliver unless asked.
    assert replay["delivery_status"] == "skipped" and replay["deliveries"] == []


async def test_replay_says_when_it_cant_be_exact(tmp_path):
    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml")
    h.sandbox.images = {"datalab-agent:dev": "sha256:" + "cd" * 32, "sha256:" + "cd" * 32: "x"}
    h.sandbox.platform = "linux/amd64"
    with pytest.raises(ReplayNotExact) as refused:
        await h.runner.replay(first["id"])
    reasons = " ".join(refused.value.reasons)
    assert "isn't on this computer" in reasons and "linux/amd64" in reasons
    replay = await h.finish(await h.runner.replay(first["id"], allow_inexact=True))
    assert replay["replay_exact"] == 0 and len(replay["replay_notes"]) >= 2
    assert replay["image_digest"] == "sha256:" + "cd" * 32


async def test_replay_refuses_when_the_kept_extract_changed(tmp_path):
    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml")
    h.output(first, "extract").write_text("tampered\n")
    check = await h.runner.replay_check(first["id"])
    assert check.blocking
    with pytest.raises(RunRefused):
        await h.runner.replay(first["id"])


async def test_run_again_uses_todays_file_and_data(tmp_path):
    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml", {"min_cell": 12}, seed=3)
    h.write("weekly_steps.yaml", WEEKLY.replace("Weekly steps by device", "Weekly steps"))
    again = await h.finish(await h.runner.run_again(first["id"]))
    assert again["mode"] == "run_again" and again["of_run"] == first["id"]
    assert "Weekly steps, small" in again["workflow_text"]
    assert again["params"]["min_cell"] == 12 and again["seed"] == 3
    assert len(h.database.calls) == 2  # extracted again
    assert again["delivery_status"] == "delivered"


async def test_stop_cancels_the_run_and_removes_its_containers(tmp_path):
    release = asyncio.Event()

    async def waits(run: FakeRun) -> int:
        await release.wait()
        return 0

    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": waits}))
    h.write("weekly_steps.yaml", WEEKLY)
    run_id = await h.runner.start("weekly_steps.yaml", {})
    await asyncio.wait_for(h.sandbox.started.wait(), 5)
    assert await h.runner.stop(run_id)
    run = h.runner.detail(run_id)
    assert run is not None and run["status"] == "cancelled"
    assert [s["status"] for s in run["steps"]] == ["succeeded", "succeeded", "cancelled", "skipped"]
    assert run_id in h.sandbox.removed and run["delivery_status"] == "skipped"


async def test_a_run_datalab_left_going_is_interrupted(tmp_path):
    release = asyncio.Event()

    async def waits(run: FakeRun) -> int:
        await release.wait()
        return 0

    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": waits}))
    h.write("weekly_steps.yaml", WEEKLY)
    run_id = await h.runner.start("weekly_steps.yaml", {})
    await asyncio.wait_for(h.sandbox.started.wait(), 5)
    h.make_runner()  # DataLab starting again on the same data folder
    run = h.runner.detail(run_id)
    assert run is not None and run["status"] == "interrupted"
    assert {s["status"] for s in run["steps"][2:]} <= {"cancelled", "skipped"}
    release.set()
    await h.runner.stop(run_id)


async def test_the_run_reads_only_what_it_declares(tmp_path):
    """The declared reads go to the data service as the only tables allowed."""
    h = harness(tmp_path)
    run = await h.run("weekly_steps.yaml")
    [query] = h.access_log.for_origin("run", run["id"])
    assert query.tables == ["IHS_2025.WEARABLE_DAILY"]


async def test_an_invalid_workflow_doesnt_start(tmp_path):
    h = harness(tmp_path)
    h.write(
        "bad.yaml",
        WEEKLY.replace(
            "      ORDER BY", "      AND 1 = (SELECT 1 FROM IHS_2025.PARTICIPANTS)\n      ORDER BY"
        ),
    )
    with pytest.raises(WorkflowInvalid) as refused:
        await h.runner.start("bad.yaml")
    assert any("IHS_2025.PARTICIPANTS" in p.message for p in refused.value.problems)
    with pytest.raises(WorkflowInvalid):
        await h.runner.start("weekly_steps.yaml", {"min_cell": "many"})
    assert h.runner.store.list_runs() == []


async def test_a_committed_file_records_its_git_blob_and_commit(tmp_path):
    h = harness(tmp_path)
    git = ["git", "-C", str(h.folder), "-c", "user.name=T", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "workflows"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    run = await h.run("weekly_steps.yaml")
    assert run["workflow_source"] == "git" and run["repo_commit"] == head.strip()
    assert run["workflow_blob"] == git_blob_id((h.folder / "weekly_steps.yaml").read_bytes())
    # An edit not yet committed is recorded by its checksum instead.
    h.write("weekly_steps.yaml", WEEKLY + "\n")
    edited = await h.run("weekly_steps.yaml")
    assert edited["workflow_source"] == "file" and edited["repo_commit"] is None


async def test_only_declared_regular_files_come_back(tmp_path):
    def sneaky(run: FakeRun) -> int:
        run.output("final").symlink_to("/etc/passwd")
        (run.host("/run/out") / "undeclared.txt").write_text("extra")
        run.result()
        return 0

    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": sneaky}))
    h.write("weekly_steps.yaml", WEEKLY)
    run = await h.run("weekly_steps.yaml")
    summary = run["steps"][2]
    assert summary["status"] == "failed" and "weekly.csv" in summary["message"]
    assert summary["outputs"] == {}
    assert not (h.run_dir(run) / "steps/summary/scratch").exists()
    assert run["delivery_status"] == "skipped"


async def test_a_step_that_writes_past_the_run_cap_fails(tmp_path):
    def big(run: FakeRun) -> int:
        run.output("final").write_bytes(b"x" * 3 * 1024**2)
        run.result()
        return 0

    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": big}), max_run_bytes=2 * 1024**2)
    h.write("weekly_steps.yaml", WEEKLY)
    run = await h.run("weekly_steps.yaml")
    assert run["steps"][2]["status"] == "failed"


async def test_real_profile_delivers_to_the_folder_its_key_names(tmp_path, monkeypatch):
    # The temporary folder is under /private/var, which is never a destination.
    monkeypatch.setattr("datalab.sessions.inputs._SYSTEM_FOLDERS_POSIX", ())
    monkeypatch.setattr("datalab.sessions.inputs._CONTAINERS_POSIX", ())
    h = harness(tmp_path, profile="real")
    first = await h.run("weekly_steps.yaml")
    assert first["delivery_status"] == "failed"
    assert "practice-folder" in first["delivery_message"]
    target = tmp_path / "Dropbox" / "IHS"
    target.mkdir(parents=True)
    destination = h.destinations.add("Dropbox IHS", target)
    assert h.destinations.set_key(destination.id, "practice-folder")
    run = await h.run("weekly_steps.yaml")
    assert run["delivery_status"] == "delivered"
    [delivery] = run["deliveries"]
    assert Path(delivery["folder"]).parent == target / "weekly_steps"
    assert delivery["destination_id"] == destination.id
    manifest = json.loads((Path(delivery["folder"]) / "datalab-export.json").read_text())
    assert manifest["contains_study_data"] is True


async def test_container_steps_need_the_image(tmp_path):
    h = harness(tmp_path)
    h.sandbox.images = {}
    with pytest.raises(RunRefused):
        await h.runner.start("weekly_steps.yaml")


async def test_stop_is_refused_once_delivery_starts_and_a_cancel_waits_for_it(
    tmp_path, monkeypatch
):
    """export() runs in a thread a cancel can't stop, so what it writes must
    always be recorded: its delivery row and the audit log's export entry."""
    h = harness(tmp_path)
    go, entered = threading.Event(), threading.Event()
    real_export = exports.export

    def slow_export(*args, **kwargs):
        entered.set()
        go.wait(10)
        return real_export(*args, **kwargs)

    monkeypatch.setattr(runner_module.exports, "export", slow_export)
    run_id = await h.runner.start("weekly_steps.yaml")
    await asyncio.to_thread(entered.wait, 10)
    with pytest.raises(RunRefused, match="Delivery has started"):
        await h.runner.stop(run_id)
    asyncio.get_running_loop().call_later(0.2, go.set)
    await h.runner.close()  # DataLab shutting down: cancels the run's task
    run = h.runner.detail(run_id)
    assert run is not None and run["delivery_status"] == "delivered"
    assert run["status"] == "succeeded" and "asked to stop" in run["delivery_message"]
    [delivery] = run["deliveries"]
    assert (Path(delivery["folder"]) / "datalab-export.json").is_file()
    audit = (h.settings.data_dir / "logs/audit.jsonl").read_text().splitlines()
    assert json.loads(audit[-1])["session_id"] == run_id


async def test_the_manifest_keeps_only_numbers_from_checks(tmp_path):
    def custom(run: FakeRun) -> int:
        run.result(checks=[{"id": "who", "status": "pass", "observed": "SYN-0001", "message": ""}])
        return 0

    text = WEEKLY.replace(
        "deliver:",
        "  - id: custom\n    qc:\n      r: x\n      inputs: { s: summary }\ndeliver:",
    )
    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": weekly_summary, "custom": custom}))
    h.write("weekly_steps.yaml", text)
    run = await h.run("weekly_steps.yaml")
    assert run["status"] == "succeeded", [s["message"] for s in run["steps"]]
    manifest = (Path(run["deliveries"][0]["folder"]) / "datalab-export.json").read_text()
    assert "SYN-0001" not in manifest
    qc = {q["step"]: q for q in json.loads(manifest)["qc"]}
    assert qc["custom"]["checks"] == [{"id": "who", "status": "pass", "observed": None}]
    assert isinstance(qc["check_raw"]["checks"][0]["observed"], int)  # row counts stay


async def test_the_result_folder_counts_against_the_cap(tmp_path):
    seen = []

    def fills_result(run: FakeRun) -> int:
        seen.append((run.step.watch, run.step.max_bytes))
        return copy_input_and_result(run)

    h = Harness(tmp_path, sandbox=FakeSandbox({"summary": fills_result}))
    h.write("weekly_steps.yaml", WEEKLY)
    run = await h.run("weekly_steps.yaml")
    [(watched, cap)] = seen
    assert watched == h.run_dir(run) / "steps" / "summary"  # /run/out and /run/result both
    assert 0 < cap <= h.settings.workflows.max_run_bytes


async def test_replay_checks_the_kept_text_against_its_blob(tmp_path):
    h = harness(tmp_path)
    first = await h.run("weekly_steps.yaml")
    h.connection.execute(
        "UPDATE workflow_runs SET workflow_text = workflow_text || '# edited' WHERE id = ?",
        (first["id"],),
    )
    check = await h.runner.replay_check(first["id"])
    assert any("blob" in reason for reason in check.blocking)
