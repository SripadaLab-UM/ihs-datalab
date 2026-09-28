"""The pipelines repo in DataLab: the clone, each Data engineering conversation's
copy, proposed changes, the package's tests, and Save & share.

GitHub is a local bare repo (main protected against force-pushes, as on
GitHub), its API a mock transport, and the test container a fake that writes
testthat's results as the real wrapper does.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import db
from datalab.api.pipelines import PipelineServices, build_pipelines_router
from datalab.config import RepoSettings, Settings, WorkflowSettings
from datalab.pipelines.check import check
from datalab.pipelines.proposals import Proposal
from datalab.pipelines.proposals import TestRun as Run
from datalab.pipelines.service import UNAVAILABLE_NOTE, Pipelines
from datalab.pipelines.testing import COMMAND, PackageTests, read_results
from datalab.repos.github import Account, GitHubAuth, Tokens, TokenStore
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.tokens import SessionTokens
from datalab.workflows.sandbox import ContainerOutcome, ContainerStep, ImageFacts
from datalab.workflows.source import workflows_folder
from tests.kb_fixtures import Remote, git

TOKEN = "ghu_test_access_token_0123456789"
REFRESH = "ghr_test_refresh_token_0123456789"
ME = Account("yfang", 42, "Yu Fang")
DIGEST = "sha256:" + "a" * 64

STEPS_R = "weekly_steps <- function(x) {\n  x\n}\n"
TEST_R = 'test_that("steps", {\n  expect_equal(weekly_steps(1), 1)\n})\n'


def sample_repo() -> dict[str, bytes]:
    return {
        "AGENTS.md": b"# ihs-pipelines\n\nRun the tests.\n",
        "ihsDataR/DESCRIPTION": b"Package: ihsDataR\nVersion: 0.1.0\n",
        "ihsDataR/.gitignore": b".Rproj.user\n",
        "ihsDataR/R/steps.R": STEPS_R.encode(),
        "ihsDataR/tests/testthat/test-steps.R": TEST_R.encode(),
        "workflows/weekly.yaml": b"name: weekly\nsteps: []\n",
        "reference/2024/Steps_2024.R": b"# legacy\n",
        ".github/workflows/ci.yml": b"name: ci\non: push\n",
    }


@dataclass
class FakeSandbox:
    """The test container: records what it was given, and answers as testthat would."""

    failing: int = 0  # how many tests fail
    exit_code: int = 0
    write_results: bool = True
    steps: list[ContainerStep] = field(default_factory=list)
    trees: list[dict[str, str]] = field(default_factory=list)  # the files each run saw
    removed: list[str] = field(default_factory=list)

    async def image(self, ref: str) -> ImageFacts | None:
        return ImageFacts(ref, DIGEST, "linux/arm64", (), "R version 4.6.1", "f" * 64)

    async def host_platform(self) -> str:
        return "linux/arm64"

    async def run(self, step: ContainerStep) -> ContainerOutcome:
        self.steps.append(step)
        source = next(b for b in step.binds if b.target == "/run/src")
        out = next(b for b in step.binds if b.target == "/run/out")
        assert source.readonly and not out.readonly
        self.trees.append(
            {
                p.relative_to(source.source).as_posix(): p.read_text()
                for p in source.source.rglob("*")
                if p.is_file()
            }
        )
        if self.write_results:
            rows = ['"file","test","nb","failed","skipped","error","warning"']
            rows += ['"test-steps.R","steps",1,0,FALSE,FALSE,0'] * 3
            rows += ['"test-steps.R","broken",1,1,FALSE,FALSE,0'] * self.failing
            (out.source / "results.csv").write_text("\n".join(rows) + "\n")
        return ContainerOutcome(exit_code=self.exit_code, log=b"== Testing ihsDataR ==\n")

    async def remove_run(self, run_id: str) -> None:
        self.removed.append(run_id)


@dataclass
class Lab:
    pipelines: Pipelines
    manager: SessionManager
    store: ConversationStore
    remote: Remote
    client: TestClient
    sandbox: FakeSandbox


@pytest.fixture
def lab(tmp_path, github_keychain):
    remote = Remote(tmp_path / "github-pipelines", sample_repo())
    settings = Settings(
        profile="real",
        data_dir=tmp_path / "data",
        oracle=None,
        repos=RepoSettings(
            pipelines="SripadaLab-UM/ihs-pipelines",
            client_id="Iv23liTESTCLIENT",
            access_contact="Ali",
        ),
    )
    TokenStore().save(Tokens(TOKEN, time.time() + 8 * 3600, REFRESH, time.time() + 1e7, ME))

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)  # a repository the person can't see

    auth = GitHubAuth("Iv23liTESTCLIENT", http=httpx.Client(transport=httpx.MockTransport(answer)))
    connection = db.connect(settings.database_file)
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    sandbox = FakeSandbox()
    router = build_pipelines_router(
        PipelineServices(
            settings, connection, store, manager, auth=auth, sandbox=sandbox, remote=remote.url
        )
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield Lab(router.pipelines, manager, store, remote, client, sandbox)  # type: ignore[attr-defined]
    connection.close()


def synced(lab: Lab) -> None:
    assert lab.client.post("/api/pipelines/sync").json()["repo"] == "in sync"


def conversation(lab: Lab, mode: str = "engineering") -> str:
    made = lab.store.create(kind="data", mode=mode, title="Weekly steps", model="m")
    lab.manager._seed_workspace(made)
    return made.id


def copy_of(lab: Lab, cid: str) -> Path:
    return lab.manager.paths(cid).work / "pipelines"


def turn(lab: Lab, cid: str, edits: dict[str, str | bytes | None]) -> Proposal | None:
    """The agent changes its copy; the turn ends with a checkpoint and the hook."""
    folder = copy_of(lab, cid)
    for path, content in edits.items():
        target = folder / path
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    paths = lab.manager.paths(cid)
    number = Checkpoints(paths.checkpoints, paths.work).take("After a turn").number
    before = lab.pipelines.store.latest(cid)
    asyncio.run(lab.manager._run_after_turn(cid, TurnInfo(number, "completed", 0, number)))
    after = lab.pipelines.store.latest(cid)
    return after if after is not None and (before is None or after.id != before.id) else None


def settled(lab: Lab, proposal_id: str) -> dict:
    """The proposal once its tests or its save have finished (they run in the background)."""
    for _ in range(200):
        found = lab.client.get(f"/api/pipelines/proposals/{proposal_id}").json()
        test = found["proposal"]["test"]
        testing = test is not None and test["status"] == "running"
        if found["proposal"]["status"] != "saving" and not testing:
            return found
        time.sleep(0.05)
    raise AssertionError("still going")


def accept(lab: Lab, proposal: Proposal, confirmed: list[str] | None = None) -> dict:
    response = lab.client.post(
        f"/api/pipelines/proposals/{proposal.id}/accept", json={"confirmed": confirmed or []}
    )
    assert response.status_code == 200, response.text
    return settled(lab, proposal.id)


NEW_STEPS = "weekly_steps <- function(x) {\n  x * 7\n}\n"


# The copy -----------------------------------------------------------------


def test_only_engineering_and_workflow_authoring_conversations_get_a_copy_of_the_repo(lab):
    synced(lab)
    cid = conversation(lab)
    folder = copy_of(lab, cid)
    assert (folder / "ihsDataR" / "R" / "steps.R").read_text() == STEPS_R
    assert (folder / "workflows" / "weekly.yaml").is_file()
    # Not the automation, nor names every disk can't take; no git, no credentials.
    assert not (folder / ".github").exists() and not (folder / "ihsDataR" / ".gitignore").exists()
    assert not (folder / ".git").exists()
    everything = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    assert TOKEN.encode() not in everything and REFRESH.encode() not in everything
    assert lab.pipelines.base(cid) == lab.remote.head()
    for mode in ("analysis", "extraction", "knowledge"):
        assert not copy_of(lab, conversation(lab, mode=mode)).exists()
    authoring = conversation(lab, mode="workflows")
    assert (copy_of(lab, authoring) / "workflows" / "weekly.yaml").is_file()
    assert lab.pipelines.base(authoring) == lab.remote.head()
    # The Pipelines tab's own chat works on the same copy.
    tab = conversation(lab, mode="pipelines")
    assert (copy_of(lab, tab) / "ihsDataR" / "R" / "steps.R").read_text() == STEPS_R
    assert lab.pipelines.base(tab) == lab.remote.head()


def test_a_workflow_authoring_draft_becomes_a_pipelines_proposal(lab):
    """The agent drafts in workflows/; the person reviews and saves it in the
    Pipelines tab, like any Data engineering change."""
    synced(lab)
    cid = conversation(lab, mode="workflows")
    draft = "name: monthly\nsteps: []\n"
    proposal = turn(lab, cid, {"workflows/monthly.yaml": draft})
    assert proposal is not None
    assert [(c.path, c.change) for c in proposal.files] == [("workflows/monthly.yaml", "added")]
    listed = lab.client.get("/api/pipelines/proposals").json()
    assert [p["id"] for p in listed if p["conversation_id"] == cid] == [proposal.id]


def test_without_a_clone_the_copy_says_so_and_nothing_is_proposed(lab):
    cid = conversation(lab)
    assert (copy_of(lab, cid) / "README.md").read_text() == UNAVAILABLE_NOTE
    assert lab.pipelines.base(cid) is None
    assert turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS}) is None


def test_status_sync_and_browsing(lab):
    status = lab.client.get("/api/pipelines/status").json()
    assert (status["repo"], status["signed_in"]) == ("not cloned", True)
    assert status["account"] == {"login": "yfang", "name": "Yu Fang"}
    assert lab.client.get("/api/pipelines/files").json()["files"] == []
    synced(lab)
    tree = lab.client.get("/api/pipelines/files").json()
    assert tree["head"] == lab.remote.head()
    assert {"path": "ihsDataR/R/steps.R", "size": len(STEPS_R)} in tree["files"]
    shown = lab.client.get("/api/pipelines/files/ihsDataR/R/steps.R").json()
    assert (shown["text"], shown["too_large"]) == (STEPS_R, False)
    assert lab.client.get("/api/pipelines/files/ihsDataR/R/nope.R").status_code == 404
    assert lab.client.get("/api/pipelines/files/..%2Fsecrets").status_code == 404
    lab.remote.write({"workflows/other.yaml": b"name: other\n"}, "Someone else")
    lab.pipelines._repo().clone.fetch()
    assert lab.client.get("/api/pipelines/status").json()["behind"] == 1


def test_missing_access_says_whom_to_ask(lab, tmp_path):
    lab.pipelines._repo().clone.remote = str(tmp_path / "not-there.git")
    status = lab.client.post("/api/pipelines/sync").json()
    assert status["repo"] == "no access"
    assert "@yfang" in status["message"] and "Ask Ali" in status["message"]


def test_not_configured_in_practice(tmp_path, github_keychain):
    settings = Settings(profile="practice", data_dir=tmp_path / "data", oracle=None)
    connection = db.connect(settings.database_file)
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    router = build_pipelines_router(
        PipelineServices(settings, connection, store, manager, auth=None, sandbox=FakeSandbox())
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        status = client.get("/api/pipelines/status").json()
        assert (status["available"], status["repo"]) == (False, "not configured")
        assert client.post("/api/pipelines/sync").status_code == 409
    connection.close()


# Proposals ------------------------------------------------------------------


def test_changes_are_proposed_and_what_cant_be_is_refused_with_reasons(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(
        lab,
        cid,
        {
            "ihsDataR/R/steps.R": NEW_STEPS,
            "workflows/monthly.yaml": "name: monthly\nsteps: []\n",
            "ihsDataR/tests/testthat/test-steps.R": None,
            "AGENTS.md": "# changed\n",
            ".github/workflows/ci.yml": "name: sneaky\n",
            "ihsDataR/data/raw.rds": b"\x00\x01binary",
        },
    )
    assert proposal is not None
    assert [(c.path, c.change) for c in proposal.files] == [
        ("ihsDataR/R/steps.R", "modified"),
        ("workflows/monthly.yaml", "added"),
        ("ihsDataR/tests/testthat/test-steps.R", "deleted"),
    ]
    reasons = {r.path: r.reason for r in proposal.refused}
    assert "the rest is there to read" in reasons["AGENTS.md"]
    assert "automation" in reasons[".github/workflows/ci.yml"]
    assert reasons["ihsDataR/data/raw.rds"] == "it isn't a text file"
    # Nothing new, nothing new proposed; undone, withdrawn.
    assert turn(lab, cid, {}) is None
    turn(
        lab,
        cid,
        {
            "ihsDataR/R/steps.R": STEPS_R,
            "workflows/monthly.yaml": None,
            "ihsDataR/tests/testthat/test-steps.R": TEST_R,
            "AGENTS.md": "# ihs-pipelines\n\nRun the tests.\n",
            ".github/workflows/ci.yml": None,
            "ihsDataR/data/raw.rds": None,
        },
    )
    assert lab.pipelines.get(proposal.id).status == "withdrawn"


def test_a_proposal_shows_each_files_diff_and_the_check(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS + "# ID ABC123456\n"})
    assert proposal is not None
    detail = lab.client.get(f"/api/pipelines/proposals/{proposal.id}").json()
    [changed] = detail["files"]
    assert (changed["before"], changed["binary"]) == (STEPS_R, False)
    assert "+  x * 7" in changed["diff"]
    assert [f["rule"] for f in detail["findings"]] == ["study_id"]
    listed = lab.client.get("/api/pipelines/proposals", params={"conversation_id": cid}).json()
    assert [(p["id"], p["conversation_title"], p["test"]) for p in listed] == [
        (proposal.id, "Weekly steps", None)
    ]


# Tests ------------------------------------------------------------------------


def test_the_packages_tests_run_on_the_proposals_files_in_the_container(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    started = lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests").json()
    assert started["proposal"]["test"]["status"] in ("running", "passed")
    test = settled(lab, proposal.id)["proposal"]["test"]
    assert (test["status"], test["tests"], test["passed"]) == ("passed", 3, 3)
    [step] = lab.sandbox.steps
    assert step.command == COMMAND and step.image == DIGEST
    [seen] = lab.sandbox.trees
    # Exactly the proposal's tree: the agent's change, no automation.
    assert seen["ihsDataR/R/steps.R"] == NEW_STEPS
    assert ".github/workflows/ci.yml" not in seen
    log = lab.client.get(f"/api/pipelines/tests/{test['id']}/log").json()
    assert log["text"] == "== Testing ihsDataR ==\n"
    # Its copy of the files is gone once it's done (just after the result is
    # recorded, so give it a moment); the log stays.
    copy = lab.pipelines.tests.folder / test["id"]
    for _ in range(100):
        if not copy.exists():
            break
        time.sleep(0.05)
    assert not copy.exists()


def test_failing_tests_say_which(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.sandbox.failing = 2
    lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests")
    test = settled(lab, proposal.id)["proposal"]["test"]
    assert (test["status"], test["failed"], test["message"]) == (
        "failed",
        2,
        "2 of 5 tests failed.",
    )
    assert test["failures"] == [{"file": "test-steps.R", "test": "broken", "kind": "failure"}] * 2


def test_tests_that_dont_finish_are_an_error_not_a_pass(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.sandbox.write_results, lab.sandbox.exit_code = False, 1
    lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests")
    test = settled(lab, proposal.id)["proposal"]["test"]
    assert test["status"] == "error"
    assert "didn't run to the end (exit 1)" in test["message"]


def test_results_are_read_from_testthats_data_frame(tmp_path):
    results = tmp_path / "results.csv"
    results.write_text(
        '"file","test","nb","failed","skipped","error","warning"\n'
        '"test-a.R","ok",2,0,FALSE,FALSE,1\n'
        '"test-a.R","skip",0,0,TRUE,FALSE,0\n'
        '"test-b.R","boom",1,0,FALSE,TRUE,0\n'
    )
    summary = read_results(results)
    assert summary is not None
    assert {k: summary[k] for k in ("tests", "passed", "skipped", "errors", "warnings")} == {
        "tests": 3,
        "passed": 1,
        "skipped": 1,
        "errors": 1,
        "warnings": 1,
    }
    assert summary["failures"] == [{"file": "test-b.R", "test": "boom", "kind": "error"}]
    assert read_results(tmp_path / "missing.csv") is None


def test_results_the_container_links_elsewhere_or_makes_a_pipe_arent_read(tmp_path):
    # The results folder is the container's to write: a link there would point
    # at the host's files, and a pipe would never end.
    secret = tmp_path / "secret.csv"
    secret.write_text(
        '"file","test","nb","failed","skipped","error","warning"\n"x","y",1,0,0,0,0\n'
    )
    (tmp_path / "linked.csv").symlink_to(secret)
    assert read_results(tmp_path / "linked.csv") is None
    os.mkfifo(tmp_path / "pipe.csv")
    assert read_results(tmp_path / "pipe.csv") is None


# Save & share -----------------------------------------------------------------


def test_save_and_share_tests_first_then_pushes_exactly_that_change(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "saved", done
    assert lab.remote.show("ihsDataR/R/steps.R") == NEW_STEPS.rstrip("\n")
    [line] = lab.remote.log("%an <%ae>|%s")[:1]
    assert line == "Yu Fang <42+yfang@users.noreply.github.com>|Pipelines: steps.R"
    body = lab.remote.log("%B")
    assert f"DataLab-Proposal: {proposal.id}" in "\n".join(body)
    assert f"DataLab-Tests: {done['result']['test']}" in "\n".join(body)
    assert done["commit"] == lab.remote.head()
    assert len(lab.sandbox.steps) == 1  # tested once, before the push
    # The base moved on: nothing more to propose until the agent changes more.
    assert lab.pipelines.base(cid) == proposal.commit
    assert turn(lab, cid, {}) is None
    status = lab.client.get("/api/pipelines/status").json()
    assert (status["repo"], status["head"]) == ("in sync", lab.remote.head())


def test_failing_tests_stop_the_save(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    before = lab.remote.head()
    lab.sandbox.failing = 1
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "tests_failed"
    assert "nothing was shared" in done["result"]["message"]
    assert lab.remote.head() == before
    # Fixed by the agent, a new proposal; this one's replaced.
    lab.sandbox.failing = 0
    again = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS + "\n"})
    assert again is not None and lab.pipelines.get(proposal.id).status == "superseded"
    assert accept(lab, again)["proposal"]["status"] == "saved"


def test_others_changes_to_the_package_are_tested_again_before_the_push(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests")
    assert settled(lab, proposal.id)["proposal"]["test"]["status"] == "passed"
    lab.remote.write({"ihsDataR/R/dates.R": b"to_date <- function(x) x\n"}, "Someone else")
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "saved"
    # Tested on its own tree, then again on the rebased commit that was pushed.
    assert len(lab.sandbox.steps) == 2
    assert "ihsDataR/R/dates.R" in lab.sandbox.trees[1]
    assert lab.sandbox.steps[1].binds[0].source != lab.sandbox.steps[0].binds[0].source
    assert lab.remote.show("ihsDataR/R/dates.R") == "to_date <- function(x) x"
    # The pushed commit names the run that passed on it: the second.
    message = git("log", "-1", "--format=%B", "main", cwd=lab.remote.bare)
    assert f"DataLab-Tests: {done['result']['test']}" in message
    first = lab.pipelines.store.latest_test(proposal.tree)
    assert first is not None and first.id != done["result"]["test"]


def test_install_and_load_code_waits_for_the_person_to_confirm(lab):
    synced(lab)
    cid = conversation(lab)
    hook = STEPS_R + "\n.onLoad <- function(libname, pkgname) {\n  system('curl x')\n}\n"
    proposal = turn(
        lab,
        cid,
        {"ihsDataR/R/steps.R": hook, "ihsDataR/configure": "#!/bin/sh\necho hi\n"},
    )
    assert proposal is not None
    findings = lab.client.get(f"/api/pipelines/proposals/{proposal.id}").json()["findings"]
    code = {(f["path"], f["rule"]): f for f in findings if f["severity"] == "code"}
    assert set(code) == {
        ("ihsDataR/R/steps.R", "load_hook"),
        ("ihsDataR/configure", "install_script"),
    }
    assert code[("ihsDataR/R/steps.R", "load_hook")]["text"].startswith(".onLoad <- function")
    assert code[("ihsDataR/R/steps.R", "load_hook")]["line"] == 5
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "check_failed"
    assert {f["rule"] for f in done["result"]["findings"]} == {"load_hook", "install_script"}
    saved = accept(lab, proposal, [f["id"] for f in code.values()])["proposal"]
    assert saved["status"] == "saved"


def test_others_changes_elsewhere_dont_need_the_tests_again(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.remote.write({"workflows/other.yaml": b"name: other\n"}, "Someone else")
    assert accept(lab, proposal)["proposal"]["status"] == "saved"
    assert len(lab.sandbox.steps) == 1


def test_failing_tests_after_others_changes_stop_the_save(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests")
    settled(lab, proposal.id)
    before = lab.remote.write({"ihsDataR/R/dates.R": b"broken <- \n"}, "Someone else")
    lab.sandbox.failing = 1
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "tests_failed"
    assert done["result"]["after_rebase"] is True
    assert lab.remote.head() == before


def test_a_conflict_shares_nothing(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    before = lab.remote.write(
        {"ihsDataR/R/steps.R": b"weekly_steps <- function(x) {\n  x + 1\n}\n"}, "Someone else"
    )
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "conflict"
    assert done["result"]["conflicts"] == ["ihsDataR/R/steps.R"]
    assert lab.remote.head() == before


def test_possible_participant_data_waits_for_the_person_to_confirm(lab):
    synced(lab)
    cid = conversation(lab)
    fixture = "id,date,steps\nABC123456,2025-03-01,9000\n"
    proposal = turn(lab, cid, {"ihsDataR/tests/testthat/fixtures/steps.csv": fixture})
    assert proposal is not None
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "check_failed"
    findings = done["result"]["findings"]
    assert findings and all(f["severity"] == "data" for f in findings)
    assert lab.sandbox.steps == []  # stopped before the tests
    saved = accept(lab, proposal, [f["id"] for f in findings])["proposal"]
    assert saved["status"] == "saved"


def test_discarding_moves_the_base_on(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    before = lab.remote.head()
    rejected = lab.client.post(f"/api/pipelines/proposals/{proposal.id}/reject").json()
    assert rejected["proposal"]["status"] == "rejected"
    assert lab.remote.head() == before
    again = lab.client.post(f"/api/pipelines/proposals/{proposal.id}/accept", json={})
    assert again.status_code == 409
    # The next proposal holds only what's new.
    later = turn(lab, cid, {"workflows/monthly.yaml": "name: monthly\n"})
    assert later is not None and [c.path for c in later.files] == ["workflows/monthly.yaml"]


def test_a_save_cut_off_by_datalab_stopping_says_so(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.pipelines.store.update(proposal, status="saving")
    run = lab.pipelines.store.add_test(proposal.tree, proposal_id=proposal.id, commit=None)
    lab.pipelines.store.end_interrupted()
    found = lab.pipelines.get(proposal.id)
    assert found.status == "failed"
    assert found.result["message"] == "DataLab stopped while saving. Try again."
    assert found.commit == proposal.commit
    assert lab.pipelines.store.get_test(run.id).status == "error"  # type: ignore[union-attr]


# Where workflow files come from -------------------------------------------------


def lab_settings(lab: Lab) -> Settings:
    return Settings(
        profile="real",
        data_dir=lab.pipelines.tests.folder.parent,
        oracle=None,
        repos=RepoSettings(pipelines="SripadaLab-UM/ihs-pipelines", client_id="Iv23liTESTCLIENT"),
    )


def test_workflows_come_from_the_pipelines_clone_once_its_synced(lab, tmp_path):
    settings = lab_settings(lab)
    local = settings.data_dir / "workflows-local"
    folder = workflows_folder(settings)
    # Until the first sync: the local folder, and it says so.
    assert folder.root == local and "hasn't been synced yet" in (folder.note or "")
    synced(lab)
    assert folder.root == lab.pipelines._repo().clone.path and folder.note is None
    assert folder.paths() == ["workflows/weekly.yaml"]
    # A folder in settings.toml wins; practice never uses the lab's repos.
    mine = WorkflowSettings(folder=str(tmp_path / "mine"))
    assert workflows_folder(replace(settings, workflows=mine)).root == tmp_path / "mine"
    assert workflows_folder(replace(settings, profile="practice")).root == local


def test_a_runs_snapshot_is_one_commit_whatever_syncs_meanwhile(lab, tmp_path):
    synced(lab)
    folder = workflows_folder(lab_settings(lab))
    first = lab.remote.head()
    run = folder.snapshot(tmp_path / "run" / "source")
    # Someone saves a new package and workflow; the tab syncs mid-run.
    changed = {"ihsDataR/R/steps.R": NEW_STEPS.encode(), "workflows/weekly.yaml": b"name: w\n"}
    lab.remote.write(changed, "Someone else")
    synced(lab)
    assert (folder.root / "ihsDataR/R/steps.R").read_text() == NEW_STEPS  # the checkout moved on
    # The run's copy didn't: it's all from the commit it started on, and says so.
    assert (run.package_dir / "R" / "steps.R").read_text() == STEPS_R
    workflow = run.read("workflows/weekly.yaml")
    assert (workflow.source, workflow.commit, workflow.text) == (
        "git",
        first,
        "name: weekly\nsteps: []\n",
    )
    assert run.package().tree_sha256 != folder.package().tree_sha256
    # Only what a run needs: not AGENTS.md, reference/ or .github/.
    copied = sorted(p.name for p in (tmp_path / "run" / "source").iterdir())
    assert copied == ["ihsDataR", "workflows"]


DEVICES_PIPELINE = b"""\
name: weekly_devices
reads:
  - object: IHS_2025.WEARABLE_DAILY
    columns: [STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS]
    where: RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
parameters: [start_date]
outputs: { devices: devices.csv }
"""
DEVICES_WORKFLOW = b"""\
name: devices
reads: [IHS_2025.WEARABLE_DAILY]
parameters:
  start_date: { type: date, default: 2025-04-15 }
steps:
  - id: extract
    sql: |
      SELECT STUDY_PARTICIPANT_ID, RECORD_DATE, DEVICE, STEPS
      FROM IHS_2025.WEARABLE_DAILY
      WHERE RECORD_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
    output: raw.csv
  - id: wait
    r: Sys.sleep(1)
    inputs: { raw: extract }
  - id: metrics
    pipeline: weekly_devices
"""


async def test_a_run_uses_the_commit_it_started_on_though_the_repo_syncs_mid_run(lab, tmp_path):
    from tests.workflow_fakes import FakeRun, FakeSandbox, Harness

    lab.remote.write(
        {
            "ihsDataR/inst/pipelines/weekly_devices/pipeline.yaml": DEVICES_PIPELINE,
            "ihsDataR/inst/pipelines/weekly_devices/run.R": b"write.csv(x, outputs$devices)\n",
            "workflows/devices.yaml": DEVICES_WORKFLOW,
        },
        "Add the devices pipeline",
    )
    synced(lab)
    started_on = lab.remote.head()
    release = asyncio.Event()
    waiting = asyncio.Event()

    async def wait(run: FakeRun) -> int:
        waiting.set()
        await release.wait()
        run.result()
        return 0

    def metrics(run: FakeRun) -> int:
        run.output("devices").write_text("DEVICE,days\nfitbit,3\n")
        run.result()
        return 0

    class Sandbox(FakeSandbox):
        async def run(self, step: ContainerStep) -> ContainerOutcome:
            if step.name.startswith("build-"):
                self.steps.append(step)
                return ContainerOutcome(exit_code=0, log=b"")
            return await super().run(step)

    h = Harness(tmp_path, sandbox=Sandbox({"wait": wait, "metrics": metrics}))
    h.runner.folder = workflows_folder(lab_settings(lab))
    run_id = await h.runner.start("workflows/devices.yaml")
    await waiting.wait()
    # Mid-run: someone saves a new package, and the Pipelines tab syncs.
    lab.remote.write({"ihsDataR/R/steps.R": NEW_STEPS.encode()}, "Someone else")
    await asyncio.to_thread(lab.pipelines.sync)
    assert (lab.pipelines._repo().clone.path / "ihsDataR/R/steps.R").read_text() == NEW_STEPS
    release.set()
    run = await h.finish(run_id)
    assert run["status"] == "succeeded", [(s["step_id"], s["message"]) for s in run["steps"]]
    # Recorded, and built, from the commit it started on.
    assert (run["workflow_source"], run["repo_commit"]) == ("git", started_on)
    built = h.run_dir(run) / "package-src"
    assert (built / "R" / "steps.R").read_text() == STEPS_R


# The check ------------------------------------------------------------------------


def test_a_small_id_map_is_caught_even_where_the_knowledge_bases_scan_isnt():
    fixture = b"participant_id,first_visit\n1001,2019-03-02\n1002,2019-03-09\n1003,2019-04-01\n"
    report = check({"ihsDataR/tests/testthat/fixtures/oracle_results/id_map.csv": fixture})
    rules = [f.rule for f in report.findings]
    assert rules.count("data_file") == 1 and rules.count("id_columns") == 1
    assert rules.count("date_near_number") == 3
    columns = next(f for f in report.findings if f.rule == "id_columns")
    assert "participant_id" in columns.message and columns.line == 1
    dated = [f for f in report.findings if f.rule == "date_near_number"]
    assert dated[0].text == "1001,2019-03-02"
    # Every one waits for the person; a confirmed one no longer does.
    assert len(report.blocking()) == len(report.findings)
    assert len(report.blocking([f.id for f in report.findings])) == 0


def test_test_code_with_ids_and_dates_is_flagged_but_ordinary_code_isnt():
    test = b'x <- data.frame(id = 204512, day = as.Date("2025-03-01"))\n'
    assert [f.rule for f in check({"ihsDataR/tests/testthat/test-x.R": test}).findings] == [
        "date_near_number"
    ]
    code = b'weekly <- function(x, since = as.Date("2025-01-01")) head(x, 1000)\n'
    assert check({"ihsDataR/R/weekly.R": code}).findings == []
    # A year isn't an ID.
    assert check({"ihsDataR/tests/testthat/test-y.R": b'"2025-03-01" # FY2025\n'}).findings == []


def test_code_that_runs_outside_the_test_container_is_named():
    files = {
        "ihsDataR/.Rprofile": b"options(x = 1)\n",
        "ihsDataR/cleanup": b"rm -f src/*.o\n",
        "ihsDataR/src/Makevars": b"PKG_LIBS = -lm\n",
        "ihsDataR/R/zzz.R": b".onAttach <- function(...) packageStartupMessage('hi')\n",
        "ihsDataR/R/ok.R": b"# not .onLoad\nf <- function() 1\n",
    }
    rules = {f.path: f.rule for f in check(files).findings if f.severity == "code"}
    assert rules == {
        "ihsDataR/.Rprofile": "startup_code",
        "ihsDataR/cleanup": "install_script",
        "ihsDataR/src/Makevars": "compiled_code",
        "ihsDataR/R/zzz.R": "load_hook",
    }


async def test_only_a_few_test_runs_go_at_once(tmp_path, monkeypatch):
    from datalab.pipelines import testing

    tests = PackageTests(sandbox=FakeSandbox(), image="x", store=None, folder=tmp_path)  # type: ignore[arg-type]
    going = most = 0

    async def slow(self, run, clone, commit, place):
        nonlocal going, most
        going += 1
        most = max(most, going)
        await asyncio.sleep(0.05)
        going -= 1
        return run

    monkeypatch.setattr(testing.PackageTests, "_run", slow)
    runs = [Run(f"pt_{i}", "t", None, "c", "running", "") for i in range(5)]
    await asyncio.gather(*(tests.execute(run, None) for run in runs))  # type: ignore[arg-type]
    assert most == testing.MAX_AT_ONCE == 2


def test_tests_get_a_folder_of_their_own_where_compiled_code_can_run(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"ihsDataR/R/steps.R": NEW_STEPS})
    assert proposal is not None
    lab.client.post(f"/api/pipelines/proposals/{proposal.id}/tests")
    settled(lab, proposal.id)
    [step] = lab.sandbox.steps
    binds = {b.target: b.readonly for b in step.binds}
    assert binds == {"/run/src": True, "/run/out": False, "/run/work": False}
    assert step.env == {"TMPDIR": "/run/work/tmp"}
    assert "cp -r /run/src/ihsDataR /run/work/pkg" in step.command[-1]


def test_save_and_share_runs_the_workflow_check_with_the_changes_own_pipelines(lab):
    """A workflow file that wouldn't run is an error that stops the save. Its
    pipelines are looked up in the change's own tree, so a pipeline added in
    the same change counts; and the real profile's small-cell rule applies."""
    from tests.test_workflow_model import PIPELINE, USES_PIPELINE

    synced(lab)
    cid = conversation(lab, mode="workflows")
    before = lab.remote.head()
    broken = USES_PIPELINE.replace("qc: { file: metrics", "qc: { file: nowhere")
    proposal = turn(lab, cid, {"workflows/metrics.yaml": broken})
    assert proposal is not None
    detail = lab.client.get(f"/api/pipelines/proposals/{proposal.id}").json()
    errors = [f for f in detail["findings"] if f["rule"] == "workflow"]
    assert {f["severity"] for f in errors} == {"error"}
    messages = " ".join(f["message"] for f in errors)
    assert "There's no pipeline 'daily_metrics'" in messages  # not in main's tree
    assert "'nowhere' isn't an earlier step" in messages
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "check_failed" and lab.remote.head() == before
    assert lab.sandbox.steps == []  # stopped before the tests

    # The pipeline in the same change, and a delivered CSV without a
    # small-cell check: the real profile's rule, though this DataLab is a test.
    delivers = (
        USES_PIPELINE
        + "deliver:\n  destination: practice-folder\n  folder: m\n  files: [metrics]\n"
    )
    again = turn(
        lab,
        cid,
        {
            "workflows/metrics.yaml": delivers,
            "ihsDataR/inst/pipelines/daily_metrics/pipeline.yaml": PIPELINE,
            "ihsDataR/inst/pipelines/daily_metrics/run.R": "x <- 1\n",
        },
    )
    assert again is not None
    detail = lab.client.get(f"/api/pipelines/proposals/{again.id}").json()
    [error] = [f for f in detail["findings"] if f["rule"] == "workflow"]
    assert "needs a small_cells check" in error["message"]
    # Fixed: it saves.
    fixed = delivers.replace(
        "  folder: m\n",
        "  folder: m\n  without_small_cells: { metrics: A row-level file the person asked for. }\n",
    )
    last = turn(lab, cid, {"workflows/metrics.yaml": fixed})
    assert last is not None
    detail = lab.client.get(f"/api/pipelines/proposals/{last.id}").json()
    assert [f for f in detail["findings"] if f["rule"] == "workflow"] == []
    assert accept(lab, last)["proposal"]["status"] == "saved"
    assert "without_small_cells" in lab.remote.show("workflows/metrics.yaml")


def test_only_workflow_files_get_the_workflow_check():
    from datalab.pipelines.check import is_workflow_file

    assert is_workflow_file("workflows/weekly.yaml") and is_workflow_file("workflows/a.YML")
    for path in ("workflows/nested/a.yaml", "workflows/.hidden.yaml", "workflows/notes.md",
                 "ihsDataR/inst/pipelines/p/pipeline.yaml"):  # fmt: skip
        assert not is_workflow_file(path)
    seen: list[str] = []
    files = {"workflows/a.yaml": b"name: a\n", "ihsDataR/R/x.R": b"x <- 1\n"}
    report = check(files, lambda text: seen.append(text) or ["one", "two"])
    assert seen == ["name: a\n"]
    assert [(f.path, f.severity, f.message) for f in report.findings if f.rule == "workflow"] == [
        ("workflows/a.yaml", "error", "The workflow check: one"),
        ("workflows/a.yaml", "error", "The workflow check: two"),
    ]


def test_an_alias_bomb_in_a_proposal_is_refused_by_its_check_and_save(lab):
    """The agent writes it under workflows/: opening the proposal and Save &
    share both read it through parse_yaml, which refuses it at once."""
    from tests.test_safeyaml import BOMB

    synced(lab)
    cid = conversation(lab, mode="workflows")
    before = lab.remote.head()
    proposal = turn(lab, cid, {"workflows/bomb.yaml": BOMB})
    assert proposal is not None
    started = time.monotonic()
    detail = lab.client.get(f"/api/pipelines/proposals/{proposal.id}").json()
    assert time.monotonic() - started < 5
    [error] = [f for f in detail["findings"] if f["rule"] == "workflow"]
    assert "anchors or aliases" in error["message"]
    done = accept(lab, proposal)["proposal"]
    assert done["status"] == "check_failed" and lab.remote.head() == before
