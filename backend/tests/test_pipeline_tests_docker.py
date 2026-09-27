"""The package's tests in a real container: testthat's counts, as the Pipelines tab shows them.

Needs Docker and the agent image (`DATALAB_TEST_AGENT_IMAGE`, default
`datalab-agent:dev`). Run with `uv run pytest -m docker`. The package is a
tiny made-up one in a local repo; its containers carry this test's own
instance label and `datalab.spike=m6test`, and only those are removed.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from datalab import db
from datalab.pipelines.proposals import PipelineStore
from datalab.pipelines.testing import PackageTests
from datalab.repos.git import Clone
from datalab.sessions.containers import instance_of
from datalab.workflows.sandbox import DockerSandbox, StepLimits
from tests.kb_fixtures import git

pytestmark = pytest.mark.docker

IMAGE = os.environ.get("DATALAB_TEST_AGENT_IMAGE", "datalab-agent:dev")

PACKAGE = {
    "ihsDataR/DESCRIPTION": (
        "Package: ihsDataR\nVersion: 0.1.0\nTitle: Test\nDescription: Test.\n"
        "License: MIT\nEncoding: UTF-8\n"
    ),
    "ihsDataR/NAMESPACE": "export(weekly)\n",
    "ihsDataR/R/weekly.R": "weekly <- function(x) x * 7\n",
    "ihsDataR/tests/testthat.R": "library(testthat)\nlibrary(ihsDataR)\ntest_check('ihsDataR')\n",
    "ihsDataR/tests/testthat/test-weekly.R": (
        'test_that("weekly", { expect_equal(weekly(1), 7) })\n'
        'test_that("wrong", { expect_equal(weekly(1), 8) })\n'
        'test_that("later", { skip("not today") })\n'
    ),
    ".github/workflows/ci.yml": "name: ci\n",
}


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def test_testthat_counts_come_back_from_the_container(tmp_path):
    if _docker("image", "inspect", IMAGE).returncode != 0:
        pytest.skip(f"Needs the agent image {IMAGE}")
    repo = tmp_path / "repo"
    for path, text in PACKAGE.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git("init", "-q", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "A package", cwd=repo)
    commit, tree = git("rev-parse", "HEAD", cwd=repo), git("rev-parse", "HEAD^{tree}", cwd=repo)
    data = tmp_path / "data"
    data.mkdir()
    instance = instance_of(data)
    tests = PackageTests(
        sandbox=DockerSandbox(
            profile="practice",
            instance=instance,
            limits=StepLimits(timeout_seconds=300, memory="2g", cpus="1"),
            cache_dir=data / "workflow-cache",
            extra_labels={"datalab.spike": "m6test"},
        ),
        image=IMAGE,
        store=PipelineStore(db.connect(data / "datalab.sqlite")),
        folder=data / "pipeline-tests",
    )
    try:
        run = tests.begin(commit, tree, proposal_id=None)
        done = asyncio.run(tests.execute(run, Clone(repo, str(repo), allow_local=True)))
    finally:
        listed = _docker(
            "ps", "-aq",
            "--filter", f"label=datalab.instance={instance}",
            "--filter", "label=datalab.spike=m6test",
        ).stdout.split()  # fmt: skip
        if listed:
            _docker("rm", "-f", *listed)
    assert (done.status, done.message) == ("failed", "1 of 3 tests failed.")
    counts = {k: done.summary[k] for k in ("tests", "passed", "failed", "skipped", "errors")}
    assert counts == {"tests": 3, "passed": 1, "failed": 1, "skipped": 1, "errors": 0}
    [failure] = done.summary["failures"]
    assert failure == {"file": "test-weekly.R", "test": "wrong", "kind": "failure"}
    log, _ = tests.log_tail(run.id)
    assert "[ FAIL 1 | WARN 0 | SKIP 1 | PASS 1 ]" in log
