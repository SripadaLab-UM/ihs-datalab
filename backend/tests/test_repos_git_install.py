"""Git for Windows, installed by DataLab itself for a DataLab older than that step."""

import re
import sys
from pathlib import Path

import pytest

from datalab.repos import git as git_module

INSTALL = (Path(__file__).resolve().parents[2] / "installer" / "windows" / "install.ps1").read_text(
    encoding="utf-8"
)


def test_the_pinned_git_matches_the_installers() -> None:
    assert git_module.GIT_FOR_WINDOWS_URL in INSTALL
    assert git_module.GIT_FOR_WINDOWS_SHA256 in INSTALL
    assert f'$GitPublisher = "{git_module.GIT_FOR_WINDOWS_PUBLISHER}"' in INSTALL
    assert re.search(r"v\d+\.\d+\.\d+\.windows\.\d+", git_module.GIT_FOR_WINDOWS_URL)


def test_it_installs_nothing_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert git_module.install_git_for_windows() is False


def test_a_missing_git_is_installed_once_and_the_command_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def run(command, **kwargs):
        calls.append(command[0])
        if command[0] == "git":
            raise FileNotFoundError
        import subprocess

        return subprocess.CompletedProcess(command, 0, b"ok", b"")

    monkeypatch.setattr(git_module.subprocess, "run", run)
    monkeypatch.setattr(git_module, "install_git_for_windows", lambda: True)
    monkeypatch.setattr(git_module, "git_executable", lambda: "C:/Git/cmd/git.exe")
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    assert clone.git("status", cwd=tmp_path).stdout == b"ok"
    assert calls[-1] == "C:/Git/cmd/git.exe"


def test_when_it_cannot_install_it_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(git_module.subprocess, "run", run)
    monkeypatch.setattr(git_module, "install_git_for_windows", lambda: False)
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    with pytest.raises(git_module.GitError, match=r"git-scm\.com"):
        clone.git("status", cwd=tmp_path)
