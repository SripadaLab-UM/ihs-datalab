"""The knowledge base in conversations: the copy, proposed edits, and Save & share.

GitHub is a local bare repo (with main protected against force-pushes and
deletion, as on GitHub), and its API a mock transport.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab import db
from datalab.api.knowledge import KnowledgeServices, build_knowledge_router
from datalab.config import RepoSettings, Settings
from datalab.knowledge import check as kb
from datalab.knowledge.proposals import MAX_DIFF_TOTAL, Proposal
from datalab.knowledge.service import UNAVAILABLE_NOTE, Knowledge
from datalab.repos.github import Account, GitHubAuth, Tokens, TokenStore
from datalab.sessions.checkpoints import Checkpoints
from datalab.sessions.hooks import TurnInfo
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore
from datalab.sessions.tokens import SessionTokens
from tests.kb_fixtures import FITBIT, MIDNIGHT, Remote, page

TOKEN = "ghu_test_access_token_0123456789"
REFRESH = "ghr_test_refresh_token_0123456789"
ME = Account("yfang", 42, "Yu Fang")


@dataclass
class Lab:
    knowledge: Knowledge
    manager: SessionManager
    store: ConversationStore
    remote: Remote
    client: TestClient
    github: dict


@pytest.fixture
def lab(tmp_path, github_keychain):
    remote = Remote(tmp_path)
    settings = Settings(
        profile="real",
        data_dir=tmp_path / "data",
        oracle=None,
        repos=RepoSettings(
            knowledge="SripadaLab-UM/ihs-knowledge",
            client_id="Iv23liTESTCLIENT",
            access_contact="Ali",
        ),
    )
    TokenStore().save(Tokens(TOKEN, time.time() + 8 * 3600, REFRESH, time.time() + 1e7, ME))
    github = {"repo_status": 200}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/"):
            return httpx.Response(github["repo_status"], json={"permissions": {"push": True}})
        if request.url.path == "/login/device/code":
            return httpx.Response(
                200,
                json={"device_code": "d", "user_code": "WXYZ-0000", "expires_in": 900,
                      "interval": 5, "verification_uri": "https://github.com/login/device"},
            )  # fmt: skip
        return httpx.Response(404)

    auth = GitHubAuth("Iv23liTESTCLIENT", http=httpx.Client(transport=httpx.MockTransport(answer)))
    connection = db.connect(settings.database_file)
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    router = build_knowledge_router(
        KnowledgeServices(settings, connection, store, manager, auth=auth, remote=remote.url)
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield Lab(router.knowledge, manager, store, remote, client, github)  # type: ignore[attr-defined]
    connection.close()


def conversation(lab: Lab) -> str:
    made = lab.store.create(kind="data", mode="analysis", title="t", model="m")
    lab.manager._seed_workspace(made)
    return made.id


def kb_dir(lab: Lab, cid: str) -> Path:
    return lab.manager.paths(cid).work / "kb"


def turn(
    lab: Lab, cid: str, edits: dict[str, str | bytes | None], *, max_file_bytes: int | None = None
) -> Proposal | None:
    """The agent edits its copy; the turn ends with a checkpoint and the hook."""
    folder = kb_dir(lab, cid)
    for path, content in edits.items():
        target = folder / path
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    paths = lab.manager.paths(cid)
    extra = {"max_file_bytes": max_file_bytes} if max_file_bytes else {}
    checkpoints = Checkpoints(paths.checkpoints, paths.work, **extra)
    number = checkpoints.take("After a turn").number
    before = lab.knowledge.store.latest(cid)
    asyncio.run(lab.manager._run_after_turn(cid, TurnInfo(number, "completed", 0, number)))
    after = lab.knowledge.store.latest(cid)
    return after if after is not None and (before is None or after.id != before.id) else None


def events(lab: Lab, cid: str, kind: str = "kb_proposal") -> list[dict]:
    return [e.data for e in lab.store.events_of_types_after(cid, 0, (kind,))]


def accept(lab: Lab, proposal: Proposal, confirmed: list[str] | None = None) -> dict:
    response = lab.client.post(
        f"/api/knowledge/proposals/{proposal.id}/accept", json={"confirmed": confirmed or []}
    )
    assert response.status_code == 200, response.text
    return response.json()


def synced(lab: Lab) -> None:
    assert lab.client.post("/api/knowledge/sync").json()["repo"] == "in sync"


NEW_PAGE = page("wear-time", "qc", "qc", body="Count a day only with 10 hours of wear.")


# The copy -----------------------------------------------------------------


def test_each_conversation_gets_a_copy_of_the_synced_commit(lab):
    synced(lab)
    cid = conversation(lab)
    folder = kb_dir(lab, cid)
    assert (folder / "sources" / "fitbit.md").read_text() == FITBIT
    assert not (folder / ".git").exists() and not (folder / ".github").exists()
    assert lab.knowledge.base(cid) == lab.remote.head()
    # Nothing that could hold a credential is in the copy.
    everything = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    assert TOKEN.encode() not in everything and REFRESH.encode() not in everything


def test_without_a_clone_the_copy_says_so_and_nothing_is_proposed(lab):
    cid = conversation(lab)
    assert (kb_dir(lab, cid) / "README.md").read_text() == UNAVAILABLE_NOTE
    assert lab.knowledge.base(cid) is None
    assert turn(lab, cid, {"qc/new.md": NEW_PAGE}) is None


def test_status_says_where_the_clone_is(lab):
    status = lab.client.get("/api/knowledge/status").json()
    assert (status["repo"], status["signed_in"]) == ("not cloned", True)
    assert status["account"] == {"login": "yfang", "name": "Yu Fang"}
    synced(lab)
    lab.remote.write({"qc/x.md": b"x"}, "Someone else")
    lab.knowledge.clone.fetch()
    assert lab.client.get("/api/knowledge/status").json()["behind"] == 1
    synced(lab)
    lab.client.post("/api/knowledge/sign-out")
    assert lab.client.get("/api/knowledge/status").json()["repo"] == "signed out"


def test_missing_access_says_whom_to_ask(lab, tmp_path):
    lab.knowledge.clone.remote = str(tmp_path / "not-there.git")
    lab.github["repo_status"] = 404
    status = lab.client.post("/api/knowledge/sync").json()
    assert status["repo"] == "no access"
    assert "@yfang" in status["message"] and "Ask Ali" in status["message"]


def test_sign_in_routes(lab):
    lab.client.post("/api/knowledge/sign-out")
    started = lab.client.post("/api/knowledge/sign-in").json()
    assert (started["state"], started["user_code"]) == ("waiting", "WXYZ-0000")
    assert lab.client.post("/api/knowledge/sign-in/poll").json()["state"] == "waiting"
    assert lab.client.post("/api/knowledge/sign-in/cancel").json()["state"] == "signed out"
    assert lab.client.get("/api/knowledge/sign-in").json()["state"] == "signed out"


# Proposals ------------------------------------------------------------------


def test_edits_are_proposed_and_what_cant_be_is_refused_with_reasons(lab):
    synced(lab)
    cid = conversation(lab)
    agent_fitbit = FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025 only.")
    agent_fitbit = agent_fitbit.replace("reviewed_by: yfang", "reviewed_by: codex")
    proposal = turn(
        lab,
        cid,
        {
            "sources/fitbit.md": agent_fitbit,
            "qc/wear-time.md": NEW_PAGE,
            "qc/midnight-sleep.md": None,
            "notes.txt": "scratch",
            "index.md": "# mine\n",
            "generated/schema/IHS_2025/NEW.yml": "schema: IHS_2025\n",
            "qc/chart.md": b"\x89PNG\r\n\x1a\n\x00\x00",
        },
    )
    assert proposal is not None
    changes = {c.path: c.change for c in proposal.files}
    assert changes == {
        "sources/fitbit.md": "modified",
        "qc/wear-time.md": "added",
        "qc/midnight-sleep.md": "deleted",
    }
    refused = {r.path: r.reason for r in proposal.refused}
    assert set(refused) == {"notes.txt", "index.md", "generated/schema/IHS_2025/NEW.yml",
                            "qc/chart.md"}  # fmt: skip
    assert "layout" in refused["notes.txt"] and "text" in refused["qc/chart.md"]
    [event] = events(lab, cid)
    assert event["id"] == proposal.id and event["base"] == lab.remote.head()
    assert "+  - Wear time is in 2025 only." in event["diff"]
    assert "codex" not in event["diff"]  # reviewed_by is DataLab's to set
    [fitbit] = [f for f in event["files"] if f["path"] == "sources/fitbit.md"]
    assert any("reviewed_by" in flag for flag in fitbit["flags"])
    assert (fitbit["added"], fitbit["removed"]) == (1, 1)
    # fitbit's `related` and its link to the deleted page now go nowhere.
    assert event["check"]["errors"] == 2


def test_links_and_oversize_files_are_refused(lab):
    synced(lab)
    cid = conversation(lab)
    folder = kb_dir(lab, cid)
    # A folder swapped for a link: refused, and its files aren't "deleted".
    (folder / "sources").rename(folder.parent / "moved")
    (folder / "sources").symlink_to(Path.home())
    (folder / "qc" / "keys.md").symlink_to(Path.home() / ".ssh" / "id_rsa")
    proposal = turn(
        lab,
        cid,
        {
            "qc/big.md": "a" * (kb.MAX_TEXT_BYTES + 10),
            "qc/huge.md": "b" * (kb.MAX_TEXT_BYTES + 5000),  # over this checkpoint's limit
        },
        max_file_bytes=kb.MAX_TEXT_BYTES + 1000,
    )
    assert proposal is not None and proposal.files == []
    refused = {r.path: r.reason for r in proposal.refused}
    assert "link" in refused["sources"] and "link" in refused["qc/keys.md"]
    assert "256 KB" in refused["qc/big.md"]
    assert "too large" in refused["qc/huge.md"]


def test_removing_the_whole_copy_proposes_nothing(lab):
    synced(lab)
    cid = conversation(lab)
    shutil.rmtree(kb_dir(lab, cid))
    proposal = turn(lab, cid, {})
    assert proposal is not None and proposal.files == []
    assert "removed or emptied" in proposal.refused[0].reason


def test_a_proposal_is_announced_once_replaced_when_it_changes_and_withdrawn_if_undone(lab):
    synced(lab)
    cid = conversation(lab)
    first = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE})
    assert first is not None
    assert turn(lab, cid, {}) is None  # nothing new: no second card
    second = turn(lab, cid, {"qc/other.md": page("other", "qc", "qc")})
    assert second is not None and len(second.files) == 2
    assert lab.knowledge.get(first.id).status == "superseded"
    turn(lab, cid, {"qc/wear-time.md": None, "qc/other.md": None})
    assert lab.knowledge.get(second.id).status == "withdrawn"
    updates = [(u["id"], u["status"]) for u in events(lab, cid, "kb_proposal_updated")]
    assert updates == [(first.id, "superseded"), (second.id, "withdrawn")]
    # Acting on a replaced proposal is refused.
    response = lab.client.post(f"/api/knowledge/proposals/{first.id}/accept", json={})
    assert response.status_code == 409


def test_the_diff_in_the_event_is_capped(lab):
    synced(lab)
    cid = conversation(lab)
    body = "\n".join(f"Line {n} of a long explanation." for n in range(7000))
    edits = {f"qc/long-{n}.md": page(f"long-{n}", "qc", "qc", body=body) for n in range(3)}
    turn(lab, cid, edits)
    [event] = events(lab, cid)
    assert event["truncated"] is True
    assert len(event["diff"]) <= MAX_DIFF_TOTAL + 200
    detail = lab.client.get(f"/api/knowledge/proposals/{event['id']}").json()
    assert all(f["after"] and len(f["diff"]) > 64 * 1024 for f in detail["files"])


# Save & share -------------------------------------------------------------


def test_save_and_share_pushes_exactly_the_reviewed_change_as_the_person(lab):
    synced(lab)
    cid = conversation(lab)
    started = lab.remote.head()
    fitbit = FITBIT.replace("Wear time isn't recorded.", "Wear time is in 2025 only.")
    proposal = turn(lab, cid, {"sources/fitbit.md": fitbit, "qc/wear-time.md": NEW_PAGE})
    assert proposal is not None
    detail = accept(lab, proposal)
    assert detail["proposal"]["status"] == "saved", detail["proposal"]["result"]
    [line, *_] = lab.remote.log()
    commit, rest = line.split(" ", 1)
    parent, author, subject = rest.split("|")
    assert commit == detail["proposal"]["commit"] and parent == started
    assert author == "Yu Fang <42+yfang@users.noreply.github.com>"
    assert subject == "Knowledge: qc/wear-time, sources/fitbit"
    message = lab.remote.log("%B")
    assert f"DataLab-Conversation: {cid}" in "\n".join(message)
    assert f"DataLab-Proposal: {proposal.id}" in "\n".join(message)
    shared = lab.remote.show("sources/fitbit.md")
    assert "Wear time is in 2025 only." in shared
    today = time.strftime("%Y-%m-%d")
    assert f"reviewed_by: yfang\nreviewed_on: {today}" in shared  # a reviewed page
    assert "reviewed_by" not in lab.remote.show("qc/wear-time.md")  # a draft
    assert "[wear-time](qc/wear-time.md)" in lab.remote.show("index.md")
    [update] = events(lab, cid, "kb_proposal_updated")
    assert (update["status"], update["commit"]) == ("saved", commit)
    # The clone follows, and the next proposal holds only what's new.
    assert lab.client.get("/api/knowledge/status").json()["head"] == commit
    assert turn(lab, cid, {}) is None
    later = turn(lab, cid, {"qc/other.md": page("other", "qc", "qc")})
    assert later is not None and [c.path for c in later.files] == ["qc/other.md"]
    assert accept(lab, later)["proposal"]["status"] == "saved"
    assert lab.remote.log("%P")[0] == commit


def test_the_persons_edits_are_what_gets_shared(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(
        lab, cid, {"qc/wear-time.md": NEW_PAGE, "qc/other.md": page("other", "qc", "qc")}
    )
    assert proposal is not None
    mine = NEW_PAGE.replace("10 hours", "12 hours")
    edited = lab.client.put(
        f"/api/knowledge/proposals/{proposal.id}/edits",
        json={"files": {"qc/wear-time.md": mine, "qc/other.md": None}},
    ).json()
    by_path = {f["path"]: f for f in edited["files"]}
    assert by_path["qc/wear-time.md"]["edited"] and "12 hours" in by_path["qc/wear-time.md"]["diff"]
    assert by_path["qc/other.md"]["left_out"] and by_path["qc/other.md"]["diff"] == ""
    refused = lab.client.put(
        f"/api/knowledge/proposals/{proposal.id}/edits", json={"files": {"qc/x.md": "x"}}
    )
    assert refused.status_code == 409
    assert accept(lab, proposal)["proposal"]["status"] == "saved"
    assert "12 hours" in lab.remote.show("qc/wear-time.md")
    with pytest.raises(subprocess.CalledProcessError):
        lab.remote.show("qc/other.md")  # left out
    # The agent's own versions are settled: nothing comes back next turn.
    assert turn(lab, cid, {}) is None


def test_discarding_shares_nothing_and_isnt_proposed_again(lab):
    synced(lab)
    cid = conversation(lab)
    before = lab.remote.head()
    proposal = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE})
    assert proposal is not None
    rejected = lab.client.post(f"/api/knowledge/proposals/{proposal.id}/reject").json()
    assert rejected["proposal"]["status"] == "rejected"
    assert lab.remote.head() == before
    assert turn(lab, cid, {}) is None
    more = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE + "\nOne more line.\n"})
    assert more is not None
    [diff] = [
        f["diff"] for f in lab.client.get(f"/api/knowledge/proposals/{more.id}").json()["files"]
    ]
    assert "+One more line." in diff and "+id: wear-time" not in diff


def test_someone_elses_change_elsewhere_is_kept_by_the_rebase(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE})
    assert proposal is not None
    theirs = lab.remote.write(
        {"sources/fitbit.md": FITBIT.replace("# Fitbit", "# Fitbit trackers").encode()}, "Theirs"
    )
    assert accept(lab, proposal)["proposal"]["status"] == "saved"
    assert lab.remote.log("%P")[0] == theirs  # on top of theirs, never forced
    assert "# Fitbit trackers" in lab.remote.show("sources/fitbit.md")
    assert "wear-time" in lab.remote.show("index.md")


def test_a_conflict_stops_the_save_and_a_resolution_finishes_it(lab):
    synced(lab)
    cid = conversation(lab)
    ours = MIDNIGHT.replace("Naps aren't covered.", "Naps under 3 hours aren't covered.")
    proposal = turn(lab, cid, {"qc/midnight-sleep.md": ours})
    assert proposal is not None
    theirs_text = MIDNIGHT.replace("Naps aren't covered.", "Naps are covered separately.")
    theirs = lab.remote.write({"qc/midnight-sleep.md": theirs_text.encode()}, "Theirs")
    detail = accept(lab, proposal)
    assert detail["proposal"]["status"] == "conflict"
    result = detail["proposal"]["result"]
    assert result["conflicts"] == ["qc/midnight-sleep.md"] and result["upstream"] == theirs
    assert lab.remote.head() == theirs  # nothing was pushed
    [file] = detail["files"]
    assert file["conflict"] and "separately" in file["theirs"]
    both = MIDNIGHT.replace(
        "Naps aren't covered.", "Naps are covered separately, if under 3 hours."
    )
    lab.client.put(
        f"/api/knowledge/proposals/{proposal.id}/edits",
        json={"files": {"qc/midnight-sleep.md": both}},
    )
    saved = accept(lab, proposal)
    assert saved["proposal"]["status"] == "saved", saved["proposal"]["result"]
    assert lab.remote.show("qc/midnight-sleep.md") + "\n" == both
    assert lab.remote.log("%P")[0] == theirs


def test_a_check_that_fails_after_the_rebase_pushes_nothing(lab):
    synced(lab)
    cid = conversation(lab)
    linked = NEW_PAGE.replace("cohorts: [2025]", "related: [qc/midnight-sleep]\ncohorts: [2025]")
    proposal = turn(lab, cid, {"qc/wear-time.md": linked})
    assert proposal is not None
    # Meanwhile someone else removes the page it links to.
    theirs = lab.remote.write(
        {
            "qc/midnight-sleep.md": None,
            "sources/fitbit.md": FITBIT.replace("related: [qc/midnight-sleep]\n", "")
            .replace("See [the sleep rule](../qc/midnight-sleep.md).\n", "")
            .encode(),
        },
        "Theirs",
    )
    detail = accept(lab, proposal)
    result = detail["proposal"]["result"]
    assert detail["proposal"]["status"] == "check_failed"
    assert result["after_rebase"] is True
    assert any("qc/midnight-sleep" in f["message"] for f in result["findings"])
    assert lab.remote.head() == theirs


def test_possible_participant_data_blocks_until_a_person_confirms_it(lab):
    synced(lab)
    cid = conversation(lab)
    leaky = page("wear-time", "qc", "qc", body="For example, SYN001 wore it 9 hours.")
    proposal = turn(lab, cid, {"qc/wear-time.md": leaky})
    assert proposal is not None
    assert events(lab, cid)[0]["check"]["data"] == 1
    before = lab.remote.head()
    blocked = accept(lab, proposal)
    assert blocked["proposal"]["status"] == "check_failed"
    [hit] = blocked["proposal"]["result"]["findings"]
    assert hit["severity"] == "data" and hit["rule"] == "study_id"
    assert lab.remote.head() == before
    [shown] = [f for f in blocked["findings"] if f["severity"] == "data"]
    assert shown["id"] == hit["id"]
    assert accept(lab, proposal, [hit["id"]])["proposal"]["status"] == "saved"


def test_if_main_moves_during_the_save_it_rebases_again_never_forcing(lab, monkeypatch):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE})
    assert proposal is not None
    clone = lab.knowledge.clone
    real_push = clone.push
    raced: list[str] = []

    def push(commit: str):
        if not raced:
            raced.append(lab.remote.write({"qc/race.md": b"theirs\n"}, "Raced"))
        return real_push(commit)

    monkeypatch.setattr(clone, "push", push)
    assert accept(lab, proposal)["proposal"]["status"] == "saved"
    assert lab.remote.log("%P")[0] == raced[0]
    assert lab.remote.show("qc/race.md") == "theirs"


def test_saving_needs_someone_signed_in(lab):
    synced(lab)
    cid = conversation(lab)
    proposal = turn(lab, cid, {"qc/wear-time.md": NEW_PAGE})
    assert proposal is not None
    lab.client.post("/api/knowledge/sign-out")
    response = lab.client.post(f"/api/knowledge/proposals/{proposal.id}/accept", json={})
    assert response.status_code == 403
    assert lab.knowledge.get(proposal.id).status == "open"


def test_proposals_are_listed_per_conversation(lab):
    synced(lab)
    one, two = conversation(lab), conversation(lab)
    turn(lab, one, {"qc/a.md": page("a", "qc", "qc")})
    turn(lab, two, {"qc/b.md": page("b", "qc", "qc")})
    listed = lab.client.get("/api/knowledge/proposals", params={"conversation_id": one}).json()
    assert [f["path"] for p in listed for f in p["files"]] == ["qc/a.md"]
    assert len(lab.client.get("/api/knowledge/proposals").json()) == 2
    assert lab.client.get("/api/knowledge/proposals/kp_nope").status_code == 404


def test_the_practice_profile_never_uses_the_lab_repos(settings, tmp_path):
    connection = db.connect(tmp_path / "db.sqlite")
    store = ConversationStore(connection)
    manager = SessionManager(settings, store, SessionTokens())
    knowledge = Knowledge(settings, connection, store, manager)
    assert not knowledge.available and "Practice" in (knowledge.unavailable or "")
    assert manager._seeds == [] and manager._after_turn == []
    assert os.path.exists(tmp_path / "db.sqlite")


# The token stays out of containers ----------------------------------------


def test_the_github_token_never_reaches_a_container(lab, monkeypatch):
    from datalab.sessions import codex_config
    from datalab.sessions import containers as module

    synced(lab)
    cid = conversation(lab)
    seen: list[str] = []

    async def docker(*args: str, check: bool = True) -> str:
        seen.append(" ".join(args))
        if args[0] == "run" and "--env-file" in args:
            seen.append(Path(args[args.index("--env-file") + 1]).read_text())
        return "Accepting HTTP Socket connections" if args[0] == "logs" else ""

    monkeypatch.setattr(module, "docker", docker)
    for kind in ("data", "research"):
        asyncio.run(lab.manager._containers_for(cid, kind).start("session-token-abc"))
        seen.append(codex_config.render(kind, model="m", tool_timeout_seconds=1))
    everything = "\n".join(seen)
    assert "DATALAB_SESSION_TOKEN=session-token-abc" in everything  # the env was read
    assert TOKEN not in everything and REFRESH not in everything
    # The clone is never mounted: the agent gets a copy of the files.
    assert str(lab.knowledge.clone.path.parent) not in everything
    work = lab.manager.paths(cid).root
    files = b"".join(p.read_bytes() for p in work.rglob("*") if p.is_file())
    assert TOKEN.encode() not in files and REFRESH.encode() not in files


class FakeProbe:
    def __init__(self, paths, inspect: dict) -> None:
        self.paths = paths
        self._inspect = inspect

    async def inspect(self) -> dict:
        return self._inspect


def test_the_safety_check_looks_for_the_github_token(lab, tmp_path):
    from datalab.safety import SafetyCheck
    from datalab.safety.canary import Canaries
    from datalab.sessions.containers import SessionPaths

    settings = lab.manager._settings
    synced(lab)
    tokens = [TOKEN, REFRESH]
    check = SafetyCheck(
        settings, SessionTokens(), Canaries(), model_key=lambda: "", github_tokens=lambda: tokens
    )
    paths = SessionPaths(tmp_path / "probe")
    (paths.root / "work").mkdir(parents=True)
    clean = {"Config": {"Env": ["DATALAB_SESSION_TOKEN=abc"], "Cmd": ["sleep"]}, "Mounts": []}

    def run(inspect: dict) -> tuple[str, str]:
        result = asyncio.run(check._no_github_token_in(FakeProbe(paths, inspect), kind="data"))
        return result.status, result.detail

    assert run(clean)[0] == "pass"
    leaked = {**clean, "Config": {"Env": [f"GH={TOKEN}"]}}
    assert run(leaked) == ("fail", "Found: in the container's environment or command")
    repos = settings.data_dir / "repos"
    assert "mounted" in run({**clean, "Mounts": [{"Source": str(repos / "ihs-knowledge")}]})[1]
    (paths.work / "notes.md").write_text(f"token {REFRESH}")
    assert "a file the container can see" in run(clean)[1]
    (paths.work / "notes.md").unlink()
    (repos / "ihs-knowledge" / "leak.txt").write_text(TOKEN)
    assert "plain text" in run(clean)[1]
    tokens.clear()
    skipped = asyncio.run(check._no_github_token_in(FakeProbe(paths, clean), kind="research"))
    assert (skipped.status, skipped.required) == ("skip", False)


def test_a_turn_seeds_the_copy_first_and_ends_with_the_agents_edit_proposed(lab):
    from tests.test_conversations import FakeRuntime

    synced(lab)
    made = lab.store.create(kind="research", mode="research", title="t", model="m")
    folder = kb_dir(lab, made.id)

    class EditingRuntime(FakeRuntime):
        async def send(self, text, *, effort=None):
            assert (folder / "sources" / "fitbit.md").exists()  # seeded before the turn
            (folder / "qc" / "wear-time.md").write_text(NEW_PAGE)
            return await super().send(text, effort=effort)

    def runtime(conversation):
        async def emit(kind, data):
            lab.store.append(conversation.id, kind, data)

        made_runtime = EditingRuntime(emit)
        lab.manager._runtimes[conversation.id] = made_runtime
        return made_runtime

    lab.manager._runtime = runtime  # type: ignore[method-assign]

    async def run_turn() -> None:
        await lab.manager.send(made, "Note the wear-time rule.", None)
        while lab.manager.is_busy(made.id):
            await asyncio.sleep(0.02)

    asyncio.run(run_turn())
    [event] = events(lab, made.id)
    assert [f["path"] for f in event["files"]] == ["qc/wear-time.md"]
    kinds = [e.type for e in lab.store.all_events_after(made.id, 0)]
    assert kinds.index("checkpoint") < kinds.index("kb_proposal") < kinds.index("turn_done")


def test_a_flood_of_refusals_is_one_line(lab):
    synced(lab)
    cid = conversation(lab)
    objects = {f".git/objects/{n:02x}": f"object {n}" for n in range(30)}
    proposal = turn(lab, cid, {**objects, "qc/wear-time.md": NEW_PAGE, "odd\nname.md": "x"})
    assert proposal is not None and [c.path for c in proposal.files] == ["qc/wear-time.md"]
    refused = {r.path: r.reason for r in proposal.refused}
    assert refused[".git/"].startswith("30 files: it isn't part of the knowledge base's layout")
    assert "odd\nname.md" in refused
