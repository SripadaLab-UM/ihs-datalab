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
    monkeypatch.setattr(git_module, "git_executable", lambda: "git")  # not found: no Mac stub

    def run(command, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(git_module.subprocess, "run", run)
    monkeypatch.setattr(git_module, "install_git_for_windows", lambda: False)
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    with pytest.raises(git_module.GitError, match=r"git-scm\.com"):
        clone.git("status", cwd=tmp_path)


def test_the_signature_check_reads_its_values_from_the_environment() -> None:
    # `powershell -Command` doesn't fill $args, so the check must not use it.
    assert "$args" not in git_module._SIGNATURE_CHECK
    assert "DATALAB_CHECK_FILE" in git_module._SIGNATURE_CHECK
    assert "DATALAB_CHECK_PUBLISHER" in git_module._SIGNATURE_CHECK


def test_the_reason_it_could_not_install_is_in_the_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_module, "git_executable", lambda: "git")  # not found: no Mac stub

    def run(command, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(git_module.subprocess, "run", run)
    monkeypatch.setattr(git_module, "install_git_for_windows", lambda: False)
    monkeypatch.setattr(git_module, "git_install_failure", "the download from github.com failed")
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    with pytest.raises(git_module.GitError, match=r"download from github\.com failed"):
        clone.git("status", cwd=tmp_path)


def _mac(monkeypatch: pytest.MonkeyPatch, tools_installed: bool) -> list[list[str]]:
    import subprocess

    ran: list[list[str]] = []

    def run(command, **kwargs):
        ran.append(command)
        code = 0 if command[:2] == ["xcode-select", "-p"] and tools_installed else 1
        if command[:2] == ["xcode-select", "--install"]:
            code = 0
        return subprocess.CompletedProcess(command, code, b"", b"")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(git_module.subprocess, "run", run)
    monkeypatch.setattr(git_module, "git_executable", lambda: "/usr/bin/git")
    return ran


def test_a_mac_without_command_line_tools_opens_apples_installer_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _mac(monkeypatch, tools_installed=False)
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    with pytest.raises(git_module.GitError, match=r"press Install"):
        clone.git("status", cwd=tmp_path)
    assert ["xcode-select", "--install"] in ran
    assert not any(command[0] == "/usr/bin/git" for command in ran)  # the stub never runs


def test_a_mac_with_command_line_tools_runs_git_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _mac(monkeypatch, tools_installed=True)
    clone = git_module.Clone(tmp_path, "https://example.invalid/x.git")
    assert clone.git("status", cwd=tmp_path, check=False).returncode == 1  # the fake's answer
    assert ["xcode-select", "--install"] not in ran
    assert any(command[0] == "/usr/bin/git" for command in ran)


def test_another_git_on_a_mac_is_used_without_asking_for_the_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = _mac(monkeypatch, tools_installed=False)
    monkeypatch.setattr(git_module, "git_executable", lambda: "/opt/homebrew/bin/git")
    assert git_module.mac_tools_missing() is False
    assert ran == []


def test_it_asks_for_no_mac_tools_off_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert git_module.mac_tools_missing() is False


def test_a_mac_where_xcode_select_cannot_run_does_not_claim_the_tools_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = _mac(monkeypatch, tools_installed=False)

    def run(command, **kwargs):
        ran.append(command)
        raise FileNotFoundError

    monkeypatch.setattr(git_module.subprocess, "run", run)
    assert git_module.mac_tools_missing() is False
    assert ["xcode-select", "--install"] not in ran
