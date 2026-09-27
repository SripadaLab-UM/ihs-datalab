"""Workflow runs with real step containers: SQL -> R -> QC, pipelines, Stop.

Needs Docker and the agent image (`DATALAB_TEST_AGENT_IMAGE`, default
`datalab-agent:dev`). Run with `uv run pytest -m docker`. The SQL steps run
through the real data service over a SQLite stand-in with synthetic rows
(SYN-####), so no database is needed.

Every container is labelled with this test's own DataLab instance (and
`datalab.spike=m6test`), and only those are removed afterwards.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from datalab.sessions.containers import instance_of
from datalab.workflows.sandbox import DockerSandbox, StepLimits
from tests.workflow_fakes import Harness

pytestmark = pytest.mark.docker

IMAGE = os.environ.get("DATALAB_TEST_AGENT_IMAGE", "datalab-agent:dev")
TEST_LABEL = {"datalab.spike": "m6test"}

CHAIN = """\
name: weekly_steps
description: Weekly steps by device for the synthetic cohort, small cells suppressed.
reads: [IHS_2025.WEARABLE_DAILY]
parameters:
  start_date: { type: date, default: 2025-04-01 }
  end_date:   { type: date, default: 2025-05-01 }
  min_cell:   { type: integer, default: 11 }
  suppress:   { type: boolean, default: true }
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
      unique_by: [STUDY_PARTICIPANT_ID, RECORD_DATE]
  - id: summary
    r: |
      x <- read.csv(inputs$raw, check.names = FALSE)
      x$week <- paste0("W", (as.integer(substr(x$RECORD_DATE, 9, 10)) - 1) %/% 7 + 1)
      keys <- unique(x[, c("DEVICE", "week")])
      keys <- keys[order(keys$DEVICE, keys$week), ]
      rows <- lapply(seq_len(nrow(keys)), function(i) {
        g <- x[x$DEVICE == keys$DEVICE[i] & x$week == keys$week[i], ]
        v <- g$STEPS[!is.na(g$STEPS)]
        data.frame(DEVICE = keys$DEVICE[i], week = keys$week[i],
                   n_participants = length(unique(g$STUDY_PARTICIPANT_ID)),
                   mean_steps = mean(v),
                   boot_mean = mean(replicate(200, mean(sample(v, replace = TRUE)))))
      })
      out <- do.call(rbind, rows)
      small <- out$n_participants < params$min_cell
      if (isTRUE(params$suppress)) out[small, c("n_participants", "mean_steps", "boot_mean")] <- NA
      write.csv(out, outputs$final, row.names = FALSE, na = "")
      datalab_count("groups", nrow(out))
    inputs: { raw: extract }
    output: weekly.csv
  - id: check_summary
    qc:
      file: summary
      unique_by: [DEVICE, week]
      small_cells: { count_column: n_participants, min: $min_cell }
  - id: plausible
    qc:
      r: |
        s <- read.csv(inputs$summary)
        bad <- sum(!is.na(s$mean_steps) & (s$mean_steps < 0 | s$mean_steps > 50000))
        datalab_check("plausible_mean_steps", bad == 0, observed = bad, expected = 0,
                      message = sprintf("%d rows with implausible mean steps", bad))
      inputs: { summary: summary }
deliver:
  destination: practice-folder
  folder: weekly_steps
  files: [summary]
"""

MISBEHAVING = """\
name: misbehaving
reads: [IHS_2025.WEARABLE_DAILY]
steps:
  - id: extract
    sql: SELECT STUDY_PARTICIPANT_ID, STEPS FROM IHS_2025.WEARABLE_DAILY
    output: raw.csv
  - id: sneaky
    r: |
      file.symlink("/etc/passwd", outputs$final)
      writeLines("extra", "/run/out/undeclared.txt")
      net <- tryCatch({ readLines(url("http://example.org"), n = 1); "reached" },
                      error = function(e) "blocked", warning = function(w) "blocked")
      wrote <- tryCatch({ writeLines("x", inputs$raw); "wrote" },
                        error = function(e) "refused", warning = function(w) "refused")
      datalab_message(paste("network:", net, "- input write:", wrote))
    inputs: { raw: extract }
    output: looks_innocent.csv
"""

SLEEPS = """\
name: sleeps
reads: [IHS_2025.WEARABLE_DAILY]
steps:
  - id: extract
    sql: SELECT STUDY_PARTICIPANT_ID, STEPS FROM IHS_2025.WEARABLE_DAILY
    output: raw.csv
  - id: wait
    r: Sys.sleep(120)
    inputs: { raw: extract }
"""

PIPELINE_YAML = """\
name: weekly_devices
reads:
  - object: IHS_2025.WEARABLE_DAILY
    columns: [STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS]
    where: RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
parameters: [start_date]
outputs: { devices: devices.csv }
"""

USES_PIPELINE = """\
name: devices
reads: [IHS_2025.WEARABLE_DAILY]
parameters:
  start_date: { type: date, default: 2025-04-15 }
steps:
  - id: metrics
    pipeline: weekly_devices
  - id: check
    qc: { file: metrics, min_rows: 3, required_columns: [DEVICE, days] }
"""


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


@pytest.fixture
def harness(tmp_path):
    if _docker("image", "inspect", IMAGE).returncode != 0:
        pytest.skip(f"Needs the agent image {IMAGE}")

    def sandbox(settings):
        return DockerSandbox(
            profile="practice",
            instance=instance_of(settings.data_dir),
            limits=StepLimits(timeout_seconds=300, memory="2g", cpus="1"),
            cache_dir=settings.data_dir / "workflow-cache",
            extra_labels=TEST_LABEL,
        )

    h = Harness(tmp_path, sandbox=sandbox, agent_image=IMAGE)
    instance = instance_of(h.settings.data_dir)
    try:
        yield h
    finally:
        # Only this test's containers: its own instance, and the test label.
        listed = _docker(
            "ps", "-aq",
            "--filter", f"label=datalab.instance={instance}",
            "--filter", "label=datalab.spike=m6test",
        ).stdout.split()  # fmt: skip
        if listed:
            _docker("rm", "-f", *listed)


def containers_of(h: Harness) -> list[str]:
    instance = instance_of(h.settings.data_dir)
    return _docker(
        "ps", "-aq", "--filter", f"label=datalab.instance={instance}"
    ).stdout.split()  # fmt: skip


async def test_sql_r_qc_chain_delivers_and_replays_byte_identical(harness):
    h = harness
    h.write("weekly_steps.yaml", CHAIN)
    run = await h.run("weekly_steps.yaml", seed=2026)
    assert run["status"] == "succeeded", [(s["step_id"], s["message"]) for s in run["steps"]]
    summary = next(s for s in run["steps"] if s["step_id"] == "summary")
    assert summary["exit_code"] == 0 and summary["result"]["counts"] == {"groups": 12}
    assert summary["result"]["rng_kind"] == ["Mersenne-Twister", "Inversion", "Rejection"]
    assert run["image_digest"].startswith("sha256:") and run["r_packages_sha256"]
    assert run["delivery_status"] == "delivered"
    weekly = h.output(run, "summary").read_text()
    assert weekly.splitlines()[0] == '"DEVICE","week","n_participants","mean_steps","boot_mean"'
    assert containers_of(h) == []  # --rm, and nothing left behind

    replay = await h.finish(await h.runner.replay(run["id"]))
    assert replay["status"] == "succeeded" and replay["replay_exact"] == 1
    assert replay["reproduced"] == 1, replay["replay_notes"]
    for step in ("extract", "summary"):
        assert h.output(replay, step).read_bytes() == h.output(run, step).read_bytes()

    # Control: another seed changes the bootstrap column, so the seed is what pins it.
    other = await h.run("weekly_steps.yaml", seed=2027)
    assert h.output(other, "summary").read_bytes() != h.output(run, "summary").read_bytes()


async def test_a_small_cell_in_real_r_output_stops_delivery(harness):
    h = harness
    h.write("weekly_steps.yaml", CHAIN)
    run = await h.run("weekly_steps.yaml", {"suppress": False})
    statuses = {s["step_id"]: s["status"] for s in run["steps"]}
    assert statuses == {
        "extract": "succeeded",
        "check_raw": "succeeded",
        "summary": "succeeded",
        "check_summary": "failed",
        "plausible": "skipped",
    }
    assert run["delivery_status"] == "skipped"
    assert not (h.settings.data_dir / "practice-exports").exists()


async def test_a_misbehaving_step_gets_nothing_out(harness):
    h = harness
    h.write("misbehaving.yaml", MISBEHAVING)
    run = await h.run("misbehaving.yaml")
    sneaky = run["steps"][1]
    assert sneaky["status"] == "failed" and sneaky["outputs"] == {}
    [message] = sneaky["result"]["messages"]
    assert message["text"] == "network: blocked - input write: refused"
    assert "looks_innocent.csv" in sneaky["message"]
    extract = h.output(run, "extract")
    assert extract.read_text().startswith("STUDY_PARTICIPANT_ID")


async def test_stop_removes_the_step_container(harness):
    h = harness
    h.write("sleeps.yaml", SLEEPS)
    run_id = await h.runner.start("sleeps.yaml")
    deadline = time.time() + 60
    while not containers_of(h) and time.time() < deadline:
        await asyncio.sleep(0.2)
    assert containers_of(h)
    began = time.monotonic()
    assert await h.runner.stop(run_id)
    assert time.monotonic() - began < 15
    run = h.runner.detail(run_id)
    assert run is not None and run["status"] == "cancelled"
    assert containers_of(h) == []


async def test_a_pipeline_runs_from_its_built_package(harness):
    h = harness
    package = h.folder / "ihsDataR"
    (package / "R").mkdir(parents=True)
    (package / "DESCRIPTION").write_text(
        "Package: ihsDataR\nTitle: Test Pipelines\nVersion: 0.0.1\n"
        'Authors@R: person("Test", "Person", role = c("aut", "cre"), email = "t@example.org")\n'
        "Description: A stand-in for the lab's pipelines package, in tests.\n"
        "License: MIT\nEncoding: UTF-8\n"
    )
    (package / "NAMESPACE").write_text("export(device_days)\n")
    (package / "R" / "devices.R").write_text(
        "device_days <- function(x) {\n"
        "  out <- aggregate(STEPS ~ DEVICE, data = x, FUN = length)\n"
        '  names(out) <- c("DEVICE", "days")\n'
        "  out[order(out$DEVICE), ]\n"
        "}\n"
    )
    pipeline = package / "inst" / "pipelines" / "weekly_devices"
    pipeline.mkdir(parents=True)
    (pipeline / "pipeline.yaml").write_text(PIPELINE_YAML)
    (pipeline / "run.R").write_text(
        "x <- read.csv(inputs$IHS_2025.WEARABLE_DAILY)\n"
        "write.csv(ihsDataR::device_days(x), outputs$devices, row.names = FALSE)\n"
    )
    h.write("devices.yaml", USES_PIPELINE)
    run = await h.run("devices.yaml")
    assert run["status"] == "succeeded", [(s["step_id"], s["message"]) for s in run["steps"]]
    metrics = run["steps"][0]
    assert metrics["inputs"]["IHS_2025.WEARABLE_DAILY"]["folder"] == "extracts"
    [query] = metrics["queries"]
    assert query["binds"] == {"start_date": "2025-04-15"} and "WHERE" in query["sql"]
    [library] = run["pipelines"]
    assert library["package"] == "ihsDataR" and library["library_sha256"]
    devices = h.output(run, "metrics", "devices").read_text().splitlines()
    assert devices[0] == '"DEVICE","days"' and len(devices) == 4
    assert (Path(h.run_dir(run)) / "pipelines/weekly_devices/run.R").is_file()

    replay = await h.finish(await h.runner.replay(run["id"]))
    assert replay["reproduced"] == 1, replay["replay_notes"]
