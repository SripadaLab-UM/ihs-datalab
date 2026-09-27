"""The macOS installer's steps, run for real against stand-ins.

`docker`, `uv`, `open` and `osascript` are small scripts on PATH, and the
"installed" DataLab is one too, logging what the installer asks of it.
Nothing is downloaded or installed, and HOME is a temporary folder.
"""

from __future__ import annotations

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
case "$*" in
  --version) echo "datalab $DATALAB_TEST_VERSION" ;;
  *"github sign-in"*) exit "${DATALAB_TEST_SIGNIN:-0}" ;;
esac
exit 0
"""

FAKE_UV = """#!/bin/sh
case "$1" in
  --version) echo "uv 0.12.19" ;;
  venv) for last; do :; done; mkdir -p "$last/bin"; : > "$last/bin/python" ;;
  pip)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--python" ]; then python="$2"; fi
      shift
    done
    cp "$DATALAB_TEST_FAKE" "$(dirname "$python")/datalab"
    chmod +x "$(dirname "$python")/datalab" ;;
esac
"""


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def machine(tmp_path) -> dict[str, Path]:
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    executable(tools / "docker", "#!/bin/sh\nexit 0\n")
    executable(tools / "open", "#!/bin/sh\nexit 0\n")
    executable(tools / "osascript", "#!/bin/sh\nexit 0\n")
    executable(tools / "curl", "#!/bin/sh\necho 'no downloads in tests' >&2\nexit 1\n")
    executable(tools / "uv", FAKE_UV)
    fake = tmp_path / "fake-datalab"
    fake.write_text(FAKE_DATALAB)
    packages = tmp_path / "release"
    packages.mkdir()
    (packages / "constraints.txt").write_text("")
    return {
        "home": home,
        "tools": tools,
        "fake": fake,
        "packages": packages,
        "log": tmp_path / "log",
    }


def install(machine, version: str, *args: str, signin: int = 0) -> subprocess.CompletedProcess[str]:
    package = machine["packages"] / f"datalab-{version}-py3-none-any.whl"
    package.write_text("")
    env = {
        "HOME": str(machine["home"]),
        "PATH": f"{machine['tools']}:/usr/bin:/bin",
        "DATALAB_TEST_LOG": str(machine["log"]),
        "DATALAB_TEST_FAKE": str(machine["fake"]),
        "DATALAB_TEST_VERSION": version,
        "DATALAB_TEST_SIGNIN": str(signin),
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
    launcher = machine["home"] / "Applications" / "DataLab.app" / "Contents" / "MacOS" / "DataLab"
    text = launcher.read_text()
    assert f'\\"{app}/bin/datalab\\" --profile real serve' in text
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
