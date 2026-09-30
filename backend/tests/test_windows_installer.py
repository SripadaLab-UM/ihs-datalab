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
    [
        "if (-not $Package) {",
        "$IsUrl =",
        "if (-not $Requirements) { Write-Host",
        "\n$DockerDesktopImages = ",
        "\nfunction Invoke-Captured(",
        "\nfunction Get-OwnPids(",
        "\nfunction Stop-DockerDesktopProcesses {",
        "\nfunction Stop-DockerVm {",
    ],
)
def test_the_lines_ci_runs_on_their_own_are_still_there(marker):
    """ci.yml's windows-installer job slices the script at these, or runs
    these functions on their own."""
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
    assert "Stop-Process" not in INSTALL
    # taskkill only closes Docker Desktop (Repair-VmLogon), by its own
    # programs' names: never DataLab.
    function = code(INSTALL[INSTALL.index("function Stop-DockerDesktopProcesses") :])
    function = function[: function.index("\n}\n")]
    assert code(INSTALL).lower().count("taskkill") == function.lower().count("taskkill") == 1
    assert "foreach ($image in $DockerDesktopImages)" in function
    assert "datalab" not in function.lower()


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
    assert 'foreach ($name in "versions", "bin", "icons")' in body
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


def launcher_part(text: str) -> str:
    return text[text.index('Step "Step 8 of 8') :]


def test_the_start_menu_and_desktop_shortcuts_run_the_shim_with_each_profiles_icon():
    part = launcher_part(INSTALL)
    assert '$Links = @(Join-Path $StartMenu "$LinkName.lnk")' in part
    assert '[Environment]::GetFolderPath("Desktop")' in part
    assert '$DesktopLink = Join-Path $Desktop "$LinkName.lnk"' in part
    assert "$Links += $DesktopLink" in part
    # bin\datalab.cmd, which runs whichever version `current` names: never a
    # version's own folder, so an update doesn't break them.
    assert '$DataLab = Join-Path $Bin "datalab.cmd"' in INSTALL
    assert "EscapeSingleQuotedStringContent($DataLab)" in part
    assert "-Command `\"& '$QuotedShim' --profile $DataLabProfile serve`\"" in part
    # Someone else's Desktop shortcut of that name (one that doesn't run this
    # shim) is left alone, and it says so.
    assert '"$($Existing.Arguments)".IndexOf("\'$QuotedShim\'"' in part
    assert "that isn't DataLab's; it was left alone." in part
    assert "versions\\" not in part and "Scripts\\datalab.exe" not in part
    # The icon comes from the package, and is kept beside bin\ (not in a
    # version's folder, which an update removes).
    assert '$Icons = Join-Path $Root "icons"' in part
    assert 'Join-Path $Target "Lib\\site-packages\\datalab\\branding\\$IconName"' in part
    assert '"DataLab-practice.ico" } else { "DataLab.ico" }' in part
    assert '$Shortcut.IconLocation = "$Icon,0"' in part


def test_uninstall_removes_the_desktop_shortcuts_and_the_icons():
    assert 'foreach ($name in "versions", "bin", "icons")' in UNINSTALL
    part = UNINSTALL[UNINSTALL.index('[Environment]::GetFolderPath("Desktop")') :]
    assert 'foreach ($name in "DataLab.lnk", "DataLab (practice).lnk")' in part
    # Only DataLab's own: a shortcut that runs this DataLab's bin\datalab.cmd,
    # quoted as install.ps1 writes it.
    assert '$Shim = Join-Path $Root "bin\\datalab.cmd"' in UNINSTALL
    assert "EscapeSingleQuotedStringContent($Shim)" in UNINSTALL
    assert '"$($Shortcut.Arguments)".IndexOf("\'$QuotedShim\'"' in part
    assert "-like" not in part.split('Write-Host "DataLab has been removed."')[0]


def test_the_icons_the_installers_name_come_with_the_package():
    branding = Path(__file__).resolve().parents[1] / "src" / "datalab" / "branding"
    for name in ("DataLab.ico", "DataLab-practice.ico", "DataLab.icns", "DataLab-practice.icns"):
        assert (branding / name).stat().st_size > 1000, name
    assert (branding / "DataLab.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (branding / "DataLab.icns").read_bytes()[:4] == b"icns"


def test_a_failed_key_step_stops_the_installer_instead_of_saying_all_done():
    step6 = INSTALL[INSTALL.index('Step "Step 6 of 8') : INSTALL.index('Step "Step 7 of 8')]
    assert step6.index("setup") < step6.index("if ($LASTEXITCODE -ne 0) {")
    assert "Stop-Install" in step6


def practice_step(text: str) -> str:
    """Step 7, from the `if ($Practice) {` that names it to step 8."""
    title = text.index('Step "Step 7 of 8: Setting up the practice database"')
    return text[text.rindex("if ($Practice) {", 0, title) : text.index('Step "Step 8 of 8')]


def test_practice_sets_up_its_database_as_the_person_and_never_stops_the_install():
    step7 = practice_step(INSTALL)
    practice = step7[step7.index("if ($Practice) {\n    Say") : step7.index("} elseif")]
    assert "& $DataLab --profile $DataLabProfile practice-db setup" in practice
    assert "Stop-Install" not in practice
    assert "DataLab tries again each time it opens." in practice
    assert "reachable from this computer only" in practice
    assert "data it already has is kept" in practice
    assert "practice-db setup" in person_part(INSTALL)
    assert "practice-db" not in admin_part(INSTALL)
    # Only practice runs it: the real profile's step 7 is the GitHub one.
    assert code(INSTALL).count("practice-db setup") == 1


def test_practices_key_prompt_says_its_optional_and_asks_for_no_password():
    step6 = INSTALL[INSTALL.index('Step "Step 6 of 8') : INSTALL.index('Step "Step 7 of 8')]
    practice = step6[step6.index("if ($Practice) {") : step6.index("} else {")]
    assert "It's optional for practice" in practice
    assert "No database password, VPN or GitHub account is needed." in practice
    assert "password, if" not in practice


def test_the_practice_changes_keep_the_scripts_parseable_by_windows_powershell():
    """Windows PowerShell 5.1 reads a script without a BOM as the ANSI code
    page, so the scripts stay ASCII; and every block the practice steps open
    is closed (CI's windows-installer job parses both scripts for real)."""
    for text in (INSTALL, UNINSTALL):
        assert text.isascii()
    step = practice_step(INSTALL)
    body = code(step)
    assert body.count("{") == body.count("}")
    assert body.count("(") == body.count(")")
    assert body.count('"') % 2 == 0


def test_the_uninstaller_says_it_asks_about_the_practice_database():
    assert "before deleting the practice database" in UNINSTALL
    assert "& $Shim uninstall @choice" in UNINSTALL  # which asks (setup.uninstall)
