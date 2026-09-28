"""The real DataLab's Workflows tab lists the lab's workflows once the pipelines repo is synced.

After GitHub sign-in and a sync, `<data folder>/repos/ihs-pipelines` is a
clone whose `origin/main` holds `workflows/*.yaml`, and the tab lists them
from it (workflows/source.py). These tests build such a clone by hand, from
files given here, and check the tab's list.

`test_the_labs_own_workflows_are_listed` does the same with the lab's real
repo, which is private and never copied here: set
`DATALAB_TEST_PIPELINES_ARCHIVE` to a `git archive` tarball of it (made
outside this repo), and it builds the clone from that and checks every
workflow file in it is listed. Without it the test is skipped.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.workflows import WorkflowServices, build_workflows_router
from datalab.config import RepoSettings, WorkflowSettings
from tests.test_workflow_stages import DAILY
from tests.workflow_fakes import Harness

REPO = "SripadaLab-UM/ihs-pipelines"


def git(cwd: Path, *args: str) -> str:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.org",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.org",
    }
    done = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def synced_clone(tmp_path: Path, data_dir: Path, tree: Path) -> Path:
    """`tree` committed on `main` of a stand-in GitHub repo, and cloned where
    DataLab keeps the synced pipelines repo."""
    remote = tmp_path / "github-ihs-pipelines"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "main")
    subprocess.run(["cp", "-R", f"{tree}/.", str(remote)], check=True)
    git(remote, "add", "-A")
    git(remote, "commit", "-q", "-m", "The lab's repo")
    clone = data_dir / "repos" / REPO.split("/")[1]
    clone.parent.mkdir(parents=True, exist_ok=True)
    git(clone.parent, "clone", "-q", str(remote), clone.name)
    return clone


def real_client(tmp_path: Path) -> tuple[TestClient, Harness]:
    h = Harness(tmp_path, profile="real")
    settings = replace(
        h.settings,
        workflows=WorkflowSettings(),
        repos=RepoSettings(pipelines=REPO, client_id="Iv23liTESTCLIENT"),
        oracle=None,
    )
    app = FastAPI()
    app.include_router(
        build_workflows_router(
            WorkflowServices(settings, h.connection, h.data, h.access_log, sandbox=h.sandbox)
        )
    )
    return TestClient(app), h


def test_synced_workflows_are_listed(tmp_path):
    tree = tmp_path / "tree"
    (tree / "workflows").mkdir(parents=True)
    (tree / "workflows" / "daily_clean.yaml").write_text(DAILY)
    (tree / "workflows" / "other.yaml").write_text(DAILY.replace("daily_clean", "other"))
    (tree / "AGENTS.md").write_text("# ihs-pipelines\n")
    client, h = real_client(tmp_path)
    with client:
        # Before the first sync: nothing from the repo, and the tab says why.
        status = client.get("/api/workflows/status").json()
        assert status["profile"] == "real" and "hasn't been synced" in status["message"]
        assert client.get("/api/workflows").json() == []
        synced_clone(tmp_path, h.settings.data_dir, tree)
        listed = {w["path"]: w for w in client.get("/api/workflows").json()}
        assert set(listed) == {"workflows/daily_clean.yaml", "workflows/other.yaml"}
        assert all(
            w["source"] == "git" and w["commit"] and not w["builtin"] for w in listed.values()
        )
        # Saving a new one is Save & share, never a write into the clone.
        assert client.get("/api/workflows/status").json()["target"]["kind"] in (
            "share",
            "unavailable",
        )


@pytest.mark.skipif(
    not os.environ.get("DATALAB_TEST_PIPELINES_ARCHIVE"),
    reason="Set DATALAB_TEST_PIPELINES_ARCHIVE to a git archive of the lab's pipelines repo.",
)
def test_the_labs_own_workflows_are_listed(tmp_path):
    archive = Path(os.environ["DATALAB_TEST_PIPELINES_ARCHIVE"])
    tree = tmp_path / "tree"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree, filter="data")
    expected = sorted(
        f"workflows/{p.name}" for p in (tree / "workflows").iterdir() if p.suffix == ".yaml"
    )
    client, h = real_client(tmp_path)
    with client:
        synced_clone(tmp_path, h.settings.data_dir, tree)
        listed = {w["path"]: w for w in client.get("/api/workflows").json()}
        assert sorted(listed) == expected
        problems = {p: w["problems"] for p, w in listed.items() if not w["valid"]}
        print(f"{len(listed)} listed, {len(listed) - len(problems)} pass the file check")
        for path, found in problems.items():
            print(path, [f"{p['path']}: {p['message']}" for p in found])
        assert not problems
