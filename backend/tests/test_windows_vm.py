"""datalab/windows_vm.py and its twin in installer/windows/install.ps1: putting
back the right Docker's virtual machine needs when a policy takes it away."""

from __future__ import annotations

import base64
import re
import subprocess
import sys
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
# taskkill /F /PID, and wsl --terminate for a distribution that isn't there,
# as they answered on the same laptop (2026-09-29).
TASKKILL_DONE = "SUCCESS: The process with PID {pid} has been terminated.\r\n"
TASKKILL_GONE = 'ERROR: The process "{pid}" not found.\r\n'
# taskkill /F /FI "IMAGENAME eq …" /FI "USERNAME eq …" /PID, for an id that has
# gone or now belongs to another program (exit 0, nothing ended).
TASKKILL_NO_MATCH = b"\r\nINFO: No tasks running with the specified criteria.\r\n"
TERMINATE_NOT_FOUND = (
    b"There is no distribution with the supplied name.\r\n"
    b"Error code: Wsl/Service/WSL_E_DISTRO_NOT_FOUND\r\n"
)
# wsl --terminate docker-desktop while it ran (2026-09-29, WSL 2.7.14, with WSL_UTF8).
TERMINATE_DONE = b"The operation completed successfully. \r\n"
USER = "UMHS\\tester"


class FakeWindows:
    """subprocess.run as DataLab uses it here: docker, wsl.exe, tasklist,
    taskkill and PowerShell, over a table of processes: Docker Desktop's own
    (this user's) and its service (SYSTEM's)."""

    def __init__(
        self,
        *,
        docker=False,
        wsl=None,
        grant=0,
        after_grant=None,
        desktop_open=True,
        ready_after_open=False,
        unkillable=(),
        linger=0,
        terminate=(0, TERMINATE_DONE),
    ):
        self.docker = docker
        self.wsl = wsl or (1, REFUSED.encode("utf-16-le"))
        self.grant_code = grant
        self.after_grant = after_grant  # what wsl.exe says once the grant has run
        self.ready_after_open = ready_after_open  # the engine answers once reopened
        self.unkillable = set(unkillable)  # programs taskkill can't end
        self.linger = linger  # listings a killed process still shows up in
        self.dying: dict[int, int] = {}
        self.terminate = terminate  # what `wsl --terminate docker-desktop` answers
        self.processes: dict[int, tuple[str, str]] = {7768: ("com.docker.service", "SYSTEM")}
        self._next_pid = 30000
        if desktop_open:
            self._open_docker_desktop()
        self.calls: list[list[str]] = []
        self.opened: list[list[str]] = []

    @property
    def desktop_open(self) -> bool:
        return any(image == "Docker Desktop.exe" for image, _ in self.processes.values())

    def _open_docker_desktop(self):
        for image in windows_vm.DOCKER_DESKTOP_IMAGES:
            self._next_pid += 1
            self.processes[self._next_pid] = (image, USER)

    def _tasklist(self, command):
        for pid in list(self.dying):  # a killed process that takes a moment to go
            self.dying[pid] -= 1
            if self.dying[pid] < 0:
                del self.dying[pid]
                self.processes.pop(pid, None)
        filters = [command[i + 1] for i, part in enumerate(command) if part == "/FI"]
        image = next(f[len("IMAGENAME eq ") :] for f in filters if f.startswith("IMAGENAME eq "))
        user = next(
            (f[len("USERNAME eq ") :] for f in filters if f.startswith("USERNAME eq ")), None
        )
        found = [
            (pid, name)
            for pid, (name, owner) in self.processes.items()
            if name.lower() == image.lower() and (user is None or owner == user)
        ]
        if not found:
            return TASKLIST_NONE
        if "/FO" in command:  # CSV, as own_pids asks
            rows = [f'"{name}","{pid}","Console","1","55,376 K"\r\n' for pid, name in found]
            return "".join(rows).encode()
        return TASKLIST_OPEN

    def run(self, command, **options):
        self.calls.append(command)
        name = Path(command[0]).name.lower()
        if name == "docker" and command[1:] == ["info"]:
            return subprocess.CompletedProcess(command, 0 if self.docker else 1, b"", b"")
        if name == "docker":
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if name == "wsl.exe" and "--terminate" in command:
            code, said = self.terminate
            return subprocess.CompletedProcess(command, code, said, b"")
        if name == "wsl.exe":
            code, said = self.wsl
            return subprocess.CompletedProcess(command, code, said, b"")
        if name == "tasklist.exe":
            return subprocess.CompletedProcess(command, 0, self._tasklist(command), b"")
        if name == "taskkill.exe":
            pid = int(command[command.index("/PID") + 1])
            filters = [command[i + 1] for i, part in enumerate(command) if part == "/FI"]
            if filters:
                image = next(
                    f[len("IMAGENAME eq ") :] for f in filters if f.startswith("IMAGENAME eq ")
                )
                user = next(
                    f[len("USERNAME eq ") :] for f in filters if f.startswith("USERNAME eq ")
                )
                found = self.processes.get(pid)
                if found is None or found[0].lower() != image.lower() or found[1] != user:
                    return subprocess.CompletedProcess(command, 0, TASKKILL_NO_MATCH, b"")
            elif pid not in self.processes:
                return subprocess.CompletedProcess(
                    command, 128, b"", TASKKILL_GONE.format(pid=pid).encode()
                )
            if self.processes[pid][0] not in self.unkillable:
                if self.linger:
                    self.dying[pid] = self.linger  # still listed for a few checks
                else:
                    del self.processes[pid]
            return subprocess.CompletedProcess(
                command, 0, TASKKILL_DONE.format(pid=pid).encode(), b""
            )
        if name == "powershell.exe":
            if self.grant_code == 0 and self.after_grant is not None:
                self.wsl = self.after_grant
            return subprocess.CompletedProcess(command, self.grant_code, b"", b"")
        raise AssertionError(f"unexpected command {command}")

    def popen(self, command, **options):
        self.opened.append(command)
        self._open_docker_desktop()
        if self.ready_after_open:
            self.docker = True

    def ran(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if Path(c[0]).name.lower() == name]

    def killed(self) -> list[int]:
        return [int(c[c.index("/PID") + 1]) for c in self.ran("taskkill.exe")]


class Clock:
    """A clock that sleeping moves on, so waits take no time in tests."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def doctor_for(fake: FakeWindows, *, platform="win32") -> windows_vm.DockerDoctor:
    clock = Clock()
    return windows_vm.DockerDoctor(
        run=fake.run,
        popen=fake.popen,
        platform=platform,
        clock=clock,
        sleep=clock.sleep,
        user=lambda: USER,
    )


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
    # A declined prompt must not look like success (next test, on Windows).
    assert starter.startswith("$ErrorActionPreference = 'Stop'; ")
    assert "if (-not $p) { exit 1 }" in starter


@pytest.mark.skipif(sys.platform != "win32", reason="runs Windows PowerShell")
def test_a_prompt_that_fails_to_start_the_fix_is_never_taken_for_success():
    """Found on the Michigan Medicine laptop (0.3.0b3): cancelling CyberArk
    EPM's administrator request made the starter exit 0, so DataLab said "That
    didn't fix it. Restart Windows" instead of "Windows didn't give
    permission". Start-Process fails the same way, with no prompt, for a
    program that isn't there."""
    command = windows_vm.elevated_command(program=r"C:\DataLab\no-such-program.exe")
    assert subprocess.run(command, capture_output=True, timeout=60).returncode == 1
    fake = FakeWindows()
    doctor = doctor_for(fake)

    def real_powershell(command, **options):
        if Path(command[0]).name.lower() == "powershell.exe":
            failing = windows_vm.elevated_command(program=r"C:\DataLab\no-such-program.exe")
            return subprocess.run(failing, capture_output=True, timeout=60)
        return fake.run(command, **options)

    doctor._run = real_powershell
    assert doctor.fix().outcome == "declined"


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
    assert doctor.state() == "unsupported" and doctor.fix().outcome == "unsupported"
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
    assert doctor_for(fake).fix().outcome == "not-needed"
    fake = FakeWindows(wsl=(0, b""))  # Docker Desktop starting, VM fine
    assert doctor_for(fake).fix().outcome == "not-needed"
    assert fake.ran("powershell.exe") == []


def test_the_fix_restarts_docker_desktop_afresh_and_waits_until_it_answers(docker_app):
    """`docker desktop restart` left the backend that had given up running
    (0.3.0b3, on the laptop): now its processes end, its VM stops, it reopens."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    stuck = sorted(pid for pid, (_, owner) in fake.processes.items() if owner == USER)
    doctor = doctor_for(fake)
    assert doctor.state() == "vm-refused"
    assert doctor.fix() == windows_vm.FixResult("fixed")
    assert len(fake.ran("powershell.exe")) == 1
    assert sorted(fake.killed()) == stuck  # each of Docker Desktop's own, by id
    assert fake.ran("wsl.exe")[-1][1:] == ["--terminate", "docker-desktop"]
    assert fake.opened == [[str(docker_app)]]
    assert doctor.state() == "ready"  # checked again, not the answer from before
    assert not doctor.fixing


def test_the_restart_ends_only_docker_desktops_own_processes_of_this_account():
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    fake.processes[4242] = ("Docker Desktop.exe", "UMHS\\someone-else")
    fake.processes[4343] = ("notepad.exe", USER)
    doctor_for(fake).fix()
    assert 7768 not in fake.killed()  # com.docker.service, SYSTEM's
    assert 4242 not in fake.killed() and 4343 not in fake.killed()
    for command in fake.ran("tasklist.exe"):
        if "/FO" in command:
            assert f"USERNAME eq {USER}" in command
            image = next(p for p in command if p.startswith("IMAGENAME eq "))[
                len("IMAGENAME eq ") :
            ]
            assert image in windows_vm.DOCKER_DESKTOP_IMAGES
    # Never all of WSL: only Docker's own distribution.
    assert not [c for c in fake.ran("wsl.exe") if "--shutdown" in c]


def test_processes_that_have_ended_meanwhile_are_fine():
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    answers: list[bytes] = []

    def ended_already(command, **options):
        if Path(command[0]).name.lower() == "taskkill.exe":
            pid = int(command[command.index("/PID") + 1])
            fake.processes.pop(pid, None)  # it ended by itself just before
            answer = fake.run(command, **options)
            answers.append(answer.stdout)
            return answer
        return fake.run(command, **options)

    doctor = doctor_for(fake)
    doctor._run = ended_already
    assert doctor.fix() == windows_vm.FixResult("fixed")
    assert answers and set(answers) == {TASKKILL_NO_MATCH}  # every time, and still fixed


def test_a_process_id_windows_gave_to_another_program_is_left_alone():
    """Between the listing and taskkill, a Docker Desktop process can end and
    Windows can hand its id to something else: the filters beside /PID keep
    taskkill to Docker Desktop's own programs of this account."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    real_run = fake.run

    def reused(command, **options):
        if Path(command[0]).name.lower() == "taskkill.exe":
            pid = int(command[command.index("/PID") + 1])
            assert f"USERNAME eq {USER}" in command
            fake.processes[pid] = ("notepad.exe", USER)  # someone else's now
        return real_run(command, **options)

    doctor = doctor_for(fake)
    doctor._run = reused
    doctor.fix()
    assert [name for name, _ in fake.processes.values()].count("notepad.exe") == 4


@pytest.mark.parametrize(("linger", "outcome"), [(5, "fixed"), (40, "restart-failed")])
def test_a_process_that_takes_a_moment_to_go_is_waited_for_a_while(linger, outcome):
    """taskkill /F answers before a process has quite gone: the restart checks
    again, once a second, for 15 seconds before calling it "stop"."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True, linger=linger)
    doctor = doctor_for(fake)
    result = doctor.fix()
    assert result.outcome == outcome
    if outcome == "restart-failed":
        assert result.failed_step == "stop" and fake.opened == []


def test_during_the_fix_the_page_sees_where_it_is_and_cant_open_docker_desktop():
    """Found in review: during the restart, a GET said "stopped" (Docker
    Desktop's processes are down on purpose), and Open Docker Desktop in
    between made the restart look as if Docker Desktop wouldn't close."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    doctor = doctor_for(fake)
    seen: dict[str, tuple] = {}
    real_run = fake.run

    def watching(command, **options):
        name = Path(command[0]).name.lower()
        if name == "powershell.exe":
            seen["prompt"] = (doctor.phase, doctor.fixing)
        if name == "wsl.exe" and "--terminate" in command:  # mid-restart, processes down
            seen["restarting"] = (doctor.phase, doctor.state(), doctor.check(), doctor.start())
        return real_run(command, **options)

    doctor._run = watching
    assert doctor.fix() == windows_vm.FixResult("fixed")
    assert seen["prompt"] == ("prompt", True)
    assert seen["restarting"] == ("restarting", "starting", "starting", "starting")
    assert len(fake.opened) == 1  # the restart's own, not a second from start()
    assert doctor.phase is None and not doctor.fixing


def test_a_docker_vm_that_isnt_there_counts_as_stopped():
    fake = FakeWindows(
        after_grant=(0, b""), ready_after_open=True, terminate=(127, TERMINATE_NOT_FOUND)
    )
    assert doctor_for(fake).fix() == windows_vm.FixResult("fixed")


@pytest.mark.parametrize(
    ("fake", "step"),
    [
        (FakeWindows(after_grant=(0, b""), unkillable={"com.docker.backend.exe"}), "stop"),
        (
            FakeWindows(after_grant=(0, b""), terminate=(1, b"Catastrophic failure\r\n")),
            "terminate",
        ),
        (FakeWindows(after_grant=(0, b""), ready_after_open=False), "ready"),
    ],
)
def test_the_fix_says_which_step_of_the_restart_didnt_work(fake, step):
    result = doctor_for(fake).fix()
    assert result == windows_vm.FixResult("restart-failed", step)
    if step == "stop":
        assert fake.opened == []  # nothing further once Docker Desktop won't close


def test_a_restart_that_never_gets_ready_gives_up_after_a_few_minutes():
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=False)
    doctor = doctor_for(fake)
    started = doctor._clock()
    assert doctor.fix().failed_step == "ready"
    waited = doctor._clock() - started
    assert 240 <= waited < 260  # bounded: no endless loop
    info = [c for c in fake.calls if c[:2] == ["docker", "info"]]
    assert len(info) < 60


def test_docker_desktop_not_installed_any_more_is_the_start_step(monkeypatch, tmp_path):
    fake = FakeWindows(after_grant=(0, b""))
    doctor = doctor_for(fake)
    assert doctor.state() == "vm-refused"
    monkeypatch.setattr(windows_vm, "docker_desktop", lambda: tmp_path / "gone.exe")
    assert doctor.fix() == windows_vm.FixResult("restart-failed", "start")


def test_an_account_windows_cant_name_stops_before_anything_is_ended():
    fake = FakeWindows(after_grant=(0, b""))
    doctor = doctor_for(fake)
    doctor._user = lambda: None
    assert doctor.fix() == windows_vm.FixResult("restart-failed", "stop")
    assert fake.killed() == []


def test_a_declined_prompt_or_a_fix_that_didnt_take_says_which():
    fake = FakeWindows(grant=1)  # the box was closed, or no administrator access
    assert doctor_for(fake).fix().outcome == "declined"
    assert fake.killed() == [] and fake.opened == []
    fake = FakeWindows(grant=0, after_grant=None)  # it ran, but Windows still refuses
    assert doctor_for(fake).fix().outcome == "still-refused"
    assert fake.killed() == []


def test_one_administrator_prompt_at_a_time():
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    doctor = doctor_for(fake)
    inner: list[str] = []

    def prompt_showing(command, **options):
        if Path(command[0]).name.lower() == "powershell.exe":
            assert doctor.fixing
            inner.append(doctor.fix().outcome)  # a second click while the box is up
        return fake.run(command, **options)

    doctor._run = prompt_showing
    assert doctor.fix().outcome == "fixed"
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


def installer_function(name: str) -> str:
    body = INSTALL[INSTALL.index(f"function {name}") :]
    return body[: body.index("\n}\n")]


def test_the_installer_closes_the_same_docker_desktop_programs_as_datalab():
    listed = re.search(r"^\$DockerDesktopImages = @\((.*)\)$", INSTALL, re.M)
    assert listed, "install.ps1 has no $DockerDesktopImages"
    assert tuple(re.findall(r'"([^"]+)"', listed.group(1))) == windows_vm.DOCKER_DESKTOP_IMAGES
    assert "com.docker.service" not in listed.group(1)


def test_the_installers_repair_restarts_an_open_docker_desktop_afresh():
    """The same restart as DataLab's (0.3.0b3 found `docker desktop restart`
    leaves a stuck backend): close Docker Desktop's own programs, stop its VM,
    and let Step 2 open it again; say "restart Windows" if it doesn't come back."""
    repair = installer_function("Repair-VmLogon")
    # Code only: the comments explain what isn't done.
    script = "\n".join(line for line in INSTALL.splitlines() if not line.lstrip().startswith("#"))
    assert "docker desktop restart" not in script.lower()
    assert '"desktop", "restart"' not in script
    grant = repair.index("-Verb RunAs")
    assert grant < repair.index("Stop-DockerDesktopProcesses") < repair.index("Stop-DockerVm")
    # Said before asking: fixing closes and reopens Docker Desktop.
    assert repair.index("closes and reopens Docker Desktop") < repair.index('Ask "Fix it now?"')
    listing = installer_function("Get-OwnPids")
    assert (
        "WindowsIdentity]::GetCurrent().Name" in listing
    )  # the token's account, not $Env:USERNAME
    assert '/FI `"USERNAME eq $me`"' in listing and "/FO CSV" in listing
    kill = installer_function("Stop-DockerDesktopProcesses")
    assert '/FI `"IMAGENAME eq $image`" /FI `"USERNAME eq $me`" /PID $id' in kill
    assert "-lt 15" in kill  # waits a while for processes to go, bounded
    vm = installer_function("Stop-DockerVm")
    assert '"--terminate docker-desktop"' in vm and "WSL_E_DISTRO_NOT_FOUND" in vm
    assert "--shutdown" not in script
    step2 = INSTALL[INSTALL.index('Step "Step 2 of 8') : INSTALL.index('Step "Step 3 of 8')]
    assert "$script:VmLogonRepaired" in step2 and "Restart Windows, then run the installer" in step2


def test_the_administrator_part_gives_the_right_too():
    admin = INSTALL[
        INSTALL.index("if ($Prepare) {") : INSTALL.index(
            "# The installer, run as the person installing."
        )
    ]
    assert "& ([scriptblock]::Create($VmLogonGrant))" in admin
