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

_WINDOWS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
$paths = @()
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
ConvertTo-Json -InputObject @($paths) -Compress
"""

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
        script = _WINDOWS.replace("{kind}", kind)  # "files" or "folder" only
        # Passed in the environment, never spliced into the script.
        env["DATALAB_PICK_START"] = start
        command = ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-Command", script]
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
