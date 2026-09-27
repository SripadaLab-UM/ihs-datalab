"""The pipelines repo in DataLab: the clone, each Data engineering conversation's
copy, proposed changes, the package's tests, and Save & share.

GitHub is a local bare repo (main protected against force-pushes, as on
GitHub), its API a mock transport, and the test container a fake that writes
testthat's results as the real wrapper does.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import db
from datalab.api.pipelines import PipelineServices, build_pipelines_router
from datalab.config import RepoSettings, Settings, WorkflowSettings
from datalab.pipelines.proposals import Proposal
from datalab.pipelines.service import UNAVAILABLE_NOTE, Pipelines
from datalab.pipelines.testing import COMMAND, read_results
from datalab.repos.github import Account, GitHubAuth, Tokens, TokenStore
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.tokens import SessionTokens
from datalab.workflows.sandbox import ContainerOutcome, ContainerStep, ImageFacts
from datalab.workflows.source import workflows_root
from tests.kb_fixtures import Remote

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


def test_only_data_engineering_conversations_get_a_copy_of_the_repo(lab):
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
    analysis = conversation(lab, mode="analysis")
    assert not copy_of(lab, analysis).exists()


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
    # Its copy of the files is gone once it's done; the log stays.
    assert not (lab.pipelines.tests.folder / test["id"]).exists()


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


def test_workflows_come_from_the_pipelines_clone_when_its_set(tmp_path):
    data = tmp_path / "data"
    repos = RepoSettings(pipelines="SripadaLab-UM/ihs-pipelines", client_id="Iv23liTESTCLIENT")
    real = Settings(profile="real", data_dir=data, oracle=None, repos=repos)
    assert workflows_root(real) == data / "repos" / "ihs-pipelines"
    assert not (data / "repos" / "ihs-pipelines").exists()  # made by the first sync, not here
    folder = Settings(
        profile="real",
        data_dir=data,
        oracle=None,
        repos=repos,
        workflows=WorkflowSettings(folder=str(tmp_path / "mine")),
    )
    assert workflows_root(folder) == tmp_path / "mine"
    practice = Settings(profile="practice", data_dir=data, oracle=None, repos=repos)
    assert workflows_root(practice) == data / "workflows-local"
    assert (data / "workflows-local").is_dir()
