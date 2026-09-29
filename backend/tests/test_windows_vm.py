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
# What wsl.exe said on the Michigan Medicine laptop once the policy had run
# (2026-09-28, WSL 2.7.14), and what tasklist lists (Windows 11 26100).
REFUSED = (
    "Logon failure: the user has not been granted the requested logon type at this computer. \r\n"
    "\r\n"
    "Error code: Wsl/Service/CreateInstance/CreateVm/HCS/0x80070569\r\n"
)
TASKLIST_OPEN = (
    b"\r\nDocker Desktop.exe            30500 Console                    2     55,376 K\r\n"
    b"Docker Desktop.exe            18580 Console                    2     98,464 K\r\n"
)
TASKLIST_NONE = b"INFO: No tasks are running which match the specified criteria.\r\n"


class FakeWindows:
    """subprocess.run as DataLab uses it here: docker, wsl.exe and PowerShell."""

    def __init__(self, *, docker=False, wsl=None, grant=0, after_grant=None, desktop_open=True):
        self.docker = docker
        self.wsl = wsl or (1, REFUSED.encode("utf-16-le"))
        self.grant_code = grant
        self.after_grant = after_grant  # what wsl.exe says once the grant has run
        self.desktop_open = desktop_open  # Docker Desktop's app, per tasklist
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
        if name == "tasklist.exe":
            listed = TASKLIST_OPEN if self.desktop_open else TASKLIST_NONE
            return subprocess.CompletedProcess(command, 0, listed, b"")
        if name == "powershell.exe":
            if self.grant_code == 0 and self.after_grant is not None:
                self.wsl = self.after_grant
            return subprocess.CompletedProcess(command, self.grant_code, b"", b"")
        raise AssertionError(f"unexpected command {command}")

    def popen(self, command, **options):
        self.opened.append(command)
        self.desktop_open = True

    def ran(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if Path(c[0]).name.lower() == name]


def doctor_for(fake: FakeWindows, *, platform="win32") -> windows_vm.DockerDoctor:
    return windows_vm.DockerDoctor(run=fake.run, popen=fake.popen, platform=platform)


@pytest.fixture(autouse=True)
def installed(docker_app):
    """Docker Desktop is installed, unless a test says otherwise."""
    return docker_app


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


@pytest.mark.parametrize(
    ("fake", "state"),
    [
        (FakeWindows(docker=True), "ready"),
        (FakeWindows(), "vm-refused"),
        (FakeWindows(wsl=(0, b""), desktop_open=True), "starting"),
        (FakeWindows(wsl=(0, b""), desktop_open=False), "stopped"),
        (FakeWindows(wsl=(1, b"")), "unknown"),
    ],
)
def test_how_docker_stands(fake, state):
    assert windows_vm.docker_state(fake.run, "win32") == state
    if state in ("ready", "stopped"):
        # A working Docker, or a closed Docker Desktop, never starts WSL's VM.
        assert fake.ran("wsl.exe") == []


def test_docker_desktop_not_where_its_installer_puts_it(monkeypatch, tmp_path):
    monkeypatch.setattr(windows_vm, "docker_desktop", lambda: tmp_path / "missing.exe")
    fake = FakeWindows()
    assert windows_vm.docker_state(fake.run, "win32") == "not-installed"
    assert doctor_for(fake).start() == "not-installed" and fake.opened == []
    # A Docker that answers counts, wherever it's installed.
    assert windows_vm.docker_state(FakeWindows(docker=True).run, "win32") == "ready"


def test_nothing_is_checked_off_windows():
    fake = FakeWindows()
    doctor = doctor_for(fake, platform="darwin")
    assert doctor.state() == "unsupported" and doctor.fix() == "unsupported"
    assert fake.calls == []


def clocked(fake: FakeWindows) -> tuple[windows_vm.DockerDoctor, list[float]]:
    now = [0.0]
    doctor = windows_vm.DockerDoctor(
        run=fake.run, popen=fake.popen, platform="win32", clock=lambda: now[0]
    )
    return doctor, now


def test_an_answer_is_kept_for_a_few_seconds_unless_asked_fresh():
    fake = FakeWindows()
    doctor, _ = clocked(fake)
    assert doctor.state() == "vm-refused"
    fake.wsl = (0, b"")
    assert doctor.state() == "vm-refused"  # the page's polling doesn't start a VM each time
    assert doctor.state(fresh=True) == "starting"
    assert len(fake.ran("wsl.exe")) == 2


def test_once_the_vm_starts_it_isnt_tried_again_for_a_few_minutes():
    """While Docker Desktop starts (its engine not answering yet), the page asks
    every half minute: WSL's VM is tried once, not at every check."""
    fake = FakeWindows(wsl=(0, b""))
    doctor, now = clocked(fake)
    assert doctor.state() == "starting"
    for seconds in range(30, 300, 30):
        now[0] = seconds
        assert doctor.state() == "starting"
    assert len(fake.ran("wsl.exe")) == 1
    # Then again, in case the policy has run meanwhile.
    fake.wsl = (1, REFUSED.encode("utf-16-le"))
    now[0] = windows_vm.DockerDoctor.VM_OK_SECONDS + 1
    assert doctor.state() == "vm-refused"


def test_check_again_runs_at_most_every_few_seconds():
    fake = FakeWindows()
    doctor, now = clocked(fake)
    doctor.check()
    doctor.check()
    assert len(fake.ran("wsl.exe")) == 1
    now[0] = windows_vm.DockerDoctor.MIN_CHECK_SECONDS + 1
    doctor.check()
    assert len(fake.ran("wsl.exe")) == 2


def test_while_a_check_runs_others_get_the_last_answer_instead_of_waiting():
    fake = FakeWindows()
    doctor = doctor_for(fake)
    assert doctor.state() == "vm-refused"
    meanwhile: list[str] = []

    def slow(command, **options):
        if Path(command[0]).name.lower() == "wsl.exe":
            meanwhile.append(doctor.state(fresh=True))  # another tab, mid-check
        return fake.run(command, **options)

    doctor._run = slow
    assert doctor.state(fresh=True) == "vm-refused"
    assert meanwhile == ["vm-refused"]


def test_the_fix_is_refused_unless_the_vm_is():
    fake = FakeWindows(docker=True)
    assert doctor_for(fake).fix() == "not-needed"
    fake = FakeWindows(wsl=(0, b""))  # Docker Desktop starting, VM fine
    assert doctor_for(fake).fix() == "not-needed"
    assert fake.ran("powershell.exe") == []


def test_the_fix_restarts_docker_desktop_once_windows_lets_the_vm_start():
    fake = FakeWindows(after_grant=(0, b""))
    doctor = doctor_for(fake)
    assert doctor.state() == "vm-refused"
    assert doctor.fix() == "fixed"
    assert len(fake.ran("powershell.exe")) == 1
    assert ["docker", "desktop", "restart", "--detach"] in fake.calls
    assert doctor.state() == "starting"  # checked again, not the answer from before
    assert not doctor.fixing


def test_a_declined_prompt_or_a_fix_that_didnt_take_says_which():
    fake = FakeWindows(grant=1)  # the box was closed, or no administrator access
    assert doctor_for(fake).fix() == "declined"
    assert not [c for c in fake.calls if c[:2] == ["docker", "desktop"]]
    fake = FakeWindows(grant=0, after_grant=None)  # it ran, but Windows still refuses
    assert doctor_for(fake).fix() == "still-refused"


def test_one_administrator_prompt_at_a_time():
    fake = FakeWindows(after_grant=(0, b""))
    doctor = doctor_for(fake)
    inner: list[str] = []

    def prompt_showing(command, **options):
        if Path(command[0]).name.lower() == "powershell.exe":
            assert doctor.fixing
            inner.append(doctor.fix())  # a second click while the box is up
        return fake.run(command, **options)

    doctor._run = prompt_showing
    assert doctor.fix() == "fixed"
    assert inner == ["busy"]


def test_start_opens_docker_desktop_only_when_its_closed(docker_app):
    fake = FakeWindows(wsl=(0, b""), desktop_open=False)
    assert doctor_for(fake).start() == "starting"
    assert fake.opened == [[str(docker_app)]]
    fake = FakeWindows(wsl=(0, b""), desktop_open=True)
    assert doctor_for(fake).start() == "starting"
    assert fake.opened == []
    fake = FakeWindows()  # the VM may not start: opening Docker wouldn't help
    assert doctor_for(fake).start() == "vm-refused"
    assert fake.opened == []


def test_at_start_a_closed_docker_desktop_is_opened_and_says_so(docker_app):
    fake = FakeWindows(wsl=(0, b""), desktop_open=False)
    said: list[str] = []
    assert windows_vm.check_before_serve(doctor_for(fake), say=said.append) == "starting"
    assert fake.opened == [[str(docker_app)]]
    assert "Opening Docker Desktop" in "\n".join(said)


def test_at_start_a_refused_vm_points_to_the_page_and_never_prompts():
    """The fix needs administrator access the person may still have to turn
    on, so DataLab's window never asks: the page's button waits for them."""
    fake = FakeWindows()
    said: list[str] = []
    assert windows_vm.check_before_serve(doctor_for(fake), say=said.append) == "vm-refused"
    text = "\n".join(said)
    assert "temporary administrator access" in text and "Fix it" in text
    assert fake.ran("powershell.exe") == []
    fake = FakeWindows(docker=True)
    said.clear()
    assert windows_vm.check_before_serve(doctor_for(fake), say=said.append) == "ready"
    assert said == []


def test_docker_desktop_is_started_if_it_cant_be_restarted(docker_app):
    opened = []

    def no_cli(command, **options):
        raise FileNotFoundError(command[0])

    windows_vm.restart_docker(no_cli, lambda command, **options: opened.append(command))
    assert opened == [[str(docker_app)]]


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
