"""The computer's own file and folder picker, for attaching inputs and choosing export folders.

The browser can't name a path on the computer: it can only ask DataLab to
show the native picker, and the person chooses. So a web page, or the agent,
can't attach anything by itself.

The pickers report their choice as JSON. Names may contain line breaks and
other odd characters, so splitting text output on lines could turn one
crafted filename into several paths.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Literal

PickKind = Literal["files", "folder"]

# JavaScript for Automation (osascript -l JavaScript).
_MAC = """
const app = Application.currentApplication();
app.includeStandardAdditions = true;
app.activate();
const chosen = KIND === "folder"
  ? [app.chooseFolder(START
      ? {withPrompt: "Choose a folder for DataLab", defaultLocation: Path(START)}
      : {withPrompt: "Choose a folder for DataLab"})]
  : [].concat(app.chooseFile({withPrompt: "Attach files to DataLab",
                              multipleSelectionsAllowed: true}));
JSON.stringify(chosen.map(String));
"""

# Windows PowerShell 5.1. Windows keeps a program in the background (DataLab's
# PowerShell, here) from taking the focus from the browser, so a dialog with no
# window of its own opens behind it and looks like nothing happened. The
# dialogs get an owner that is actually shown: an invisible, always-on-top
# window where the mouse is, brought to the front. Windows lets it come
# forward while its input is joined to the window in front (the browser) for a
# moment. If that isn't allowed here, the dialog still opens, maybe behind.
_WINDOWS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Opacity = 0
$owner.StartPosition = 'Manual'
$cursor = [System.Windows.Forms.Cursor]::Position
$area = [System.Windows.Forms.Screen]::FromPoint($cursor).WorkingArea
$x = $area.X + [int]($area.Width / 2)
$y = $area.Y + [int]($area.Height / 3)
$owner.Bounds = [System.Drawing.Rectangle]::new($x, $y, 1, 1)
$paths = @()
try {
    $owner.Show()
    try {
        Add-Type -Namespace DataLabPicker -Name Front -MemberDefinition @'
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(IntPtr window, IntPtr process);
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("user32.dll")]
public static extern bool AttachThreadInput(uint from, uint to, bool attach);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr window);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
'@
        $api = [DataLabPicker.Front]
        $front = $api::GetWindowThreadProcessId($api::GetForegroundWindow(), [IntPtr]::Zero)
        $mine = $api::GetCurrentThreadId()
        $joined = $false
        if ($front -ne 0 -and $front -ne $mine) {
            $joined = $api::AttachThreadInput($mine, $front, $true)
        }
        $null = $api::BringWindowToTop($owner.Handle)
        $null = $api::SetForegroundWindow($owner.Handle)
        if ($joined) { $null = $api::AttachThreadInput($mine, $front, $false) }
    } catch { }
    $owner.Activate()
    $owner.BringToFront()
    if ('{kind}' -eq 'folder') {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Choose a folder for DataLab'
        if ($env:DATALAB_PICK_START) { $dialog.SelectedPath = $env:DATALAB_PICK_START }
        if ($dialog.ShowDialog($owner) -eq 'OK') { $paths = @($dialog.SelectedPath) }
    } else {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Attach files to DataLab'
        $dialog.Multiselect = $true
        if ($dialog.ShowDialog($owner) -eq 'OK') { $paths = @($dialog.FileNames) }
    }
} finally {
    $owner.Close()
    $owner.Dispose()
}
ConvertTo-Json -InputObject @($paths) -Compress
"""


def windows_script(kind: PickKind) -> str:
    """The Windows picker's script: fixed text, with only the kind filled in."""
    if kind not in ("files", "folder"):
        raise ValueError(kind)
    return _WINDOWS.replace("{kind}", kind)


def windows_command(kind: PickKind) -> list[str]:
    """PowerShell running the picker script. Encoded, as the script has double
    quotes that Windows' command line would otherwise mangle."""
    encoded = base64.b64encode(windows_script(kind).encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-EncodedCommand", encoded]


# One picker at a time, across attaching and export folders.
_lock = asyncio.Lock()


class PickerUnavailable(RuntimeError):
    pass


class PickerBusy(RuntimeError):
    pass


async def pick(kind: PickKind, *, start_in: Path | None = None) -> list[Path]:
    """Show the picker and return what the person chose (empty if they cancelled).

    `start_in` is the folder a folder picker opens in (such as the Dropbox
    folder DataLab found); the person still chooses.
    """
    env = dict(os.environ)
    start = str(start_in) if kind == "folder" and start_in is not None else ""
    if sys.platform == "darwin":
        script = f"const KIND = {json.dumps(kind)};\nconst START = {json.dumps(start)};\n" + _MAC
        command = ["osascript", "-l", "JavaScript", "-e", script]
    elif sys.platform == "win32":
        # The start folder is passed in the environment, never spliced into the script.
        env["DATALAB_PICK_START"] = start
        command = windows_command(kind)
    else:
        raise PickerUnavailable("Choosing files needs DataLab on a Mac or Windows computer.")
    if _lock.locked():
        raise PickerBusy("A file picker is already open.")
    async with _lock:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        try:
            out, _ = await process.communicate()
        finally:
            # If the request is abandoned, close the picker too.
            if process.returncode is None:
                process.kill()
    if process.returncode != 0:
        return []  # cancelled
    return parse(out.decode("utf-8-sig", "replace"))  # PowerShell may start with a BOM


def parse(output: str) -> list[Path]:
    """The chosen paths, from the picker's JSON. Only full paths count."""
    try:
        chosen = json.loads(output.strip() or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(chosen, str):
        chosen = [chosen]
    if not isinstance(chosen, list):
        return []
    return [Path(p) for p in chosen if isinstance(p, str) and p and Path(p).is_absolute()]
