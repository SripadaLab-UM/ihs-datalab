"""datalab/windows_vm.py and its twin in installer/windows/install.ps1: putting
back the right Docker's virtual machine needs when a policy takes it away."""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path

import pytest

from datalab import windows_vm

INSTALL = (Path(__file__).resolve().parents[2] / "installer" / "windows" / "install.ps1").read_text(
    encoding="utf-8"
)
REFUSED = (
    "The user has not been granted the requested logon type at this computer.\r\n"
    "Error code: Wsl/Service/CreateInstance/CreateVm/HCS/0x80070569\r\n"
)


class FakeWindows:
    """subprocess.run as DataLab uses it here: docker, wsl.exe and PowerShell."""

    def __init__(self, *, docker=False, wsl=None, grant=0, after_grant=None):
        self.docker = docker
        self.wsl = wsl or (1, REFUSED.encode("utf-16-le"))
        self.grant_code = grant
        self.after_grant = after_grant  # what wsl.exe says once the grant has run
        self.calls: list[list[str]] = []
        self.opened: list[list[str]] = []

    def run(self, command, **options):
        self.calls.append(command)
        name = Path(command[0]).name.lower()
        if name == "docker" and command[1:] == ["info"]:
            return subprocess.CompletedProcess(command, 0 if self.docker else 1, b"", b"")
        if name == "docker":
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if name == "wsl.exe":
            code, said = self.wsl
            return subprocess.CompletedProcess(command, code, said, b"")
        if name == "powershell.exe":
            if self.grant_code == 0 and self.after_grant is not None:
                self.wsl = self.after_grant
            return subprocess.CompletedProcess(command, self.grant_code, b"", b"")
        raise AssertionError(f"unexpected command {command}")

    def popen(self, command, **options):
        self.opened.append(command)

    def ran(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if Path(c[0]).name.lower() == name]


def serve_check(fake: FakeWindows, *, answer="y", interactive=True):
    said: list[str] = []
    outcome = windows_vm.check_before_serve(
        ask=lambda prompt: answer,
        say=said.append,
        interactive=interactive,
        run=fake.run,
        popen=fake.popen,
        platform="win32",
    )
    return outcome, "\n".join(said)


# ------------------------------------------------------------ the fix itself


def installer_grant() -> str:
    match = re.search(r"^\$VmLogonGrant = @'\n(.*?)\n'@$", INSTALL, re.S | re.M)
    assert match, "install.ps1 has no $VmLogonGrant here-string"
    return match.group(1)


def test_the_installer_and_datalab_run_the_same_fix():
    assert installer_grant() == windows_vm.GRANT_SCRIPT.strip("\n")
    assert f'$VmLogonRefused = "{windows_vm.LOGON_REFUSED}"' in INSTALL


def test_the_fix_adds_one_right_for_the_virtual_machines_and_nothing_else():
    script = windows_vm.GRANT_SCRIPT
    assert "$Sid = 'S-1-5-83-0'" in script and "$Right = 'SeServiceLogonRight'" in script
    assert script.count("LsaAddAccountRights(") == 1
    # Never removes or replaces rights, never compiles into a folder the person can change.
    for never in (
        "LsaRemoveAccountRights",
        "secedit",
        "Add-Type",
        "$env:",
        "Get-Content",
        "Import-Module",
    ):
        assert never.lower() not in script.lower()
    # Each Windows call reports its status instead of throwing it as an HRESULT.
    assert "PreserveSig" in script


def test_the_administrator_prompt_runs_only_the_fixed_text():
    command = windows_vm.elevated_command()
    # Joined with the running system's separator: "\\" on Windows, "/" on CI's Linux.
    program = command[0].lower().replace("/", "\\")
    assert program.endswith(r"system32\windowspowershell\v1.0\powershell.exe")
    starter = command[-1]
    assert "-Verb RunAs" in starter and "exit $p.ExitCode" in starter
    encoded = re.search(r"-EncodedCommand ([A-Za-z0-9+/=]+)", starter).group(1)
    assert base64.b64decode(encoded).decode("utf-16-le") == windows_vm.GRANT_SCRIPT


# ------------------------------------------------------------ finding out


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        (REFUSED.encode("utf-16-le"), REFUSED),
        (REFUSED.encode("utf-8"), REFUSED),
        (b"", ""),
    ],
)
def test_wsl_output_is_read_in_either_encoding(raw, text):
    assert windows_vm.wsl_text(raw) == text


@pytest.mark.parametrize(
    ("wsl", "found"),
    [
        ((1, REFUSED.encode("utf-16-le")), "refused"),
        ((1, REFUSED.encode("utf-8")), "refused"),
        ((0, b""), "ok"),
        ((1, "There is no distribution with the supplied name.".encode("utf-16-le")), "unknown"),
    ],
)
def test_the_probe_starts_wsls_own_system_distribution_never_dockers(wsl, found):
    fake = FakeWindows(wsl=wsl)
    assert windows_vm.probe(fake.run) == found
    (command,) = fake.ran("wsl.exe")
    assert command[1:] == ["--system", "-e", "true"]
    assert "docker-desktop" not in command


def test_a_wsl_that_hangs_or_is_missing_is_unknown_not_refused():
    def hangs(command, **options):
        raise subprocess.TimeoutExpired(command, 90)

    def missing(command, **options):
        raise FileNotFoundError(command[0])

    assert windows_vm.probe(hangs) == "unknown"
    assert windows_vm.probe(missing) == "unknown"
    assert windows_vm.docker_answers(hangs) is False


# ------------------------------------------------------------ at DataLab's start


def test_nothing_is_checked_off_windows_or_while_docker_answers():
    fake = FakeWindows()
    assert windows_vm.check_before_serve(run=fake.run, platform="darwin") == "not-windows"
    assert fake.calls == []
    fake = FakeWindows(docker=True)
    assert serve_check(fake)[0] == "docker-answers"
    assert fake.ran("wsl.exe") == []


def test_a_virtual_machine_that_starts_means_docker_is_just_not_running_yet():
    fake = FakeWindows(wsl=(0, b""))
    outcome, said = serve_check(fake)
    assert outcome == "vm-starts" and said == ""
    assert fake.ran("powershell.exe") == []


def test_accepting_fixes_it_and_restarts_docker_desktop():
    fake = FakeWindows(after_grant=(0, b""))
    outcome, said = serve_check(fake)
    assert outcome == "fixed"
    assert "Windows won't let its virtual" in said and "Restart Windows" in said
    assert len(fake.ran("powershell.exe")) == 1
    assert ["docker", "desktop", "restart", "--detach"] in fake.calls
    assert "Docker Desktop is restarting" in said


def test_declining_or_no_console_changes_nothing_and_says_restart_windows():
    fake = FakeWindows()
    outcome, said = serve_check(fake, answer="n")
    assert outcome == "declined" and "Restart Windows" in said
    assert fake.ran("powershell.exe") == []
    fake = FakeWindows()
    outcome, said = serve_check(fake, interactive=False)
    assert outcome == "told" and "Start menu" in said
    assert fake.ran("powershell.exe") == []


def test_a_declined_prompt_or_a_fix_that_didnt_take_says_so():
    fake = FakeWindows(grant=1)  # the box was closed
    outcome, said = serve_check(fake)
    assert outcome == "failed" and "didn't work" in said
    assert not [c for c in fake.calls if c[:2] == ["docker", "desktop"]]
    fake = FakeWindows(grant=0, after_grant=None)  # granted, but still refused
    assert serve_check(fake)[0] == "failed"


def test_docker_desktop_is_started_if_it_cant_be_restarted(monkeypatch, tmp_path):
    app = tmp_path / "Docker Desktop.exe"
    app.write_bytes(b"")
    monkeypatch.setattr(windows_vm, "docker_desktop", lambda: app)
    opened = []

    def no_cli(command, **options):
        raise FileNotFoundError(command[0])

    windows_vm.restart_docker(no_cli, lambda command, **options: opened.append(command))
    assert opened == [[str(app)]]


# ------------------------------------------------------------ the installer


def test_the_installer_checks_before_starting_docker_and_fixes_only_on_yes():
    step2 = INSTALL[INSTALL.index('Step "Step 2 of 8') : INSTALL.index('Step "Step 3 of 8')]
    assert step2.index("if (Test-VmLogonRefused) { Repair-VmLogon }") < step2.index(
        "Start-Process $DockerDesktop"
    )
    repair = INSTALL[INSTALL.index("function Repair-VmLogon") :]
    repair = repair[: repair.index("\n}\n")]
    assert repair.index('Ask "Fix it now?"') < repair.index("-Verb RunAs")
    assert "-EncodedCommand $encoded" in repair and "$VmLogonGrant" in repair
    probe = INSTALL[INSTALL.index("function Test-VmLogonRefused") :]
    assert '"--system", "-e", "true"' in probe[: probe.index("\n}\n")]


def test_the_administrator_part_gives_the_right_too():
    admin = INSTALL[
        INSTALL.index("if ($Prepare) {") : INSTALL.index(
            "# The installer, run as the person installing."
        )
    ]
    assert "& ([scriptblock]::Create($VmLogonGrant))" in admin
