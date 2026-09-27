"""installer/windows: what can be checked without Windows.

CI's windows-installer job parses both scripts with Windows PowerShell 5.1
and runs their package-finding lines; these check the rest of what they
promise each other and the release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WINDOWS = Path(__file__).resolve().parents[2] / "installer" / "windows"
INSTALL = (WINDOWS / "install.ps1").read_text(encoding="utf-8")
UNINSTALL = (WINDOWS / "uninstall.ps1").read_text(encoding="utf-8")
SHARED_START = "# --- Shared by install.ps1 and uninstall.ps1"
SHARED_END = "# --- End of the part shared by install.ps1 and uninstall.ps1"


def code(text: str) -> str:
    """Without whole-line comments."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def shared(text: str) -> str:
    assert text.count(SHARED_START) == 1 and text.count(SHARED_END) == 1
    return text[text.index(SHARED_START) : text.index(SHARED_END)]


def test_the_shared_block_is_byte_identical_in_both_scripts():
    assert shared(INSTALL) == shared(UNINSTALL)
    functions = ("Remove-Tree", "Test-AdminOwned", "Test-AdminFolder", "Remove-RecordedAdminFolder")
    for function in functions:
        assert f"function {function}(" in shared(INSTALL)


@pytest.mark.parametrize(
    "marker",
    ["if (-not $Package) {", "$IsUrl =", "if (-not $Requirements) { Write-Host"],
)
def test_the_lines_ci_runs_on_their_own_are_still_there(marker):
    """ci.yml's windows-installer job slices the script at these."""
    assert INSTALL.count(marker) == 1


def test_it_installs_only_what_requirements_txt_pins():
    assert "uv tool install" not in code(INSTALL)
    assert "--require-hashes --only-binary :all:" in INSTALL
    assert "--default-index https://pypi.org/simple" in INSTALL
    assert "--no-config" in INSTALL and "-r requirements.txt" in INSTALL
    assert "'^(UV|PIP)_'" in INSTALL  # uv's and pip's settings don't come from the environment


def person_part(text: str) -> str:
    return text[text.index("# The installer, run as the person installing.") :]


def admin_part(text: str) -> str:
    start = text.index("if ($Prepare) {")
    return text[start : text.index("# The installer, run as the person installing.")]


def test_the_github_steps_and_datalab_run_as_the_person_never_elevated():
    commands = ("github sign-in", "repos sync", "pull-images", "--profile $DataLabProfile setup")
    for command in commands:
        assert command in person_part(INSTALL)
        assert command not in admin_part(INSTALL)


def test_quickedit_is_turned_off_in_memory_for_the_admin_window_only():
    assert "Add-Type" not in code(INSTALL)  # it compiles through files in TEMP
    assert "Disable-QuickEdit" in admin_part(INSTALL)
    assert "HKCU:" not in INSTALL and "Set-ItemProperty" not in INSTALL


def test_a_running_datalab_is_never_stopped_by_the_installer():
    assert "Get-RunningDataLab" in person_part(INSTALL)
    assert "Stop-Process" not in INSTALL and "taskkill" not in INSTALL.lower()


def test_each_checked_download_is_logged_with_its_publisher():
    assert 'Say "Checked: SHA-256 and signature ($signedBy)"' in INSTALL


def test_the_uninstaller_says_what_stays_and_how_to_remove_it():
    for thing in ("Docker Desktop", "Windows", "Subsystem for Linux", "uv", "docker-users"):
        assert thing in UNINSTALL[UNINSTALL.index('Write-Host "DataLab has been removed."') :]
    assert '"DataLab (practice).lnk"' in UNINSTALL
