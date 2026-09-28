"""The macOS installer's steps, run for real against stand-ins.

`docker`, `uv`, `open` and `osascript` are small scripts on PATH, and the
"installed" DataLab is one too, logging what the installer asks of it.
Nothing is downloaded or installed, and HOME is a temporary folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from datalab.updater import Layout

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the macOS installer's shell")

INSTALLER = Path(__file__).resolve().parents[2] / "installer" / "macos" / "install.sh"

FAKE_DATALAB = """#!/bin/sh
echo "$*" >> "$DATALAB_TEST_LOG"
if [ -n "${DATALAB_TEST_WHO:-}" ]; then echo "$0" >> "$DATALAB_TEST_WHO"; fi
if [ -n "${DATALAB_TEST_PATHLOG:-}" ]; then echo "$PATH" >> "$DATALAB_TEST_PATHLOG"; fi
case "$*" in
  --version) echo "datalab $DATALAB_TEST_VERSION" ;;
  *"github sign-in"*) exit "${DATALAB_TEST_SIGNIN:-0}" ;;
  *"practice-db setup"*) exit "${DATALAB_TEST_PRACTICE_DB:-0}" ;;
esac
exit 0
"""

FAKE_UV = """#!/bin/sh
case "$1" in
  --version) echo "uv 0.12.19" ;;
  venv)
    for last; do :; done
    mkdir -p "$last/bin"; : > "$last/bin/python"
    # The icons the package brings (datalab/branding).
    icons="$last/lib/python3.13/site-packages/datalab/branding"
    mkdir -p "$icons"
    echo "real icon" > "$icons/DataLab.icns"
    echo "practice icon" > "$icons/DataLab-practice.icns" ;;
  pip)
    here="$(basename "$(pwd)")"
    echo "pip $* (in $here) UV_INDEX_URL=${UV_INDEX_URL:-unset}" >> "$DATALAB_TEST_UVLOG"
    while [ $# -gt 0 ]; do
      if [ "$1" = "--python" ]; then python="$2"; fi
      shift
    done
    cp "$DATALAB_TEST_FAKE" "$(dirname "$python")/datalab"
    chmod +x "$(dirname "$python")/datalab" ;;
esac
"""


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture(scope="session")
def system_bin(tmp_path_factory) -> Path:
    """The system's commands, without any `docker`: a CI runner (or this Mac)
    may have a real one on PATH, and the tests must never find it. Every
    installer run gets exactly the fakes plus this folder on PATH.
    DATALAB_TEST_SYSTEM_BIN_EXTRA names a folder searched first (to check
    that a `docker` there is left out)."""
    folder = tmp_path_factory.mktemp("system-bin")
    sources = [*filter(None, [os.environ.get("DATALAB_TEST_SYSTEM_BIN_EXTRA")]), "/usr/bin", "/bin"]
    for source in sources:
        for entry in sorted(Path(source).iterdir()) if Path(source).is_dir() else []:
            link = folder / entry.name
            if entry.name.startswith("docker") or link.exists() or link.is_symlink():
                continue
            link.symlink_to(entry)
    return folder


@pytest.fixture
def machine(tmp_path, system_bin) -> dict[str, Path]:
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(tools / "docker", "#!/bin/sh\nexit 0\n")
    executable(tools / "open", "#!/bin/sh\nexit 0\n")
    # Logs what it's asked to run: each argument as ARG:<value>, then the
    # script it reads from its input.
    executable(
        tools / "osascript",
        '#!/bin/sh\n{ for a; do echo "ARG:$a"; done; cat; } >> "${DATALAB_TEST_OSA:-/dev/null}"\n',
    )
    executable(tools / "curl", "#!/bin/sh\necho 'no downloads in tests' >&2\nexit 1\n")
    executable(tools / "uv", FAKE_UV)
    fake = tmp_path / "fake-datalab"
    fake.write_text(FAKE_DATALAB)
    packages = tmp_path / "release"
    packages.mkdir()
    (home / "Desktop").mkdir()
    # Stands in for /Applications, which the tests must never touch.
    system_apps = tmp_path / "Applications"
    system_apps.mkdir()
    return {
        "home": home,
        "system": system_bin,
        "apps": system_apps,
        "tools": tools,
        "fake": fake,
        "packages": packages,
        "log": tmp_path / "log",
        "uvlog": tmp_path / "uvlog",
    }


def install(
    machine,
    version: str,
    *args: str,
    signin: int = 0,
    practice_db: int = 0,
    pinned: bool = True,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    wheel = f"datalab-{version}-py3-none-any.whl"
    package = machine["packages"] / wheel
    package.write_bytes(f"package {version}".encode())
    digest = hashlib.sha256(package.read_bytes() if pinned else b"another").hexdigest()
    (machine["packages"] / "requirements.txt").write_text(
        f"httpx==0.28.1 \\\n    --hash=sha256:{'1' * 64}\n./{wheel} --hash=sha256:{digest}\n"
    )
    env = {
        **extra_env,
        "HOME": str(machine["home"]),
        "PATH": f"{machine['tools']}:{machine['system']}",
        "DATALAB_TEST_LOG": str(machine["log"]),
        "DATALAB_TEST_FAKE": str(machine["fake"]),
        "DATALAB_TEST_VERSION": version,
        "DATALAB_TEST_SIGNIN": str(signin),
        "DATALAB_TEST_PRACTICE_DB": str(practice_db),
        "DATALAB_TEST_UVLOG": str(machine["uvlog"]),
        "DATALAB_SYSTEM_APPLICATIONS": str(machine["apps"]),
    }
    return subprocess.run(
        ["sh", str(INSTALLER), "--package", str(package), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        # No terminal to ask: the question's default (yes) is taken.
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )


def root(machine) -> Path:
    return machine["home"] / "Library" / "Application Support" / "DataLab" / "app"


def asked(machine) -> list[str]:
    lines = machine["log"].read_text().splitlines()
    machine["log"].unlink()
    return lines


def test_it_installs_a_version_in_its_own_folder_then_signs_in_and_syncs(machine):
    done = install(machine, "0.1.0a3", "--settings", "/lab/settings.toml")
    assert done.returncode == 0, done.stdout + done.stderr
    app = root(machine)
    assert (app / "versions" / "0.1.0a3" / ".complete").is_file()
    assert (app / "current").read_text() == "0.1.0a3\n"
    assert not (app / "previous").exists()
    assert asked(machine) == [
        "--version",  # the new folder's own check
        "--version",  # through bin/datalab, the launcher's command
        "--profile real pull-images",
        "--profile real setup --settings /lab/settings.toml",
        "--profile real github sign-in",
        "--profile real repos sync",
    ]
    launcher = machine["apps"] / "DataLab.app" / "Contents" / "MacOS" / "DataLab"
    text = launcher.read_text()
    assert f"exec osascript - '{app}/bin/datalab' <<'OSA'" in text
    assert 'do script (quoted form of item 1 of argv) & " --profile real serve"' in text
    # The updater recognises what the installer made.
    layout = Layout(app, windows=False, prefix=app / "versions" / "0.1.0a3")
    assert layout.running_version() == "0.1.0a3" and layout.installed() == ["0.1.0a3"]


def test_installing_a_newer_version_keeps_the_one_before(machine):
    install(machine, "0.1.0a3")
    again = install(machine, "0.1.0a4")
    assert again.returncode == 0, again.stdout + again.stderr
    app = root(machine)
    assert (app / "current").read_text() == "0.1.0a4\n"
    assert (app / "previous").read_text() == "0.1.0a3\n"
    assert (app / "versions" / "0.1.0a3" / ".complete").is_file()
    # The shim runs whichever version `current` names.
    shim = subprocess.run(
        [str(app / "bin" / "datalab"), "--version"],
        env={**os.environ, "DATALAB_TEST_LOG": str(machine["log"]), "DATALAB_TEST_VERSION": "x"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert shim.stdout.strip() == "datalab x"


def test_practice_skips_the_github_steps(machine):
    done = install(machine, "0.1.0a3", "--profile", "practice")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Skipped: practice DataLab doesn't use the lab's repositories." in done.stdout
    assert not any("github" in line or "repos" in line for line in asked(machine))


def test_practice_sets_up_its_database_and_asks_for_no_password(machine):
    done = install(machine, "0.1.0a3", "--profile", "practice")
    assert done.returncode == 0, done.stdout + done.stderr
    assert asked(machine) == [
        "--version",
        "--version",
        "--profile practice pull-images",
        "--profile practice setup",
        "--profile practice practice-db setup",
    ]
    assert "6/7 Setting up the practice database" in done.stdout
    assert "reachable from this computer only" in done.stdout
    assert "data it already has is kept" in done.stdout
    assert "It's optional for practice" in done.stdout
    assert "No database password, VPN or GitHub account is needed." in done.stdout
    assert "database password, if" not in done.stdout


def test_a_practice_database_that_isnt_ready_still_finishes_the_install(machine):
    done = install(machine, "0.1.0a3", "--profile", "practice", practice_db=1)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "DataLab tries again each time it opens." in done.stdout
    assert "7/7 Launcher" in done.stdout


def test_the_real_install_never_touches_the_practice_database(machine):
    done = install(machine, "0.1.0a3")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any("practice" in line for line in asked(machine))
    assert "6/7 The lab's knowledge base and pipelines" in done.stdout
    assert "database password, if" in done.stdout


def test_nothing_to_sign_in_to_means_no_sync(machine):
    done = install(machine, "0.1.0a3", signin=2)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = asked(machine)
    assert "--profile real github sign-in" in lines and "--profile real repos sync" not in lines


def test_a_failed_sign_in_still_finishes_the_install(machine):
    done = install(machine, "0.1.0a3", signin=1)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--profile real repos sync" in asked(machine)  # it says to sign in first
    assert "7/7 Launcher" in done.stdout


def test_a_package_without_a_version_in_its_name_is_refused(machine):
    package = machine["packages"] / "datalab.whl"
    package.write_text("")
    done = subprocess.run(
        ["sh", str(INSTALLER), "--package", str(package)],
        env={"HOME": str(machine["home"]), "PATH": f"{machine['tools']}:{machine['system']}"},
        capture_output=True,
        text=True,
        timeout=60,
        start_new_session=True,
    )
    assert done.returncode == 2 and "datalab-<version>-py3-none-any.whl" in done.stdout


def test_uv_installs_only_what_requirements_txt_pins_by_hash(machine):
    done = install(machine, "0.1.0a3", UV_INDEX_URL="https://evil.example/simple")
    assert done.returncode == 0, done.stdout + done.stderr
    [pip] = machine["uvlog"].read_text().splitlines()
    assert (
        "pip install -q --no-config --require-hashes --only-binary :all: "
        "--default-index https://pypi.org/simple --link-mode copy --python "
    ) in pip
    assert pip.split(" (in ")[0].endswith("-r requirements.txt")
    assert "UV_INDEX_URL=unset" in pip  # the environment can't steer uv
    record = json.loads((root(machine) / "versions" / "0.1.0a3" / ".complete").read_text())
    package = machine["packages"] / "datalab-0.1.0a3-py3-none-any.whl"
    assert record["wheel_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()


def test_a_package_requirements_txt_doesnt_pin_is_refused(machine):
    done = install(machine, "0.1.0a3", pinned=False)
    assert done.returncode == 1 and "doesn't name this package with this checksum" in done.stdout
    assert not (root(machine) / "versions" / "0.1.0a3").exists()


def test_the_same_version_with_another_package_is_reinstalled(machine):
    install(machine, "0.1.0a3")
    (root(machine) / "versions" / "0.1.0a3" / ".complete").write_text(
        json.dumps({"version": "0.1.0a3", "wheel_sha256": "0" * 64})
    )
    machine["uvlog"].unlink()
    done = install(machine, "0.1.0a3")
    assert done.returncode == 0 and machine["uvlog"].exists()


def test_practice_gets_a_launcher_of_its_own(machine):
    install(machine, "0.1.0a3")
    install(machine, "0.1.0a3", "--profile", "practice")
    apps = machine["apps"]
    assert sorted(p.name for p in apps.iterdir()) == ["DataLab (practice).app", "DataLab.app"]
    plist = (apps / "DataLab (practice).app" / "Contents" / "Info.plist").read_text()
    assert "edu.umich.ihs.datalab.practice" in plist
    assert (
        "--profile practice serve"
        in (apps / "DataLab (practice).app" / "Contents" / "MacOS" / "DataLab").read_text()
    )


def test_the_shim_falls_back_to_the_previous_version(machine):
    install(machine, "0.1.0a3")
    install(machine, "0.1.0a4")
    app = root(machine)
    (app / "versions" / "0.1.0a4" / "bin" / "datalab").unlink()  # broken
    shim = subprocess.run(
        [str(app / "bin" / "datalab"), "--version"],
        env={**os.environ, "DATALAB_TEST_LOG": str(machine["log"]), "DATALAB_TEST_VERSION": "x"},
        capture_output=True,
        text=True,
    )
    assert shim.returncode == 0 and "opening the version before it" in shim.stderr


def test_a_version_must_start_with_a_digit(machine):
    done = install(machine, "latest")
    assert done.returncode == 2 and "datalab-<version>-py3-none-any.whl" in done.stdout


def test_it_refuses_to_run_as_root(machine, tmp_path):
    executable(machine["tools"] / "id", "#!/bin/sh\necho 0\n")
    done = install(machine, "0.1.0a3")
    assert done.returncode == 1 and "Run this without sudo." in done.stdout
    assert not root(machine).exists()


# ------------------------------------------------------------------ the app, where people look

UNINSTALLER = INSTALLER.with_name("uninstall.sh")


def open_app(machine, launcher: Path, tmp_path: Path) -> tuple[str, str]:
    """Run the app's launch script. Returns the program path it hands
    AppleScript, and the command Terminal would then run (AppleScript's
    `quoted form of` the path, then the profile's arguments)."""
    osa = tmp_path / "osa"
    osa.unlink(missing_ok=True)
    env = {
        **os.environ,
        "PATH": f"{machine['tools']}:{machine['system']}",
        "DATALAB_TEST_OSA": str(osa),
    }
    subprocess.run([str(launcher)], env=env, check=True)
    lines = osa.read_text().splitlines()
    args = [line.removeprefix("ARG:") for line in lines if line.startswith("ARG:")]
    assert args[0] == "-" and len(args) == 2  # the script on its input, the path its argument
    script = "\n".join(line for line in lines if not line.startswith("ARG:"))
    assert "on run argv" in script
    profile = script.split('of argv) & " --profile ', 1)[1].split('"', 1)[0]
    quoted = "'" + args[1].replace("'", "'\\''") + "'"  # AppleScript's quoted form
    return args[1], f"{quoted} --profile {profile}"


def run_in_terminal(machine, command: str, tmp_path: Path) -> str:
    """Run what Terminal would; returns which DataLab program ran."""
    who = tmp_path / "who"
    who.unlink(missing_ok=True)
    env = {
        **os.environ,
        "DATALAB_TEST_LOG": str(machine["log"]),
        "DATALAB_TEST_WHO": str(who),
        "DATALAB_TEST_VERSION": "x",
    }
    subprocess.run(["sh", "-c", command], env=env, check=True)
    return who.read_text().strip()


def test_the_app_goes_in_applications_with_its_icon_and_a_desktop_shortcut(machine):
    done = install(machine, "0.1.0a3")
    assert done.returncode == 0, done.stdout + done.stderr
    app = machine["apps"] / "DataLab.app"
    plist = (app / "Contents" / "Info.plist").read_text()
    assert "<key>CFBundleIconFile</key><string>DataLab</string>" in plist
    assert (app / "Contents" / "Resources" / "DataLab.icns").read_text() == "real icon\n"
    link = machine["home"] / "Desktop" / "DataLab"
    assert link.is_symlink() and Path(os.readlink(link)) == app
    assert not (machine["home"] / "Applications").exists()
    # It says where everything went, and asks nothing without a terminal.
    assert f"The app:           {app}" in done.stdout
    assert f"Desktop shortcut:  {link}" in done.stdout
    assert f"Program files:     {root(machine)}" in done.stdout
    assert "Open DataLab now?" not in done.stdout and "Finder? [" not in done.stdout


def test_practice_has_its_own_name_icon_and_shortcut(machine):
    install(machine, "0.1.0a3")
    install(machine, "0.1.0a3", "--profile", "practice")
    app = machine["apps"] / "DataLab (practice).app"
    assert (app / "Contents" / "Resources" / "DataLab.icns").read_text() == "practice icon\n"
    assert "<string>DataLab (practice)</string>" in (app / "Contents" / "Info.plist").read_text()
    desktop = machine["home"] / "Desktop"
    assert sorted(p.name for p in desktop.iterdir()) == ["DataLab", "DataLab (practice)"]
    assert Path(os.readlink(desktop / "DataLab (practice)")) == app


def test_without_rights_to_applications_it_uses_your_own(machine):
    machine["apps"].chmod(0o555)
    try:
        done = install(machine, "0.1.0a3", "--profile", "practice")
    finally:
        machine["apps"].chmod(0o755)
    assert done.returncode == 0, done.stdout + done.stderr
    app = machine["home"] / "Applications" / "DataLab (practice).app"
    assert (app / "Contents" / "MacOS" / "DataLab").is_file()
    assert Path(os.readlink(machine["home"] / "Desktop" / "DataLab (practice)")) == app
    assert list(machine["apps"].iterdir()) == []


def test_someone_elses_app_of_that_name_is_left_alone(machine):
    theirs = machine["apps"] / "DataLab.app" / "Contents"
    theirs.mkdir(parents=True)
    (theirs / "Info.plist").write_text("<string>org.example.datalab</string>")
    done = install(machine, "0.1.0a3")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (theirs / "Info.plist").read_text() == "<string>org.example.datalab</string>"
    assert (machine["home"] / "Applications" / "DataLab.app").is_dir()


def test_an_earlier_installers_copy_in_your_applications_goes(machine):
    machine["apps"].chmod(0o555)
    try:
        install(machine, "0.1.0a3")  # into ~/Applications, as before
    finally:
        machine["apps"].chmod(0o755)
    done = install(machine, "0.1.0a3")
    assert "Removed the copy an earlier installer put in" in done.stdout
    assert not (machine["home"] / "Applications" / "DataLab.app").exists()
    link = machine["home"] / "Desktop" / "DataLab"
    assert Path(os.readlink(link)) == machine["apps"] / "DataLab.app"  # moved with it


def test_other_things_on_the_desktop_are_left_alone(machine):
    desktop = machine["home"] / "Desktop"
    (desktop / "DataLab").write_text("my notes")
    (desktop / "DataLab (practice)").symlink_to("/somewhere/else")
    install(machine, "0.1.0a3")
    done = install(machine, "0.1.0a3", "--profile", "practice")
    assert "to something else; it was left alone" in done.stdout
    assert (desktop / "DataLab").read_text() == "my notes"
    assert os.readlink(desktop / "DataLab (practice)") == "/somewhere/else"


def test_the_app_and_its_shortcut_keep_working_after_an_update(machine, tmp_path):
    install(machine, "0.1.0a3")
    app = root(machine)
    launcher = machine["home"] / "Desktop" / "DataLab" / "Contents" / "MacOS" / "DataLab"
    assert "versions" not in launcher.read_text()  # never a version's own folder
    # An in-app update, as the updater does it: the new version beside the
    # old one, the launcher switched to it, then the old one removed.
    layout = Layout(app, windows=False, prefix=app / "versions" / "0.1.0a4")
    new = layout.folder("0.1.0a4")
    (new / "bin").mkdir(parents=True)
    executable(new / "bin" / "datalab", machine["fake"].read_text())
    (new / ".complete").write_text("{}")
    layout.switch("0.1.0a4", previous="0.1.0a3")
    assert layout.prune({"0.1.0a4"}) == ["0.1.0a3"]
    # Open the app through the Desktop shortcut: it runs the shim...
    shim, command = open_app(machine, launcher, tmp_path)
    assert shim == str(layout.shim)
    # ...which runs the new version.
    assert Path(run_in_terminal(machine, command, tmp_path)) == layout.executable("0.1.0a4")
    assert (machine["apps"] / "DataLab.app" / "Contents" / "Resources" / "DataLab.icns").is_file()


def test_uninstall_removes_the_apps_and_their_desktop_shortcuts_only(machine):
    install(machine, "0.1.0a3")
    install(machine, "0.1.0a3", "--profile", "practice")
    other = machine["apps"] / "Other.app"
    other.mkdir()
    (machine["home"] / "Desktop" / "notes.txt").write_text("mine")
    machine["log"].unlink()
    done = subprocess.run(
        ["sh", str(UNINSTALLER), "--keep-data"],
        env={
            "HOME": str(machine["home"]),
            "PATH": f"{machine['tools']}:{machine['system']}",
            "DATALAB_TEST_LOG": str(machine["log"]),
            "DATALAB_TEST_VERSION": "0.1.0a3",
            "DATALAB_SYSTEM_APPLICATIONS": str(machine["apps"]),
        },
        capture_output=True,
        text=True,
        timeout=60,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert asked(machine) == ["uninstall --keep-data"]
    assert [p.name for p in machine["apps"].iterdir()] == ["Other.app"]
    assert [p.name for p in (machine["home"] / "Desktop").iterdir()] == ["notes.txt"]
    assert not root(machine).exists()


def uninstall(machine) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(UNINSTALLER), "--keep-data"],
        env={
            "HOME": str(machine["home"]),
            "PATH": f"{machine['tools']}:{machine['system']}",
            "DATALAB_TEST_LOG": str(machine["log"]),
            "DATALAB_TEST_VERSION": "0.1.0a3",
            "DATALAB_SYSTEM_APPLICATIONS": str(machine["apps"]),
        },
        capture_output=True,
        text=True,
        timeout=60,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )


def test_the_app_opens_from_a_home_folder_with_an_apostrophe(machine, tmp_path):
    home = tmp_path / "o'brien"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / "Desktop").mkdir()
    machine = {**machine, "home": home}
    done = install(machine, "0.1.0a3", "--profile", "practice")
    assert done.returncode == 0, done.stdout + done.stderr
    launcher = machine["apps"] / "DataLab (practice).app" / "Contents" / "MacOS" / "DataLab"
    subprocess.run(["sh", "-n", str(launcher)], check=True)  # the launch script parses
    shim, command = open_app(machine, launcher, tmp_path)
    assert shim == str(root(machine) / "bin" / "datalab") and "o'brien" in shim
    assert command.endswith(" --profile practice serve")
    ran = run_in_terminal(machine, command, tmp_path)
    assert Path(ran) == root(machine) / "versions" / "0.1.0a3" / "bin" / "datalab"


def test_someone_elses_app_in_your_applications_stops_the_install_there(machine):
    machine["apps"].chmod(0o555)
    theirs = machine["home"] / "Applications" / "DataLab.app" / "Contents"
    theirs.mkdir(parents=True)
    (theirs / "Info.plist").write_text("<string>org.example.datalab</string>")
    try:
        done = install(machine, "0.1.0a3")
    finally:
        machine["apps"].chmod(0o755)
    assert done.returncode == 1
    assert "is another app, not DataLab's, so it was left alone" in done.stdout
    assert (theirs / "Info.plist").read_text() == "<string>org.example.datalab</string>"
    assert not (machine["home"] / "Desktop" / "DataLab").exists()


def test_our_app_that_cant_be_replaced_in_applications_falls_back_to_yours(machine):
    install(machine, "0.1.0a3")
    stuck = machine["apps"] / "DataLab.app" / "Contents"
    stuck.chmod(0o555)  # rm can't empty it
    try:
        done = install(machine, "0.1.0a3")
    finally:
        stuck.chmod(0o755)
    assert done.returncode == 0, done.stdout + done.stderr
    app = machine["home"] / "Applications" / "DataLab.app"
    assert f"The app:           {app}" in done.stdout
    assert "An earlier copy is still in" in done.stdout
    assert Path(os.readlink(machine["home"] / "Desktop" / "DataLab")) == app


def test_our_app_that_cant_be_replaced_in_your_applications_says_what_to_do(machine):
    machine["apps"].chmod(0o555)
    try:
        install(machine, "0.1.0a3")
        stuck = machine["home"] / "Applications" / "DataLab.app" / "Contents"
        stuck.chmod(0o555)
        try:
            done = install(machine, "0.1.0a3")
        finally:
            stuck.chmod(0o755)
    finally:
        machine["apps"].chmod(0o755)
    assert done.returncode == 1
    assert "couldn't be replaced. Quit DataLab if it's open" in done.stdout
    assert "rm:" not in done.stdout + done.stderr


def test_a_desktop_link_to_another_datalab_app_is_left_alone(machine):
    link = machine["home"] / "Desktop" / "DataLab"
    link.symlink_to("/Vendor/DataLab.app")
    done = install(machine, "0.1.0a3")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "to something else; it was left alone" in done.stdout
    assert os.readlink(link) == "/Vendor/DataLab.app"
    uninstall(machine)
    assert os.readlink(link) == "/Vendor/DataLab.app"


def test_uninstall_leaves_someone_elses_app_in_your_applications(machine):
    install(machine, "0.1.0a3")
    theirs = machine["home"] / "Applications" / "DataLab (practice).app" / "Contents"
    theirs.mkdir(parents=True)
    (theirs / "Info.plist").write_text("<string>org.example.datalab</string>")
    done = uninstall(machine)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (theirs / "Info.plist").is_file()
    assert not (machine["apps"] / "DataLab.app").exists()


# ------------------------------------------------------------ Docker Desktop
#
# Docker Desktop, its disk image and macOS's tools are stand-ins too: `open`
# "starts" Docker by writing to a state file that the fake `docker` reads,
# `hdiutil` "mounts" a copy of a folder that stands in for the disk image
# (and "detaches" it by moving it aside), and `codesign`/`spctl` report the
# signer written in the fake app. `sleep` moves a fake clock (read by
# `date +%s`) on instead of waiting. `ps` lists Docker's programs as running
# for each Docker.app in the two Applications folders, `mdfind` finds what a
# test says, and `ditto` can be made to fail. The real Docker Desktop is never
# used.

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$DATALAB_TEST_DOCKERLOG"
if [ "$1" = desktop ]; then
  [ -z "${DATALAB_TEST_NO_DESKTOP_COMMAND:-}" ] || { echo "unknown command" >&2; exit 1; }
  case "$*" in *--help*) exit 0 ;; esac
  echo restarted > "$DATALAB_TEST_STATE.restarted"
  exit 0
fi
if [ "$1" = info ]; then
  # A stuck engine ("500 Internal Server Error"): answers only after a
  # restart ("stuck"), or never ("dead").
  case "${DATALAB_TEST_ENGINE:-}" in
    dead) exit 1 ;;
    stuck) [ -f "$DATALAB_TEST_STATE.restarted" ] || exit 1 ;;
  esac
  calls=$(( $(cat "$DATALAB_TEST_STATE.calls" 2>/dev/null || echo 0) + 1 ))
  echo "$calls" > "$DATALAB_TEST_STATE.calls"
  [ "$(cat "$DATALAB_TEST_STATE" 2>/dev/null)" = running ] || exit 1
  [ "$calls" -ge "${DATALAB_TEST_READY_AFTER:-0}" ] || exit 1
fi
exit 0
"""

DOCKER_TOOLS = {
    "open": """#!/bin/sh
echo "open $*" >> "$DATALAB_TEST_OPENLOG"
case "$1" in
  *.app)
    [ -z "${DATALAB_TEST_OPEN_FAILS:-}" ] || exit 1
    if [ "${DATALAB_TEST_OPEN_STARTS:-1}" = 1 ]; then echo running > "$DATALAB_TEST_STATE"; fi ;;
esac
exit 0
""",
    # Not an administrator unless a test says so.
    "id": """#!/bin/sh
case "$1" in
  -Gn) echo "${DATALAB_TEST_GROUPS:-staff everyone}" ;;
  -un) echo tester ;;
  *) exec /usr/bin/id "$@" ;;
esac
""",
    # Never asks for a password: logs the command, then runs it (or is "cancelled").
    "sudo": """#!/bin/sh
echo "sudo $*" >> "$DATALAB_TEST_SUDOLOG"
if [ -n "${DATALAB_TEST_SUDO_CANCEL:-}" ]; then echo "sudo: a password is required" >&2; exit 1; fi
exec "$@"
""",
    # plutil -extract <key> raw -o - <plist>, for the fake apps' one-line keys.
    "plutil": """#!/bin/sh
for file; do :; done
value="$(sed -n "s|.*<key>$2</key><string>\\(.*\\)</string>.*|\\1|p" "$file" 2>/dev/null)"
value="${value%%
*}"
[ -n "$value" ] || exit 1
printf '%s\\n' "$value"
""",
    "ps": """#!/bin/sh
[ -z "${DATALAB_TEST_NOT_ALIVE:-}" ] || exit 0
for app in "$DATALAB_SYSTEM_APPLICATIONS/Docker.app" "$HOME/Applications/Docker.app" \\
  ${DATALAB_TEST_ALIVE:+"$DATALAB_TEST_ALIVE"}; do
  echo "$app/Contents/MacOS/com.docker.backend"
done
""",
    "sleep": """#!/bin/sh
echo $(( $(cat "$DATALAB_TEST_CLOCK" 2>/dev/null || echo 1000) + $1 )) > "$DATALAB_TEST_CLOCK"
""",
    "date": """#!/bin/sh
[ "$1" = +%s ] || exec /bin/date "$@"
cat "$DATALAB_TEST_CLOCK" 2>/dev/null || echo 1000
""",
    "mdfind": '#!/bin/sh\necho "mdfind $*" >> "$DATALAB_TEST_MDFINDLOG"\n'
    '[ -z "${DATALAB_TEST_MDFIND:-}" ] || printf \'%s\\n\' "$DATALAB_TEST_MDFIND"\n',
    "ditto": """#!/bin/sh
case "${DATALAB_TEST_DITTO:-}" in
  interrupt) mkdir -p "$2/Contents"; echo half > "$2/Contents/half"; kill -INT "$PPID"; exit 1 ;;
  not-permitted) echo "ditto: $2: Operation not permitted" >&2; exit 1 ;;
  fails) echo "ditto: $2: No space left on device" >&2; exit 1 ;;
  tamper) cp -R "$1" "$2" && echo "Other (ABCDE12345)" > "$2/Contents/fake-signer" ;;
  race) cp -R "$1" "$2" && mkdir -p "$(dirname "$2")/Docker.app/Contents" ;;
  *) exec cp -R "$1" "$2" ;;  # what ditto does here, on any system
esac
""",
    "sysctl": """#!/bin/sh
case "$*" in *hw.optional.arm64*) echo "${DATALAB_TEST_ARM64:-1}" ;; *) exit 1 ;; esac
""",
    "uname": """#!/bin/sh
if [ "$1" = -m ]; then echo "${DATALAB_TEST_MACHINE:-arm64}"; else /usr/bin/uname "$@"; fi
""",
    "sw_vers": '#!/bin/sh\necho "${DATALAB_TEST_MACOS:-15.1}"\n',
    "df": """#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/disk3s5 900000000 1 ${DATALAB_TEST_FREE_KB:-104857600} 1% /"
""",
    # Docker's download: a stand-in disk image, or a failure.
    "curl": """#!/bin/sh
echo "curl $*" >> "$DATALAB_TEST_CURLLOG"
case "$*" in
  *desktop.docker.com*) ;;
  *) echo 'no downloads in tests' >&2; exit 1 ;;
esac
while [ $# -gt 0 ]; do
  if [ "$1" = -o ]; then out="$2"; fi
  shift
done
if [ -n "${DATALAB_TEST_CURL_FAILS:-}" ]; then
  echo partial >> "$out"
  exit "$DATALAB_TEST_CURL_FAILS"
fi
echo "${DATALAB_TEST_DMG:-image}" >> "$out"
""",
    "hdiutil": """#!/bin/sh
echo "hdiutil $*" >> "$DATALAB_TEST_HDIUTILLOG"
case "$1" in
  attach)
    while [ $# -gt 0 ]; do
      if [ "$1" = -mountpoint ]; then mount="$2"; fi
      last="$1"; shift
    done
    if grep -q corrupt "$last"; then exit 1; fi
    cp -R "$DATALAB_TEST_DMG_SOURCE/." "$mount/" ;;
  detach)
    for mount; do :; done
    if [ -d "$mount" ]; then mv "$mount" "$(mktemp -d "$DATALAB_TEST_DETACHED/m.XXXXXX")/"; fi ;;
esac
""",
    "codesign": """#!/bin/sh
for app; do :; done
if [ -f "$app" ]; then
  signer="${DATALAB_TEST_DMG_SIGNER:-Docker Inc (9BNSXJN65R)}"  # the disk image
else
  signer="$(cat "$app/Contents/fake-signer" 2>/dev/null)" || exit 1
fi
case "$*" in
  *'-R=anchor apple generic and certificate leaf[subject.OU] = "9BNSXJN65R"'*)
    case "$signer" in *"(9BNSXJN65R)") ;; *) exit 3 ;; esac ;;
  *-R=*) exit 3 ;;
esac
case "$1" in
  --verify) [ "$signer" != broken ] || exit 1 ;;
  -dv) echo "Identifier=com.docker.docker" >&2
       echo "TeamIdentifier=${signer##*(}" | tr -d ')' >&2 ;;
esac
""",
    "spctl": """#!/bin/sh
for app; do :; done
signer="$(cat "$app/Contents/fake-signer" 2>/dev/null)" || exit 3
if [ -n "${DATALAB_TEST_SPCTL_REJECTS:-}" ]; then echo "$app: rejected" >&2; exit 3; fi
if [ -n "${DATALAB_TEST_GATEKEEPER_OFF:-}" ]; then
  printf '%s: accepted\\noverride=security disabled\\n' "$app" >&2; exit 0
fi
printf '%s: accepted\\nsource=Notarized Developer ID\\norigin=Developer ID Application: %s\\n' \\
  "$app" "$signer" >&2
""",
}

FAKE_DOCKER_INSTALL = """#!/bin/sh
echo "install $*" >> "$DATALAB_TEST_SUDOLOG"
[ -z "${DATALAB_TEST_INSTALL_FAILS:-}" ] || exit 3
app="$(cd "$(dirname "$0")/../.." && pwd)"
cp -R "$app" "$DATALAB_SYSTEM_APPLICATIONS/Docker.app"
"""

DOCKER_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.docker.docker</string>
  <key>CFBundleExecutable</key><string>com.docker.backend</string>
  <key>CFBundleShortVersionString</key><string>4.93.0</string>
  <key>LSMinimumSystemVersion</key><string>{minimum}</string>
</dict></plist>
"""


def fake_docker_app(where: Path, *, signer: str = "Docker Inc (9BNSXJN65R)") -> Path:
    app = where / "Docker.app"
    (app / "Contents" / "Resources" / "bin").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(DOCKER_PLIST.format(minimum="14.0"))
    (app / "Contents" / "fake-signer").write_text(signer)
    executable(app / "Contents" / "Resources" / "bin" / "docker", FAKE_DOCKER)
    (app / "Contents" / "MacOS").mkdir()
    executable(app / "Contents" / "MacOS" / "com.docker.backend", "#!/bin/sh\n")
    return app


@pytest.fixture
def mac(machine, tmp_path) -> dict[str, Path]:
    """A Mac without Docker Desktop: no docker on PATH, none installed."""
    (machine["tools"] / "docker").unlink()
    for name, text in DOCKER_TOOLS.items():
        executable(machine["tools"] / name, text)
    dmg = tmp_path / "dmg-contents"
    dmg.mkdir()
    # Docker's own installer: puts the app it's in into /Applications.
    install_command = fake_docker_app(dmg) / "Contents" / "MacOS" / "install"
    executable(install_command, FAKE_DOCKER_INSTALL)
    detached = tmp_path / "detached"
    detached.mkdir()
    names = ("open", "curl", "hdiutil", "docker", "path", "mdfind", "sudo")
    logs = {name: tmp_path / f"{name}.log" for name in names}
    return {**machine, "dmg": dmg, "detached": detached, "state": tmp_path / "docker-state", **logs}


def docker_install(mac, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return install(
        mac,
        "0.1.0a3",
        *args,
        DATALAB_TEST_STATE=str(mac["state"]),
        DATALAB_TEST_OPENLOG=str(mac["open"]),
        DATALAB_TEST_CURLLOG=str(mac["curl"]),
        DATALAB_TEST_HDIUTILLOG=str(mac["hdiutil"]),
        DATALAB_TEST_DOCKERLOG=str(mac["docker"]),
        DATALAB_TEST_PATHLOG=str(mac["path"]),
        DATALAB_TEST_DMG_SOURCE=str(mac["dmg"]),
        DATALAB_TEST_DETACHED=str(mac["detached"]),
        DATALAB_TEST_MDFINDLOG=str(mac["mdfind"]),
        DATALAB_TEST_SUDOLOG=str(mac["sudo"]),
        DATALAB_TEST_CLOCK=str(mac["state"].with_name("clock")),
        DATALAB_DOCKER_POLL_SECONDS="5",
        **env,
    )


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def cache(mac) -> Path:
    return mac["home"] / "Library" / "Caches" / "DataLab" / "docker-desktop"


def test_docker_already_running_is_used_as_it_is(mac):
    executable(mac["tools"] / "docker", FAKE_DOCKER)
    mac["state"].write_text("running")
    done = docker_install(mac)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Docker Desktop is running." in done.stdout
    assert lines(mac["open"]) == [] and lines(mac["curl"]) == []
    assert "isn't on your PATH" not in done.stdout
    assert "7/7 Launcher" in done.stdout


def test_docker_installed_but_stopped_is_started_and_waited_for(mac):
    app = fake_docker_app(mac["apps"])
    (app / "Contents" / "mine").write_text("my settings")
    executable(mac["tools"] / "docker", FAKE_DOCKER)
    done = docker_install(mac, DATALAB_TEST_READY_AFTER="10")
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["open"]) == [f"open {app}"]
    assert "Starting Docker Desktop" in done.stdout
    assert "Still waiting for Docker Desktop (30 seconds)" in done.stdout
    assert "Docker Desktop is running." in done.stdout
    # Nothing downloaded or reinstalled; the app is as it was.
    assert lines(mac["curl"]) == [] and lines(mac["hdiutil"]) == []
    assert (app / "Contents" / "mine").read_text() == "my settings"
    assert "Docker Subscription Service Agreement" not in done.stdout
    assert "7/7 Launcher" in done.stdout


@pytest.mark.parametrize("where", ["system", "user"])
def test_docker_whose_command_isnt_on_path_is_found_in_the_app(mac, where):
    folder = mac["apps"] if where == "system" else mac["home"] / "Applications"
    folder.mkdir(exist_ok=True)
    app = fake_docker_app(folder)
    mac["state"].write_text("running")
    done = docker_install(mac)
    assert done.returncode == 0, done.stdout + done.stderr
    bin_dir = app / "Contents" / "Resources" / "bin"
    assert f"DataLab finds it in {bin_dir}" in done.stdout
    assert lines(mac["open"]) == []
    # The rest of the install (pulling images, setup) finds it on PATH.
    assert lines(mac["path"]) and all(
        str(bin_dir) in line.split(":") for line in lines(mac["path"])
    )
    assert "docker info" in lines(mac["docker"])


def test_docker_stopped_with_its_command_off_path_is_started(mac):
    app = fake_docker_app(mac["apps"])
    done = docker_install(mac)
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["open"]) == [f"open {app}"]
    assert "DataLab finds it in" in done.stdout


def test_the_command_in_your_docker_folder_is_found(mac):
    folder = mac["home"] / ".docker" / "bin"
    folder.mkdir(parents=True)
    executable(folder / "docker", FAKE_DOCKER)
    mac["state"].write_text("running")
    done = docker_install(mac)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"DataLab finds it in {folder}" in done.stdout


def test_a_fresh_mac_gets_docker_desktop_downloaded_checked_and_installed(mac):
    done = docker_install(mac, "--install-docker")
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    [curl] = lines(mac["curl"])
    assert "https://desktop.docker.com/mac/main/arm64/Docker.dmg" in curl
    assert "--proto =https" in curl and "-C -" in curl
    assert "for this Mac (Apple silicon)" in out
    assert "this installer doesn't accept it for you" in out
    assert "your organisation may have its" in out
    assert "It's Docker Desktop 4.93.0, signed and notarized by Docker Inc." in out
    app = mac["apps"] / "Docker.app"
    assert (app / "Contents" / "fake-signer").is_file()
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()
    attach, detach = lines(mac["hdiutil"])
    assert attach.startswith("hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint ")
    assert detach.startswith("hdiutil detach")
    assert not cache(mac).exists()  # the download is deleted
    # First launch: the person accepts Docker's agreement in Docker's window.
    assert lines(mac["open"]) == [f"open {app}"]
    assert "It shows the Docker Subscription Service Agreement. Read it and choose" in out
    assert 'Choose "Use recommended settings", then Finish.' in out
    assert "choose Skip" in out
    assert "7/7 Launcher" in out
    # Run again: it's found, not downloaded again.
    again = docker_install(mac, "--install-docker")
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(lines(mac["curl"])) == 1


@pytest.mark.parametrize(
    ("arm64", "machine_name", "url"),
    [("0", "x86_64", "amd64"), ("1", "x86_64", "arm64")],  # Intel; Apple silicon under Rosetta
)
def test_the_download_matches_the_processor(mac, arm64, machine_name, url):
    done = docker_install(
        mac, "--install-docker", DATALAB_TEST_ARM64=arm64, DATALAB_TEST_MACHINE=machine_name
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"/mac/main/{url}/Docker.dmg" in lines(mac["curl"])[0]


def test_without_rights_to_applications_docker_goes_in_yours(mac):
    mac["apps"].chmod(0o555)
    try:
        done = docker_install(mac, "--install-docker")
    finally:
        mac["apps"].chmod(0o755)
    assert done.returncode == 0, done.stdout + done.stderr
    app = mac["home"] / "Applications" / "Docker.app"
    assert (app / "Contents" / "fake-signer").is_file()
    assert lines(mac["open"])[0] == f"open {app}"
    assert 'the command line tools to "User"' in done.stdout


def refused(done: subprocess.CompletedProcess[str], mac, *phrases: str) -> None:
    assert done.returncode == 1, done.stdout + done.stderr
    for phrase in phrases:
        assert phrase in done.stdout
    assert "run this installer again" in done.stdout
    assert not (mac["apps"] / "Docker.app").exists()
    assert not (mac["home"] / "Applications" / "Docker.app").exists()
    assert "2/7 uv" not in done.stdout  # it stops at the Docker step


def test_saying_no_changes_nothing_and_running_again_carries_on(mac):
    done = docker_install(mac)  # no terminal to answer: no
    refused(done, mac, "Download and install Docker Desktop? [y/N]", "wasn't installed")
    assert "drag Docker to Applications" in done.stdout
    assert lines(mac["curl"]) == [] and not cache(mac).exists()
    again = docker_install(mac, "--install-docker")
    assert again.returncode == 0, again.stdout + again.stderr


def test_an_unsupported_mac_is_told_so(mac):
    done = docker_install(
        mac, "--install-docker", DATALAB_TEST_ARM64="0", DATALAB_TEST_MACHINE="i386"
    )
    assert done.returncode == 1 and "isn't one Docker Desktop supports" in done.stdout
    assert lines(mac["curl"]) == []


def test_an_old_macos_is_told_to_update_first(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_MACOS="13.6.1")
    refused(done, mac, "needs macOS 14.0 or newer, and this Mac has macOS 13.6.1")
    assert "Software Update" in done.stdout and lines(mac["curl"]) == []


def test_a_macos_older_than_the_download_needs_stops_before_installing(mac):
    plist = mac["dmg"] / "Docker.app" / "Contents" / "Info.plist"
    plist.write_text(DOCKER_PLIST.format(minimum="15.0"))
    done = docker_install(mac, "--install-docker", DATALAB_TEST_MACOS="14.7")
    refused(done, mac, "This Docker Desktop needs macOS 15.0 or newer")
    assert not (cache(mac) / "Docker-arm64.dmg").exists()


def test_not_enough_disk_space_is_said_before_downloading(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_FREE_KB=str(2 * 1024 * 1024))
    refused(done, mac, "about 6 GB of free disk space", "this Mac has 2 GB free")
    assert lines(mac["curl"]) == []


def test_a_failed_download_is_resumed_next_time(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_CURL_FAILS="56")
    refused(done, mac, "didn't finish (curl stopped with code 56)", "continues the download")
    part = cache(mac) / "Docker-arm64.dmg.part"
    assert part.read_text() == "partial\n"
    again = docker_install(mac, "--install-docker")
    assert again.returncode == 0, again.stdout + again.stderr
    assert all("-C -" in line and str(part) in line for line in lines(mac["curl"]))


def test_a_download_that_cant_be_resumed_starts_afresh_next_time(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_CURL_FAILS="22")
    refused(done, mac, "curl stopped with code 22")
    assert not (cache(mac) / "Docker-arm64.dmg.part").exists()


def test_a_damaged_download_is_deleted(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DMG="corrupt")
    refused(done, mac, "couldn't be opened", "downloads it again")
    assert list(cache(mac).iterdir()) == []


@pytest.mark.parametrize(
    ("signer", "env"),
    [
        ("Someone Else (ABCDE12345)", {}),  # another developer
        ("broken", {}),  # a signature that doesn't verify
        ("Docker Inc (9BNSXJN65R)", {"DATALAB_TEST_SPCTL_REJECTS": "1"}),  # not notarized
    ],
)
def test_a_download_not_signed_by_docker_is_refused(mac, signer, env):
    (mac["dmg"] / "Docker.app" / "Contents" / "fake-signer").write_text(signer)
    done = docker_install(mac, "--install-docker", **env)
    refused(done, mac, "didn't pass macOS's checks", "team 9BNSXJN65R", "Nothing from it was run")
    assert list(cache(mac).iterdir()) == []
    assert lines(mac["hdiutil"])[-1].startswith("hdiutil detach")
    assert lines(mac["open"]) == []


def test_a_blocked_copy_says_to_ask_it(mac):
    user_apps = mac["home"] / "Applications"
    mac["apps"].chmod(0o555)
    user_apps.mkdir(mode=0o555)
    try:
        done = docker_install(mac, "--install-docker")
    finally:
        mac["apps"].chmod(0o755)
        user_apps.chmod(0o755)
    refused(done, mac, "couldn't be copied to", "Self Service app or ask IT")
    assert not (user_apps / ".Docker.app.datalab-partial").exists()
    assert lines(mac["hdiutil"])[-1].startswith("hdiutil detach")


def test_a_copy_left_half_done_is_replaced(mac):
    leftover = mac["apps"] / ".Docker.app.datalab-partial"
    leftover.mkdir()
    (leftover / "half").write_text("")
    done = docker_install(mac, "--install-docker")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not leftover.exists() and (mac["apps"] / "Docker.app").is_dir()


def test_declining_dockers_agreement_is_explained(mac):
    done = docker_install(
        mac, "--install-docker", DATALAB_TEST_OPEN_STARTS="0", DATALAB_TEST_NOT_ALIVE="1"
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "closed before it was ready. If you declined its agreement" in done.stdout
    assert "run this installer again" in done.stdout
    # Docker Desktop stays installed; running again just starts it.
    app = mac["apps"] / "Docker.app"
    again = docker_install(mac)
    assert again.returncode == 0, again.stdout + again.stderr
    assert lines(mac["open"])[-1] == f"open {app}" and len(lines(mac["curl"])) == 1


def test_docker_that_doesnt_start_in_time_is_explained(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_OPEN_STARTS="0", DATALAB_DOCKER_WAIT_SECONDS="60")
    assert done.returncode == 1, done.stdout + done.stderr
    # Its programs are running, so it's the engine that isn't answering.
    assert "its engine isn't answering after 60 seconds" in done.stdout
    assert "whale menu at the top of the screen > Restart" in done.stdout
    assert "Still waiting for Docker Desktop (30 seconds)" in done.stdout
    usual = docker_install(mac, DATALAB_TEST_OPEN_STARTS="0")
    assert "isn't answering after 5 minutes" in usual.stdout


def test_docker_that_cant_be_opened_is_explained(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_OPEN_FAILS="1")
    assert done.returncode == 1 and "couldn't be opened" in done.stdout


def test_another_docker_command_that_isnt_answering_is_mentioned(mac):
    executable(mac["tools"] / "docker", FAKE_DOCKER)
    done = docker_install(mac)
    refused(done, mac, "isn't answering, and Docker", "(Colima, OrbStack)")


def test_it_can_stop_after_the_docker_step(mac):
    mac["state"].write_text("running")
    executable(mac["tools"] / "docker", FAKE_DOCKER)
    done = docker_install(mac, DATALAB_INSTALL_STOP_AFTER_DOCKER="1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Stopping here" in done.stdout and "2/7 uv" not in done.stdout
    assert not root(mac).exists() and not mac["log"].exists()


def test_the_installer_never_resets_prunes_or_accepts_for_you():
    text = INSTALLER.read_text()
    for never in ("prune", "docker rm", "--accept-license", "group.com.docker"):
        assert never not in text


def test_ctrl_c_while_copying_leaves_no_half_copy(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DITTO="interrupt")
    assert done.returncode == 130, done.stdout + done.stderr
    assert "Stopped. Run this installer again" in done.stdout
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()
    assert not (mac["apps"] / "Docker.app").exists()
    assert lines(mac["hdiutil"])[-1].startswith("hdiutil detach")
    again = docker_install(mac, "--install-docker")
    assert again.returncode == 0, again.stdout + again.stderr


def test_app_management_blocking_the_copy_says_how_to_allow_it(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DITTO="not-permitted")
    refused(done, mac, '"Operation not permitted"', "Privacy & Security > App Management")
    assert "Self Service" not in done.stdout
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()


def test_another_copy_failure_gets_the_general_message(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DITTO="fails")
    refused(done, mac, "couldn't be copied to", "Self Service app or ask IT")


def test_a_copy_that_differs_from_what_was_checked_is_removed(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DITTO="tamper")
    refused(done, mac, "didn't pass macOS's checks again")
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()
    assert list(cache(mac).iterdir()) == []


def test_a_docker_app_that_appears_while_copying_is_left_alone(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_DITTO="race")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "appeared in" in done.stdout and "that one was" in done.stdout
    theirs = mac["apps"] / "Docker.app"
    assert [p.name for p in theirs.iterdir()] == ["Contents"]  # not a copy inside it
    assert list((theirs / "Contents").iterdir()) == []
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()


def test_a_disk_image_not_signed_by_docker_isnt_opened(mac):
    done = docker_install(
        mac, "--install-docker", DATALAB_TEST_DMG_SIGNER="Someone Else (ABCDE12345)"
    )
    refused(done, mac, "isn't signed by Docker Inc", "deleted without being opened")
    assert lines(mac["hdiutil"]) == [] and list(cache(mac).iterdir()) == []


def test_gatekeeper_turned_off_is_said_so(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_GATEKEEPER_OFF="1")
    refused(done, mac, "Gatekeeper is turned off", "Nothing from it was run")
    assert list(cache(mac).iterdir()) == []


def test_a_symlinked_app_in_the_disk_image_is_refused(mac, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (mac["dmg"] / "Docker.app").rename(real / "Docker.app")
    (mac["dmg"] / "Docker.app").symlink_to(real / "Docker.app")
    done = docker_install(mac, "--install-docker")
    refused(done, mac, "didn't pass macOS's checks")


def test_docker_linked_from_path_to_an_app_elsewhere_is_used(mac, tmp_path):
    elsewhere = mac["apps"] / "Utilities"
    elsewhere.mkdir()
    renamed = fake_docker_app(elsewhere).rename(elsewhere / "Docker 4.84.app")
    (mac["tools"] / "docker").symlink_to(renamed / "Contents" / "Resources" / "bin" / "docker")
    done = docker_install(mac, DATALAB_TEST_ALIVE=str(renamed))
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["open"]) == [f"open {renamed}"]
    assert lines(mac["curl"]) == [] and not (mac["apps"] / "Docker.app").exists()
    assert "isn't on your PATH" not in done.stdout


def test_a_docker_app_spotlight_knows_is_used(mac, tmp_path):
    elsewhere = mac["home"] / "Applications" / "Other Apps"
    elsewhere.mkdir(parents=True)
    app = fake_docker_app(elsewhere)
    trash = mac["home"] / ".Trash"
    trash.mkdir()
    old = fake_docker_app(trash)
    done = docker_install(mac, DATALAB_TEST_MDFIND=f"{old}\n{app}", DATALAB_TEST_ALIVE=str(app))
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["mdfind"]) == ["mdfind kMDItemCFBundleIdentifier == 'com.docker.docker'"]
    assert lines(mac["open"]) == [f"open {app}"]  # not the one in the Trash
    assert lines(mac["curl"]) == []
    assert f"DataLab finds it in {app / 'Contents' / 'Resources' / 'bin'}" in done.stdout


def test_an_empty_leftover_docker_app_is_explained(mac):
    (mac["apps"] / "Docker.app" / "Contents").mkdir(parents=True)
    done = docker_install(mac)
    assert done.returncode == 1, done.stdout + done.stderr
    assert (
        "isn't a complete Docker Desktop" in done.stdout and "Drag it to the Trash" in done.stdout
    )
    assert lines(mac["open"]) == [] and lines(mac["curl"]) == []


def test_after_installing_its_own_docker_command_is_used(mac):
    other = mac["tools"] / "docker"  # another Docker's command, not answering
    executable(other, "#!/bin/sh\nexit 1\n")
    done = docker_install(mac, "--install-docker")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "docker info" in lines(mac["docker"])  # the installed app's command answered
    bin_dir = mac["apps"] / "Docker.app" / "Contents" / "Resources" / "bin"
    assert all(line.split(":")[-1] == str(bin_dir) for line in lines(mac["path"]))


def test_the_wait_is_timed_by_the_clock(mac):
    fake_docker_app(mac["apps"])
    # Each `docker info` "takes" 50 seconds: the 60-second wait ends after two.
    slow = FAKE_DOCKER.replace(
        'if [ "$1" = info ]; then',
        'if [ "$1" = info ]; then\n  sleep 50',
    )
    executable(mac["apps"] / "Docker.app" / "Contents" / "Resources" / "bin" / "docker", slow)
    done = docker_install(mac, DATALAB_TEST_OPEN_STARTS="0", DATALAB_DOCKER_WAIT_SECONDS="60")
    assert done.returncode == 1 and "isn't answering after 60 seconds" in done.stdout
    assert len([x for x in lines(mac["docker"]) if x == "docker info"]) <= 3


def test_its_checks_use_dockers_code_requirement_and_notarization():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'certificate leaf[subject.OU] = \\"$DOCKER_TEAM_ID\\"' in text
    assert 'has_line "$assessed" "source=Notarized Developer ID"' in text
    assert "--speed-limit 10240 --speed-time 120" in text and "--tlsv1.2" in text


def staging_copy(mac) -> Path:
    """Docker's own half-done install or uninstall, with its programs running."""
    staging = mac["home"] / "Library" / "Application Support" / "com.docker.install"
    (staging / "in_progress").mkdir(parents=True)
    return fake_docker_app(staging / "in_progress")


def test_a_half_done_docker_install_isnt_taken_for_one(mac):
    app = staging_copy(mac)
    done = docker_install(mac, DATALAB_TEST_MDFIND=str(app), DATALAB_TEST_ALIVE=str(app))
    refused(done, mac, "Download and install Docker Desktop? [y/N]")
    assert lines(mac["open"]) == []
    again = docker_install(mac, "--install-docker", DATALAB_TEST_MDFIND=str(app))
    assert again.returncode == 0, again.stdout + again.stderr
    assert lines(mac["open"]) == [f"open {mac['apps'] / 'Docker.app'}"]


def test_a_docker_command_linked_into_a_half_done_install_isnt_followed(mac):
    app = staging_copy(mac)
    (mac["tools"] / "docker").symlink_to(app / "Contents" / "Resources" / "bin" / "docker")
    done = docker_install(mac)
    refused(done, mac, "isn't answering", "Download and install Docker Desktop? [y/N]")
    assert lines(mac["open"]) == []


def test_spotlight_finding_only_leftovers_means_an_install_is_offered(mac, tmp_path):
    places = [
        mac["home"] / ".Trash",
        mac["home"] / "Library" / "Caches" / "x",
        tmp_path / "Volumes-like" / "Docker",  # not in an Applications folder
        mac["home"] / "Downloads",
    ]
    found = []
    for place in places:
        place.mkdir(parents=True)
        found.append(str(fake_docker_app(place)))
    done = docker_install(mac, DATALAB_TEST_MDFIND="\n".join(found))
    refused(done, mac, "Download and install Docker Desktop? [y/N]")
    assert lines(mac["open"]) == []


def test_leftover_docker_programs_are_mentioned_when_it_doesnt_start(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_OPEN_STARTS="0", DATALAB_DOCKER_WAIT_SECONDS="60")
    assert "programs\n    from it may still be running" in done.stdout
    assert "restart the Mac" in done.stdout


def test_no_variable_runs_into_a_non_ascii_character():
    # In a UTF-8 locale, sh reads "$place…" as a variable named place plus
    # the first byte of "…", which set -u stops on. ${place}… is safe.
    for script in (INSTALLER, UNINSTALLER):
        text = script.read_bytes()
        assert not re.search(rb"\$[A-Za-z_][A-Za-z0-9_]*[\x80-\xff]", text), script.name


def test_a_fresh_install_works_in_a_utf8_locale(mac):
    done = docker_install(mac, "--install-docker", LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Copying Docker Desktop to" in done.stdout and "unbound variable" not in done.stderr


# Like Docker's engine hanging: `docker info` never returns, and (like a Go
# program) it ignores SIGALRM and SIGTERM. Only SIGKILL stops it.
HUNG_DOCKER = """#!/bin/sh
echo "docker $*" >> "$DATALAB_TEST_DOCKERLOG"
trap '' ALRM TERM
[ "$1" = info ] && exec /bin/sleep 1017
exit 0
"""


@pytest.mark.parametrize("way", ["perl", "sh"])
def test_a_hung_docker_engine_is_given_up_on(mac, way):
    app = fake_docker_app(mac["apps"])
    executable(app / "Contents" / "Resources" / "bin" / "docker", HUNG_DOCKER)
    started = time.monotonic()
    done = docker_install(
        mac,
        DATALAB_TEST_WITHIN=way,
        DATALAB_DOCKER_INFO_SECONDS="1",
        DATALAB_DOCKER_WAIT_SECONDS="10",
    )
    took = time.monotonic() - started
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is open, but its engine isn't answering after 10 seconds" in done.stdout
    assert "whale menu at the top of the screen > Restart" in done.stdout
    assert "Troubleshoot > Restart" in done.stdout and "DELETE Docker's containers" in done.stdout
    # Each call was stopped after about 1 + 2 seconds, not left hanging.
    calls = [line for line in lines(mac["docker"]) if line == "docker info"]
    assert 2 <= len(calls) <= 5 and took < 40
    running = subprocess.run(["ps", "-A", "-o", "args="], capture_output=True, text=True).stdout
    assert "sleep 1017" not in running


def restarts(mac) -> list[str]:
    return [line for line in lines(mac["docker"]) if line == "docker desktop restart"]


def test_a_stuck_engine_is_restarted_once_and_then_answers(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_ENGINE="stuck")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "engine isn't answering; restarting it" in done.stdout
    assert restarts(mac) == ["docker desktop restart"]
    assert "Docker Desktop is running." in done.stdout


def test_a_dead_engine_is_restarted_only_once(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_ENGINE="dead")
    assert done.returncode == 1, done.stdout + done.stderr
    assert len(restarts(mac)) == 1
    assert "is open, but its engine isn't answering after 5 minutes" in done.stdout


def test_a_first_run_is_never_restarted(mac):
    done = docker_install(
        mac, "--install-docker", DATALAB_TEST_ENGINE="dead", DATALAB_DOCKER_WAIT_SECONDS="300"
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert restarts(mac) == [] and "restarting it" not in done.stdout
    assert "isn't ready after 5 minutes" in done.stdout  # its window may be waiting


def test_an_older_docker_without_its_restart_command_isnt_restarted(mac):
    fake_docker_app(mac["apps"])
    done = docker_install(mac, DATALAB_TEST_ENGINE="dead", DATALAB_TEST_NO_DESKTOP_COMMAND="1")
    assert done.returncode == 1, done.stdout + done.stderr
    assert restarts(mac) == [] and "restarting it" not in done.stdout


def test_no_printf_or_echo_is_piped_into_grep():
    # grep -q in a pipe exits early: "printf: write error: Broken pipe".
    text = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r"(printf|echo)[^\n|]*\|\s*grep", text)


ADMIN = {"DATALAB_TEST_GROUPS": "staff everyone admin"}


def test_an_administrator_gets_dockers_own_installer(mac):
    done = docker_install(mac, "--install-docker", **ADMIN)
    assert done.returncode == 0, done.stdout + done.stderr
    [sudo, install] = lines(mac["sudo"])
    assert sudo.startswith("sudo ") and sudo.endswith(
        "/Docker.app/Contents/MacOS/install --user tester"
    )
    assert install == "install --user tester"  # no --accept-license
    assert "macOS asks for your password once" in done.stdout
    assert "macOS asks for your" in done.stdout and "to install Docker Desktop" in done.stdout
    app = mac["apps"] / "Docker.app"
    assert (app / "Contents" / "fake-signer").is_file()
    assert lines(mac["open"]) == [f"open {app}"]
    assert "It shows the Docker Subscription Service Agreement" in done.stdout
    assert not (mac["apps"] / ".Docker.app.datalab-partial").exists()
    assert lines(mac["hdiutil"])[-1].startswith("hdiutil detach") and not cache(mac).exists()
    assert "7/7 Launcher" in done.stdout


def test_cancelling_the_password_stops_with_what_to_do(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_SUDO_CANCEL="1", **ADMIN)
    refused(done, mac, "password wasn't given or accepted", "Nothing was changed.")
    assert "Copying Docker Desktop" not in done.stdout  # nothing else tried
    assert lines(mac["open"]) == []
    assert lines(mac["hdiutil"])[-1].startswith("hdiutil detach")
    # The download is kept, and checked again next time.
    again = docker_install(mac, "--install-docker", **ADMIN)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "Using the Docker Desktop download from before." in again.stdout
    assert len(lines(mac["curl"])) == 1


def test_dockers_installer_failing_stops_with_what_to_do(mac):
    done = docker_install(mac, "--install-docker", DATALAB_TEST_INSTALL_FAILS="1", **ADMIN)
    refused(done, mac, "installer stopped (code 3)")


def test_what_dockers_installer_put_in_applications_is_checked(mac):
    installer = mac["dmg"] / "Docker.app" / "Contents" / "MacOS" / "install"
    executable(
        installer,
        FAKE_DOCKER_INSTALL
        + 'echo "Other (ABCDE12345)" > '
        + '"$DATALAB_SYSTEM_APPLICATIONS/Docker.app/Contents/fake-signer"\n',
    )
    done = docker_install(mac, "--install-docker", **ADMIN)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "didn't pass macOS's checks" in done.stdout and lines(mac["open"]) == []


def test_a_non_administrator_gets_a_copy_without_sudo(mac):
    done = docker_install(mac, "--install-docker")
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["sudo"]) == [] and "Copying Docker Desktop" in done.stdout


def test_without_dockers_install_command_an_administrator_gets_a_copy(mac):
    (mac["dmg"] / "Docker.app" / "Contents" / "MacOS" / "install").unlink()
    done = docker_install(mac, "--install-docker", **ADMIN)
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines(mac["sudo"]) == [] and "Copying Docker Desktop" in done.stdout


def test_sudo_is_only_ever_dockers_installer_without_accepting_its_license():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "accept-license" not in text and "sudo -S" not in text
    calls = [line.strip() for line in text.splitlines() if line.strip().startswith("sudo ")]
    assert calls == ['sudo "$installer_command" --user "$(id -un)" || status=$?']
