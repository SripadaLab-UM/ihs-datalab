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


def test_uv_is_the_pinned_release_checked_and_used_by_full_path():
    import re

    pins = dict(re.findall(r'(?m)^\$(Uv\w+) = "([^"]*)"$', INSTALL))
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["UvVersion"])
    assert pins["UvZipUrl"] == (
        f"https://github.com/astral-sh/uv/releases/download/{pins['UvVersion']}/"
        "uv-x86_64-pc-windows-msvc.zip"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", pins["UvZipSha256"])
    assert pins["UvPublisher"]
    body = code(INSTALL)
    assert "irm " not in body and "| iex" not in body and "Invoke-Expression" not in body
    assert "& $Uv venv" in body and "& $Uv pip install" in body
    # Never a uv looked up on PATH.
    assert not re.search(r"(?m)^\s*uv\s", body) and "Get-Command uv" not in body


def test_the_shims_ask_python_for_utf8():
    assert "set PYTHONUTF8=1" in INSTALL
    mac = (WINDOWS.parent / "macos" / "install.sh").read_text(encoding="utf-8")
    assert "export PYTHONUTF8=1" in mac


def test_the_start_menu_path_is_escaped_for_any_single_quote():
    assert "EscapeSingleQuotedStringContent($DataLab)" in INSTALL


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("datalab-0.1.0a3-py3-none-any.whl", True),
        ("datalab-7-py3-none-any.whl", True),
        ("datalab-1.-py3-none-any.whl", False),
        ("datalab-.1-py3-none-any.whl", False),
        ("datalab-latest-py3-none-any.whl", False),
    ],
)
def test_the_package_name_must_carry_a_version(name, ok):
    import re

    pattern = re.search(r"-notmatch '(\^datalab-[^']+)'", INSTALL)
    assert pattern is not None
    assert bool(re.match(pattern.group(1), name)) is ok


def test_the_uninstaller_removes_only_what_the_installer_put_in_the_app_folder():
    body = code(UNINSTALL)
    assert 'foreach ($name in "versions", "bin")' in body
    assert "$left.Count -eq 0) { Remove-Tree $Root }" in body
    assert 'Remove-Tree (Join-Path $StateDir "install")' in body


def test_uv_copies_files_and_the_earlier_start_menu_entry_goes():
    # A hardlink into uv's cache fails in a cloud-synced or redirected folder.
    assert "--default-index https://pypi.org/simple --link-mode copy" in INSTALL
    # 0.1.0's "DataLab" entry opened the removed uv tool copy: removed only if so.
    assert '-like "*\\.local\\bin\\datalab*"' in INSTALL


def test_virtualization_is_checked_before_any_administrator_step():
    check = INSTALL.index("if (-not (Test-VirtualizationOn))")
    # Before step 1 asks for administrator permission, downloads or restarts.
    assert check < INSTALL.index('Say "This needs administrator permission, just this once."')
    assert check < INSTALL.index('Step "Step 2 of 8')
    # A running hypervisor (e.g. Credential Guard) counts as on; unknown doesn't refuse.
    body = INSTALL[INSTALL.index("function Test-VirtualizationOn") :]
    body = body[: body.index("\n}\n")]
    assert "HypervisorPresent" in body and "VirtualizationFirmwareEnabled" in body
    assert "return $true" in body.split("catch")[1]
