"""The native pickers' scripts: fixed text, brought to the front, start folder never spliced in."""

import asyncio
import base64
import re
from pathlib import Path

import pytest

from datalab.sessions import picker


def decoded(command: list[str]) -> str:
    assert command[:5] == ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-EncodedCommand"]
    return base64.b64decode(command[5]).decode("utf-16-le")


@pytest.mark.parametrize("kind", ["files", "folder"])
def test_windows_script_is_fixed_text_with_only_the_kind_filled_in(kind):
    script = decoded(picker.windows_command(kind))
    assert script == picker._WINDOWS.replace("{kind}", kind)
    assert "{kind}" not in script
    assert f"if ('{kind}' -eq 'folder')" in script
    # The start folder comes from the environment only.
    assert "$env:DATALAB_PICK_START" in script
    # Nothing else differs between the two.
    other = "files" if kind == "folder" else "folder"
    assert script.replace(f"if ('{kind}'", f"if ('{other}'") == decoded(
        picker.windows_command(other)
    )


def test_windows_script_refuses_any_other_kind():
    with pytest.raises(ValueError):
        picker.windows_script("folder'; Remove-Item C:\\ #")  # type: ignore[arg-type]


def test_windows_dialogs_have_an_owner_that_is_shown_and_brought_to_the_front():
    script = picker._WINDOWS
    for line in (
        "$owner.TopMost = $true",
        "$owner.ShowInTaskbar = $false",
        "$owner.Opacity = 0",
        "$owner.StartPosition = 'Manual'",
        "$owner.Show()",
        "$owner.Activate()",
        "$owner.BringToFront()",
        "$owner.Dispose()",
        "SetForegroundWindow($owner.Handle)",
        "AttachThreadInput($mine, $front, $true)",
        "AttachThreadInput($mine, $front, $false)",
    ):
        assert line in script, line
    # Both kinds of dialog are owned by it.
    assert script.count("ShowDialog($owner)") == 2
    # Shown before the dialogs, and the foreground workaround may fail without stopping the picker.
    assert script.index("$owner.Show()") < script.index("ShowDialog($owner)")
    assert re.search(r"try \{\s+Add-Type -Namespace DataLabPicker", script)
    # The here-string closes at the start of its line, as PowerShell needs.
    assert "\n'@\n" in script


def test_windows_start_folder_goes_in_the_environment(monkeypatch, tmp_path):
    start = tmp_path / "Dropbox (UM)'; $x = 1 #"
    seen: dict = {}

    class Done:
        returncode = 0

        async def communicate(self):
            return b'["C:\\\\Users\\\\me\\\\Dropbox\\\\IHS"]', b""

    async def spawn(*command, env, **_):
        seen["command"], seen["env"] = list(command), env
        return Done()

    monkeypatch.setattr(picker.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(picker.pick("folder", start_in=start))
    assert seen["env"]["DATALAB_PICK_START"] == str(start)
    assert str(start) not in decoded(seen["command"])
    assert seen["command"] == picker.windows_command("folder")


def test_mac_picker_comes_forward_and_passes_the_start_folder_as_json(monkeypatch):
    seen: dict = {}

    class Done:
        returncode = 0

        async def communicate(self):
            return b'["/Users/me/Dropbox/IHS"]', b""

    async def spawn(*command, **_):
        seen["command"] = list(command)
        return Done()

    monkeypatch.setattr(picker.sys, "platform", "darwin")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(picker.pick("folder", start_in=Path('/Users/me/Dropbox "x"')))
    script = seen["command"][4]
    assert script.startswith('const KIND = "folder";\nconst START = "/Users/me/Dropbox \\"x\\"";\n')
    assert "app.activate();" in script
    assert script.index("app.activate();") < script.index("chooseFolder")
