"""Windows: putting back the right Docker's Linux virtual machine needs to start.

WSL 2 (and so Docker Desktop) runs a Hyper-V virtual machine, which signs in
as `NT VIRTUAL MACHINE\\Virtual Machines` (S-1-5-83-0) and needs the "Log on
as a service" right (SeServiceLogonRight). Hyper-V adds it when Windows
starts. A domain group policy that sets "Log on as a service" to its own list
takes it away again whenever Windows re-applies security policy (on the
Michigan Medicine network, "CoreOne-Security"). From then on no WSL virtual
machine can start: `wsl.exe` fails with Wsl/Service/CreateInstance/CreateVm/
HCS/0x80070569 ("the user has not been granted the requested logon type"),
and Docker Desktop waits for its engine for ever, saying nothing.

Restarting Windows puts the right back, until the policy runs again. So does
GRANT_SCRIPT, behind one administrator prompt: it adds that one right for that
one account and changes nothing else. The installer runs the same text
(installer/windows/install.ps1, `$VmLogonGrant`); tests/test_windows_vm.py
checks the two copies are the same.

Asking Windows whether the right is there needs an administrator, so DataLab
finds out by starting WSL's own small system distribution (`wsl.exe
--system`), never Docker's, and only when Docker isn't answering.

DataLab's page shows how Docker stands and offers the fix as a button
(api/docker.py, DockerDoctor): on a Michigan Medicine computer the person has
to turn on their temporary administrator access first, which a question
asked once in DataLab's window couldn't wait for.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import io
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# HRESULT_FROM_WIN32(ERROR_LOGON_TYPE_NOT_GRANTED), as wsl.exe prints it.
LOGON_REFUSED = "0x80070569"

# The fixed text the administrator prompt runs: nothing in it comes from the
# person, a file, or the environment. It calls LsaAddAccountRights through
# methods defined in memory, not with Add-Type, which compiles into the
# person's own TEMP folder, where another program could swap what the
# administrator then loads. Exit code 0 once the right is there.
GRANT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$Sid = 'S-1-5-83-0'
$Right = 'SeServiceLogonRight'
$Marshal = [Runtime.InteropServices.Marshal]
$assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly(
    (New-Object Reflection.AssemblyName 'DataLabVmLogonRight'),
    [Reflection.Emit.AssemblyBuilderAccess]::Run)
$type = $assembly.DefineDynamicModule('DataLabVmLogonRight').DefineType('Lsa', 'Public, Class')
foreach ($method in @(
    @('LsaOpenPolicy', @([IntPtr], [byte[]], [UInt32], [IntPtr].MakeByRefType())),
    @('LsaAddAccountRights', @([IntPtr], [byte[]], [IntPtr], [UInt32])),
    @('LsaNtStatusToWinError', @([UInt32])),
    @('LsaClose', @([IntPtr])))) {
    $defined = $type.DefinePInvokeMethod($method[0], 'advapi32.dll',
        'Public, Static, PinvokeImpl', [Reflection.CallingConventions]::Standard,
        [UInt32], [Type[]]$method[1], [Runtime.InteropServices.CallingConvention]::Winapi,
        [Runtime.InteropServices.CharSet]::Unicode)
    # The NTSTATUS comes back as the value, not turned into an exception.
    $defined.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
}
$Lsa = $type.CreateType()
function Test-Status($status) {
    if ($status -ne 0) {
        throw (New-Object ComponentModel.Win32Exception([int]$Lsa::LsaNtStatusToWinError($status)))
    }
}
$sidObject = New-Object Security.Principal.SecurityIdentifier($Sid)
$sidBytes = New-Object byte[] $sidObject.BinaryLength
$sidObject.GetBinaryForm($sidBytes, 0)
# LSA_OBJECT_ATTRIBUTES, all zero (LsaOpenPolicy ignores its members).
$attributes = New-Object byte[] 64
$policy = [IntPtr]::Zero
# POLICY_CREATE_ACCOUNT | POLICY_LOOKUP_NAMES
Test-Status ($Lsa::LsaOpenPolicy([IntPtr]::Zero, $attributes, 0x810, [ref]$policy))
$name = $Marshal::StringToHGlobalUni($Right)
$unicode = $Marshal::AllocHGlobal(16)
try {
    # LSA_UNICODE_STRING: Length, MaximumLength (in bytes), then the text's address.
    $Marshal::WriteInt16($unicode, 0, [int16]($Right.Length * 2))
    $Marshal::WriteInt16($unicode, 2, [int16]($Right.Length * 2 + 2))
    $Marshal::WriteIntPtr($unicode, [IntPtr]::Size, $name)
    Test-Status ($Lsa::LsaAddAccountRights($policy, $sidBytes, $unicode, 1))
} finally {
    $Marshal::FreeHGlobal($unicode)
    $Marshal::FreeHGlobal($name)
    $null = $Lsa::LsaClose($policy)
}
"""

Run = Callable[..., subprocess.CompletedProcess]
Probe = Literal["ok", "refused", "unknown"]
# How Docker stands, as the page shows it (api/docker.py): "unsupported" off
# Windows (none of this applies there); "ready" when its engine answers;
# "not-installed" when Docker Desktop isn't where its installer puts it;
# "stopped" when Docker Desktop isn't open; "starting" while it's open but its
# engine isn't answering yet; "vm-refused" when the policy has taken the right
# away; "unknown" when WSL itself couldn't say.
DockerState = Literal[
    "unsupported", "ready", "not-installed", "stopped", "starting", "vm-refused", "unknown"
]
# What asking for the fix came to: "fixed"; "declined" when the administrator
# prompt was closed or refused (on a Michigan Medicine computer, most often
# because the temporary administrator access isn't on yet), or when the change
# itself didn't go through; "still-refused" when it went through but Windows
# still won't let the virtual machine sign in; "busy" when a prompt is already
# showing; "not-needed" unless the last check found the VM refused; "working"
# while something in DataLab is (api/docker.py: the restart would stop it);
# "restart-failed" when the right is back but Docker Desktop didn't come back
# (FixResult.failed_step says which step of the restart).
FixOutcome = Literal[
    "fixed",
    "declined",
    "still-refused",
    "busy",
    "not-needed",
    "working",
    "restart-failed",
    "unsupported",
]
# The steps of restarting Docker Desktop after the fix (restart_docker).
RestartStep = Literal["stop", "terminate", "start", "ready"]
# Where a fix under way is (DockerDoctor.phase): Windows' box showing, or the restart.
FixPhase = Literal["prompt", "restarting"]
# Docker Desktop's own processes, which run as the person: the ones a restart
# ends. Never its Windows service (com.docker.service, which runs as SYSTEM).
DOCKER_DESKTOP_IMAGES = (
    "Docker Desktop.exe",
    "com.docker.backend.exe",
    "com.docker.build.exe",
    "docker-sandbox.exe",
)


@dataclass(frozen=True)
class FixResult:
    outcome: FixOutcome
    failed_step: RestartStep | None = None


def system_dir() -> Path:
    """Windows' own System32, from Windows (not SystemRoot, which a person can
    set for their own account): the administrator prompt runs PowerShell from it."""
    if sys.platform == "win32":
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):  # type: ignore[attr-defined]
            return Path(buffer.value)
    return Path(r"C:\Windows\System32")


def powershell() -> str:
    return str(system_dir() / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def docker_desktop() -> Path:
    programs = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    return Path(programs) / "Docker" / "Docker" / "Docker Desktop.exe"


def wsl_text(raw: bytes) -> str:
    """wsl.exe writes its own messages as UTF-16 unless WSL_UTF8 is set, and
    what runs inside Linux writes UTF-8."""
    if raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def docker_answers(run: Run = subprocess.run, timeout: float = 10) -> bool:
    """Whether Docker's engine answers. A stuck one can leave `docker` waiting
    for ever, so this gives up after `timeout` seconds: a working engine
    answers in one or two, and each check a stuck one gets (the page asks
    every half minute) waits the whole time (20 s made Check again spin 20 s)."""
    try:
        return run(["docker", "info"], capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def probe(run: Run = subprocess.run, timeout: float = 90) -> Probe:
    """Try to start WSL's virtual machine: "refused" if Windows won't let it
    sign in (the missing right), "ok" if it starts, "unknown" otherwise.

    WSL's system distribution, not Docker's: starting docker-desktop outside
    Docker Desktop can leave Docker waiting for it to shut down. A virtual
    machine with nothing left to run stops by itself a minute later.
    """
    try:
        done = run(
            [str(system_dir() / "wsl.exe"), "--system", "-e", "true"],
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "WSL_UTF8": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if done.returncode == 0:
        return "ok"
    said = wsl_text(done.stdout or b"") + wsl_text(done.stderr or b"")
    return "refused" if LOGON_REFUSED in said.lower() else "unknown"


def elevated_command(program: str | None = None) -> list[str]:
    """PowerShell that shows the administrator prompt and runs GRANT_SCRIPT
    behind it, passing on its exit code: 1 if the prompt was declined.

    A declined prompt is only a non-terminating error to Start-Process, which
    left `$p` empty and `exit $p.ExitCode` exiting 0 (seen with CyberArk EPM's
    request box, cancelled): so errors stop the starter, and no process is 1.
    `program` is what's started elevated (tests start one that isn't there).
    """
    encoded = base64.b64encode(GRANT_SCRIPT.encode("utf-16-le")).decode("ascii")
    ps = powershell()
    starter = (
        "$ErrorActionPreference = 'Stop'; "
        f"$p = Start-Process '{program or ps}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        "-ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}'; "
        "if (-not $p) { exit 1 }; exit $p.ExitCode"
    )
    return [ps, "-NoProfile", "-NonInteractive", "-Command", starter]


def grant(run: Run = subprocess.run) -> bool:
    """Put the right back, behind one administrator prompt. False if the
    prompt was declined or the change didn't work."""
    try:
        return run(elevated_command(), capture_output=True, timeout=600).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def current_user() -> str | None:
    """This process's account as Windows names it ("DOMAIN\\name"), from
    Windows itself rather than the environment; None when it can't say."""
    if sys.platform != "win32":
        return None
    import ctypes

    size = ctypes.c_ulong(256)
    buffer = ctypes.create_unicode_buffer(size.value)
    secur32 = ctypes.windll.secur32  # type: ignore[attr-defined]
    # NameSamCompatible (2): "UMHS\ataxali", the form tasklist's USERNAME filter takes.
    if not secur32.GetUserNameExW(2, buffer, ctypes.byref(size)):
        return None
    return buffer.value


def own_pids(image: str, user: str, run: Run = subprocess.run) -> list[int] | None:
    """The processes of one program (by its exact file name) that run as
    `user`: never another account's, nor SYSTEM's. None when tasklist can't say."""
    try:
        listed = run(
            [
                str(system_dir() / "tasklist.exe"),
                "/FI",
                f"IMAGENAME eq {image}",
                "/FI",
                f"USERNAME eq {user}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    text = (listed.stdout or b"").decode("utf-8", errors="replace")
    pids = []
    for row in csv.reader(io.StringIO(text)):
        # No match is one line, "INFO: No tasks are running which match…".
        if len(row) >= 2 and row[0].lower() == image.lower() and row[1].isdigit():
            pids.append(int(row[1]))
    return pids


def stop_docker_desktop(
    user: str, run: Run = subprocess.run, sleep: Callable = time.sleep, gone_seconds: int = 15
) -> bool:
    """End Docker Desktop's own processes (DOCKER_DESKTOP_IMAGES, this
    account's only), by process id. False if any is still there after
    `gone_seconds`: taskkill /F answers before a process has quite gone.

    A Docker Desktop started elevated (as an administrator, e.g. through
    CyberArk EPM) may list with no user name, so it isn't found here: it's left
    running, and the restart then fails honestly at "ready"."""
    for image in DOCKER_DESKTOP_IMAGES:
        pids = own_pids(image, user, run)
        if pids is None:
            return False
        for pid in pids:
            # The same filters as the listing, beside the id: a process id
            # Windows has handed to another program since isn't ended ("INFO:
            # No tasks running with the specified criteria.", exit 0), and
            # neither is one that has ended already.
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                run(
                    [
                        str(system_dir() / "taskkill.exe"),
                        "/F",
                        "/FI",
                        f"IMAGENAME eq {image}",
                        "/FI",
                        f"USERNAME eq {user}",
                        "/PID",
                        str(pid),
                    ],
                    capture_output=True,
                    timeout=15,
                )
    for _ in range(gone_seconds):
        sleep(1)
        if all(own_pids(image, user, run) == [] for image in DOCKER_DESKTOP_IMAGES):
            return True
    return False


def terminate_docker_vm(run: Run = subprocess.run) -> bool:
    """Stop Docker's own WSL distribution, and nothing else of WSL's (never
    `wsl --shutdown`). One that isn't there counts as stopped."""
    try:
        done = run(
            [str(system_dir() / "wsl.exe"), "--terminate", "docker-desktop"],
            capture_output=True,
            timeout=60,
            env={**os.environ, "WSL_UTF8": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    said = wsl_text(done.stdout or b"") + wsl_text(done.stderr or b"")
    return done.returncode == 0 or "WSL_E_DISTRO_NOT_FOUND" in said


def restart_docker(
    user: str | None,
    run: Run = subprocess.run,
    popen: Callable = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable = time.sleep,
    ready_seconds: float = 240,
) -> RestartStep | None:
    """Restart Docker Desktop after the fix, and wait until its engine answers.
    None when it did; otherwise the step that didn't work.

    Once its engine has given up waiting for a virtual machine Windows
    refused, Docker Desktop never tries again, and `docker desktop restart`
    left its stuck backend running (seen on the Michigan Medicine laptop,
    0.3.0b3): so its processes are ended, its VM stopped, and it's opened afresh.
    """
    if user is None or not stop_docker_desktop(user, run, sleep):
        return "stop"
    if not terminate_docker_vm(run):
        return "terminate"
    if not start_docker(popen):
        return "start"
    deadline = clock() + ready_seconds
    while clock() < deadline:
        if docker_answers(run):
            return None
        sleep(5)
    return "ready"


def docker_desktop_running(run: Run = subprocess.run) -> bool:
    """Whether Docker Desktop's window app is open (its engine may still be starting)."""
    try:
        listed = run(
            [str(system_dir() / "tasklist.exe"), "/FI", "IMAGENAME eq Docker Desktop.exe", "/NH"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return b"docker desktop.exe" in (listed.stdout or b"").lower()


def start_docker(popen: Callable = subprocess.Popen) -> bool:
    """Open Docker Desktop. It needs no administrator: the installer set its
    service to start by itself."""
    app = docker_desktop()
    if not app.exists():
        return False
    popen([str(app)], close_fds=True)
    return True


def docker_state(
    run: Run = subprocess.run, platform: str = sys.platform, *, probe_vm: bool = True
) -> DockerState:
    """How Docker stands now. WSL's virtual machine is only tried while Docker
    Desktop is open and its engine isn't answering (with `probe_vm`): a
    closed Docker Desktop is "stopped" without starting anything."""
    if platform != "win32":
        return "unsupported"
    if docker_answers(run):
        return "ready"
    if not docker_desktop().exists():
        return "not-installed"
    if not docker_desktop_running(run):
        return "stopped"
    if not probe_vm:
        return "starting"
    found = probe(run)
    if found == "refused":
        return "vm-refused"
    return "starting" if found == "ok" else "unknown"


class DockerDoctor:
    """How Docker stands on this Windows computer, for DataLab's page, and the
    two things the page can do about it: open Docker Desktop, and put the
    virtual machines' right back (one administrator prompt).

    Both the page and DataLab's own start (check_before_serve) ask it, so
    their answers agree. A check can start WSL's virtual machine, so:
    - an answer is kept for CACHE_SECONDS, and asking to check again now
      (`check`, the page's Check again) is ignored within MIN_CHECK_SECONDS;
    - one check runs at a time, and while one does, others get the last answer
      rather than waiting for it;
    - once the virtual machine has started, it isn't tried again for
      VM_OK_SECONDS while Docker Desktop is still starting.
    """

    CACHE_SECONDS = 15
    MIN_CHECK_SECONDS = 5
    VM_OK_SECONDS = 300

    def __init__(
        self,
        *,
        run: Run = subprocess.run,
        popen: Callable = subprocess.Popen,
        platform: str = sys.platform,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        user: Callable[[], str | None] = current_user,
    ) -> None:
        self._run = run
        self._popen = popen
        self._platform = platform
        self._clock = clock
        self._sleep = sleep
        self._user = user
        self._checking = threading.Lock()
        self._fixing = threading.Lock()
        self._state: DockerState | None = None
        self._checked = 0.0
        self._vm_started: float | None = None
        self._phase: FixPhase | None = None

    @property
    def fixing(self) -> bool:
        """Whether the fix is under way (its prompt showing, or the restart after it)."""
        return self._fixing.locked()

    @property
    def phase(self) -> FixPhase | None:
        """Where the fix is: "prompt" (Windows' box is showing) or "restarting"
        (Docker Desktop is being closed and opened again); None when no fix is."""
        return self._phase if self.fixing else None

    def state(self, *, fresh: bool = False) -> DockerState:
        """The last few seconds' answer, or a new one (`fresh`: now)."""
        if self.phase == "restarting":
            # Docker Desktop's processes are down on purpose: not "stopped", or
            # the page would offer to open it in the middle of the restart.
            return "starting"
        if not self._checking.acquire(blocking=self._state is None):
            return self._state  # type: ignore[return-value]  # a check is running
        try:
            if fresh or self._state is None or self._clock() - self._checked > self.CACHE_SECONDS:
                vm_ok = self._vm_started is not None and (
                    self._clock() - self._vm_started < self.VM_OK_SECONDS
                )
                state = docker_state(self._run, self._platform, probe_vm=not vm_ok)
                if state == "starting" and not vm_ok:
                    self._vm_started = self._clock()  # the probe just ran, and the VM started
                elif state != "starting":
                    self._vm_started = None
                self._state = state
                self._checked = self._clock()
            return self._state
        finally:
            self._checking.release()

    def check(self) -> DockerState:
        """Check again now (the page's Check again), at most every few seconds."""
        recent = self._state is not None and self._clock() - self._checked < self.MIN_CHECK_SECONDS
        return self.state(fresh=not recent)

    def start(self) -> DockerState:
        """Open Docker Desktop if it's closed; how Docker stands then."""
        if self.fixing:
            # The fix closes and reopens Docker Desktop itself: opening it in
            # between would look to the restart as if it wouldn't close.
            return self.state()
        if self.state(fresh=True) == "stopped":
            start_docker(self._popen)
        return self.state(fresh=True)

    def fix(self) -> FixResult:
        """Show the administrator prompt and put the right back, then restart
        Docker Desktop and wait until it's ready. Waits until the prompt is
        answered. Only when the last check found the virtual machine refused:
        never while Docker works."""
        if self._platform != "win32":
            return FixResult("unsupported")
        if self.state() != "vm-refused":
            return FixResult("not-needed")
        if not self._fixing.acquire(blocking=False):
            return FixResult("busy")
        self._phase = "prompt"
        try:
            # False also when the change itself didn't go through (the script
            # failed, or the prompt was left unanswered for 10 minutes).
            if not grant(self._run):
                return FixResult("declined")
            if probe(self._run) == "refused":
                return FixResult("still-refused")
            self._vm_started = self._clock()
            self._phase = "restarting"
            failed = restart_docker(self._user(), self._run, self._popen, self._clock, self._sleep)
            return FixResult("fixed") if failed is None else FixResult("restart-failed", failed)
        finally:
            self._fixing.release()
            with self._checking:
                self._state = None


def check_before_serve(doctor: DockerDoctor, *, say: Callable[[str], None] = print) -> DockerState:
    """At DataLab's start on Windows: open Docker Desktop if it's closed, and
    if Windows won't let its virtual machine start, say so in DataLab's window
    too. The page shows the same, with the fix (api/docker.py): the fix needs
    an administrator, which on a Michigan Medicine computer means turning on
    the temporary administrator access first, so it's a button there rather
    than a question here that could only be answered once. Never a reason for
    DataLab not to start."""
    state = doctor.state(fresh=True)
    if state == "stopped":
        state = doctor.start()
        if state != "stopped":
            say("Opening Docker Desktop, which conversations and workflows need (a minute or two).")
    if state == "vm-refused":
        say("")
        say("Docker can't start on this computer right now: Windows won't let its virtual")
        say("machine sign in (a Windows policy took away a right it needs; this happens on")
        say("the Michigan Medicine network). Conversations and workflows can't run until it's")
        say("fixed. DataLab's page shows how: turn on your temporary administrator access,")
        say("then click Fix it. Or restart Windows, which fixes it for a while.")
    return state
