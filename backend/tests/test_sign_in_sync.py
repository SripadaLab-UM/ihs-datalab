"""The lab's shared DataLab syncs the lab repos when someone signs in, with
their own GitHub sign-in (repos/sign_in_sync.py), and the knowledge base's git
commands get that token (knowledge/service.py _fresh_token)."""

from __future__ import annotations

from datalab.auth.acting import acting_user
from datalab.auth.users import User
from datalab.knowledge.service import Knowledge
from datalab.repos import sign_in_sync
from datalab.repos.sign_in_sync import RepoSyncOnSignIn

ANA = User(2, "ana", admin=False)
BO = User(3, "bo", admin=False)


class Repo:
    def __init__(self, available=True, fails=False):
        self.available = available
        self.fails = fails
        self.synced_as: list[str] = []

    def sync(self):
        person = acting_user()
        self.synced_as.append(person.login if person else "nobody")
        if self.fails:
            raise RuntimeError("git clone failed")


def test_a_sign_in_syncs_each_repo_as_that_person_in_the_background():
    started = []
    knowledge, pipelines, off = Repo(fails=True), Repo(), Repo(available=False)
    hook = RepoSyncOnSignIn(knowledge, pipelines, off, start=started.append)
    hook(ANA)
    assert knowledge.synced_as == []  # nothing on the sign-in's own request
    [work] = started
    work()
    # One failing doesn't stop the next; one that isn't set up is left alone.
    assert (knowledge.synced_as, pipelines.synced_as, off.synced_as) == (["ana"], ["ana"], [])
    assert acting_user() is None  # the background acts for no one afterwards


def test_sign_ins_close_together_sync_once(monkeypatch):
    now = [100.0]
    started = []
    repo = Repo()
    hook = RepoSyncOnSignIn(repo, clock=lambda: now[0], start=started.append)
    hook(ANA)
    hook(BO)  # while the first runs
    assert len(started) == 1
    started[0]()
    now[0] += 60
    hook(BO)  # a minute later
    assert len(started) == 1
    now[0] += sign_in_sync.AGAIN_SECONDS
    hook(BO)
    started[-1]()
    assert repo.synced_as == ["ana", "bo"]


def test_the_knowledge_bases_git_commands_get_the_token():
    """On a server git has no credential helper with a sign-in in it: the
    token must come from here, as the pipelines repo's already did."""

    class Auth:
        def token_for_git(self):
            return "fresh-token"

    knowledge = Knowledge.__new__(Knowledge)
    knowledge.auth = Auth()  # type: ignore[assignment]
    assert knowledge._fresh_token() == "fresh-token"
