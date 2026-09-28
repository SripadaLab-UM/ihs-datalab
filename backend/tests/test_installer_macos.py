"""The macOS installer's steps, run for real against stand-ins.

`docker`, `uv`, `open` and `osascript` are small scripts on PATH, and the
"installed" DataLab is one too, logging what the installer asks of it.
Nothing is downloaded or installed, and HOME is a temporary folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from datalab.updater import Layout

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the macOS installer's shell")

INSTALLER = Path(__file__).resolve().parents[2] / "installer" / "macos" / "install.sh"

FAKE_DATALAB = """#!/bin/sh
echo "$*" >> "$DATALAB_TEST_LOG"
if [ -n "${DATALAB_TEST_WHO:-}" ]; then echo "$0" >> "$DATALAB_TEST_WHO"; fi
case "$*" in
  --version) echo "datalab $DATALAB_TEST_VERSION" ;;
  *"github sign-in"*) exit "${DATALAB_TEST_SIGNIN:-0}" ;;
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


@pytest.fixture
def machine(tmp_path) -> dict[str, Path]:
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
        "apps": system_apps,
        "tools": tools,
        "fake": fake,
        "packages": packages,
        "log": tmp_path / "log",
        "uvlog": tmp_path / "uvlog",
    }


def install(
    machine, version: str, *args: str, signin: int = 0, pinned: bool = True, **extra_env: str
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
        "PATH": f"{machine['tools']}:/usr/bin:/bin",
        "DATALAB_TEST_LOG": str(machine["log"]),
        "DATALAB_TEST_FAKE": str(machine["fake"]),
        "DATALAB_TEST_VERSION": version,
        "DATALAB_TEST_SIGNIN": str(signin),
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
        env={"HOME": str(machine["home"]), "PATH": f"{machine['tools']}:/usr/bin:/bin"},
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
    env = {**os.environ, "PATH": f"{machine['tools']}:/usr/bin:/bin", "DATALAB_TEST_OSA": str(osa)}
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
            "PATH": f"{machine['tools']}:/usr/bin:/bin",
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
            "PATH": f"{machine['tools']}:/usr/bin:/bin",
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
