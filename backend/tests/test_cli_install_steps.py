"""The commands the installers call after installing: `datalab github sign-in`,
`datalab repos sync`, and `datalab versions`."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from datalab import cli
from datalab.repos import commands
from datalab.repos.github import GitHubAuth
from tests.test_github_auth import CLIENT_ID, Clock, FakeGitHub

LAB_REPOS = (
    "[repos]\n"
    'knowledge = "SripadaLab-UM/ihs-knowledge"\n'
    'pipelines = "SripadaLab-UM/ihs-pipelines"\n'
    f'client_id = "{CLIENT_ID}"\n'
    'access_contact = "Ali, the DataLab maintainer"\n'
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("DATALAB_DATA_DIR", str(folder))
    monkeypatch.setenv("DATALAB_PROFILE", "real")
    return folder


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def auth(github, clock, monkeypatch) -> GitHubAuth:
    http = httpx.Client(transport=httpx.MockTransport(github.handler))
    made = GitHubAuth(CLIENT_ID, http=http, clock=clock)
    monkeypatch.setattr(cli, "_github_auth", lambda settings: made)
    # The sign-in waits between polls: on the test's clock, not for real.
    monkeypatch.setattr(
        commands, "_sleep", lambda seconds: setattr(clock, "now", clock.now + seconds)
    )
    monkeypatch.setattr(commands, "_open_page", lambda url: True)
    return made


def test_practice_skips_the_github_steps(data_dir, monkeypatch, capsys):
    monkeypatch.setenv("DATALAB_PROFILE", "practice")
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    assert cli.main(["github", "sign-in"]) == commands.SKIPPED
    assert "Practice DataLab doesn't use the lab's repositories" in capsys.readouterr().out
    assert cli.main(["repos", "sync"]) == commands.SKIPPED


def test_without_the_labs_settings_the_steps_are_skipped(data_dir, capsys):
    assert cli.main(["github", "sign-in"]) == commands.SKIPPED
    assert "Neither of the lab's repositories is set" in capsys.readouterr().out


def test_signing_in_in_the_terminal(data_dir, auth, github, capsys):
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    github.polls = [{"error": "authorization_pending"}, "ok"]
    # FakeGitHub knows ihs-knowledge; ihs-pipelines answers 404: no access.
    assert cli.main(["github", "sign-in", "--no-browser"]) == commands.FAILED
    out = capsys.readouterr().out
    assert "open https://github.com/login/device and enter the code ABCD-1234" in out
    assert "Signed in to GitHub as @yfang." in out
    assert "can't open SripadaLab-UM/ihs-pipelines" in out
    assert "Ask Ali, the DataLab maintainer to add you" in out
    assert "then run: datalab repos sync" in out
    assert "ghu_access" not in out  # never a token
    assert auth.signed_in()


def test_signing_in_when_already_signed_in(data_dir, auth, github, capsys):
    (data_dir / "settings.toml").write_text(LAB_REPOS.replace("pipelines = ", "# pipelines = "))
    github.polls = ["ok"]
    assert cli.main(["github", "sign-in", "--no-browser"]) == commands.DONE
    capsys.readouterr()
    assert cli.main(["github", "sign-in"]) == commands.DONE
    assert "Already signed in to GitHub as @yfang." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("answer", "said"),
    [({"error": "access_denied"}, "cancelled on GitHub"), ({"error": "expired_token"}, "expired")],
)
def test_a_sign_in_that_doesnt_finish_says_why(data_dir, auth, github, capsys, answer, said):
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    github.polls = [answer]
    assert cli.main(["github", "sign-in", "--no-browser"]) == commands.FAILED
    assert said in capsys.readouterr().out


def test_github_being_down_during_sign_in_is_a_message(data_dir, auth, github, capsys):
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    github.down = True
    assert cli.main(["github", "sign-in", "--no-browser"]) == commands.FAILED
    assert "couldn't be reached" in capsys.readouterr().out


def test_sync_needs_the_sign_in_first(data_dir, auth, capsys):
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    assert cli.main(["repos", "sync"]) == commands.FAILED
    assert "datalab github sign-in" in capsys.readouterr().out


class FakeRepoSync:
    made: ClassVar[list[str]] = []
    failing: ClassVar[set[str]] = set()

    def __init__(self, key, repo, data_dir, auth, state, *, contact=None) -> None:
        self.key, self.repo, self.contact = key, repo, contact
        FakeRepoSync.made.append(repo)

    def sync(self):
        return None if self.repo in self.failing else "0123456789abcdef"

    def status(self):
        return {"message": f"GitHub says your account can't open {self.repo}. Ask {self.contact}."}


def test_sync_clones_both_lab_repos_and_says_whom_to_ask(
    data_dir, auth, github, monkeypatch, capsys
):
    from datalab.repos import sync

    (data_dir / "settings.toml").write_text(LAB_REPOS)
    github.polls = ["ok"]
    cli.main(["github", "sign-in", "--no-browser"])
    capsys.readouterr()
    FakeRepoSync.made, FakeRepoSync.failing = [], {"SripadaLab-UM/ihs-pipelines"}
    monkeypatch.setattr(sync, "RepoSync", FakeRepoSync)
    assert cli.main(["repos", "sync"]) == commands.FAILED
    out = capsys.readouterr().out
    assert FakeRepoSync.made == ["SripadaLab-UM/ihs-knowledge", "SripadaLab-UM/ihs-pipelines"]
    assert "SripadaLab-UM/ihs-knowledge is up to date (0123456789ab)." in out
    assert "Ask Ali, the DataLab maintainer." in out
    FakeRepoSync.failing = set()
    assert cli.main(["repos", "sync"]) == commands.DONE


# ------------------------------------------------------------------ versions


@pytest.fixture
def install_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "app"
    for version in ("0.1.0a2", "0.1.0a3"):
        (root / "versions" / version).mkdir(parents=True)
        (root / "versions" / version / ".complete").write_text("{}")
    (root / "current").write_text("0.1.0a3\n")
    (root / "previous").write_text("0.1.0a2\n")
    monkeypatch.setenv("DATALAB_INSTALL_DIR", str(root))
    return root


def test_versions_lists_what_is_installed_side_by_side(data_dir, install_root, capsys):
    assert cli.main(["versions"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("0.1.0a3") and "opens from the launcher" in lines[0]
    assert lines[1].startswith("0.1.0a2") and "the one before" in lines[1]


def test_going_back_to_the_previous_version(data_dir, install_root, capsys):
    assert cli.main(["versions", "--use", "v0.1.0-alpha.2"]) == 0
    assert (install_root / "current").read_text().strip() == "0.1.0a2"
    assert (install_root / "previous").read_text().strip() == "0.1.0a3"
    assert "datalab rollback" in capsys.readouterr().out
    assert cli.main(["versions", "--use", "0.0.1"]) == 1


def test_signing_in_as_root_is_refused(data_dir, auth, github, monkeypatch, capsys):
    (data_dir / "settings.toml").write_text(LAB_REPOS)
    monkeypatch.setattr(commands.os, "geteuid", lambda: 0, raising=False)
    assert cli.main(["github", "sign-in", "--no-browser"]) == commands.FAILED
    assert "not with sudo" in capsys.readouterr().out
    assert not auth.signed_in() and github.requests == []


def test_versions_use_waits_for_an_unfinished_update(data_dir, install_root, capsys):
    from datalab import updates

    updates.begin(
        data_dir, data_dir / "datalab.sqlite", from_version="0.1.0a2", to_version="0.1.0a3"
    )
    assert cli.main(["versions", "--use", "0.1.0a2"]) == 1
    assert "hasn't finished" in capsys.readouterr().out
    assert (install_root / "current").read_text().strip() == "0.1.0a3"
