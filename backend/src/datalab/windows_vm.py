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
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from collections.abc import Callable
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
Outcome = Literal[
    "not-windows", "docker-answers", "vm-starts", "unknown", "told", "declined", "failed", "fixed"
]


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


def docker_answers(run: Run = subprocess.run, timeout: float = 20) -> bool:
    """Whether Docker's engine answers. A stuck one can leave `docker` waiting
    for ever, so this gives up after `timeout` seconds."""
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


def elevated_command() -> list[str]:
    """PowerShell that shows the administrator prompt and runs GRANT_SCRIPT
    behind it, passing on its exit code (1 if the prompt was declined)."""
    encoded = base64.b64encode(GRANT_SCRIPT.encode("utf-16-le")).decode("ascii")
    ps = powershell()
    starter = (
        f"$p = Start-Process '{ps}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        "-ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}'; "
        "exit $p.ExitCode"
    )
    return [ps, "-NoProfile", "-NonInteractive", "-Command", starter]


def grant(run: Run = subprocess.run) -> bool:
    """Put the right back, behind one administrator prompt. False if the
    prompt was declined or the change didn't work."""
    try:
        return run(elevated_command(), capture_output=True, timeout=600).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def restart_docker(run: Run = subprocess.run, popen: Callable = subprocess.Popen) -> None:
    """Docker Desktop doesn't try its engine again by itself once it's stuck,
    so it's restarted (or started, if it isn't running)."""
    try:
        restarted = run(
            ["docker", "desktop", "restart", "--detach"], capture_output=True, timeout=60
        )
        if restarted.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    app = docker_desktop()
    if app.exists():
        popen([str(app)], close_fds=True)


def check_before_serve(
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    interactive: bool | None = None,
    run: Run = subprocess.run,
    popen: Callable = subprocess.Popen,
    platform: str = sys.platform,
) -> Outcome:
    """At DataLab's start on Windows: if Docker isn't answering because its
    virtual machine may not sign in, say so in plain words and offer to fix
    it. Never a reason for DataLab not to start."""
    if platform != "win32":
        return "not-windows"
    if docker_answers(run):
        return "docker-answers"
    found = probe(run)
    if found != "refused":
        return "vm-starts" if found == "ok" else "unknown"
    say("")
    say("Docker can't start on this computer right now: Windows won't let its virtual")
    say("machine sign in. A Windows policy took away a right it needs (this happens on the")
    say("Michigan Medicine network). Until it's fixed, conversations and workflows can't run.")
    say("Two ways to fix it:")
    say("  - Restart Windows (no administrator needed; it can happen again later), or")
    say("  - Fix it now: DataLab asks Windows for administrator permission once, gives the")
    say("    right back, and restarts Docker Desktop. If your computer gives you")
    say("    administrator access for a limited time, request it first.")
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        say("To fix it now, open DataLab from its Start menu entry; or restart Windows.")
        return "told"
    if ask("Fix it now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        say("OK. Restart Windows when you can; DataLab keeps running meanwhile.")
        return "declined"
    say("Asking Windows for permission (look for the box; it may be behind this window)...")
    if not grant(run) or probe(run) == "refused":
        say("That didn't work (the box was closed, or administrator access has run out).")
        say("Restart Windows instead; DataLab keeps running meanwhile.")
        return "failed"
    restart_docker(run, popen)
    say("Fixed. Docker Desktop is restarting, which takes a minute or two.")
    return "fixed"
