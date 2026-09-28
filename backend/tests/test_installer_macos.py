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
    executable(tools / "osascript", "#!/bin/sh\nexit 0\n")
    executable(tools / "curl", "#!/bin/sh\necho 'no downloads in tests' >&2\nexit 1\n")
    executable(tools / "uv", FAKE_UV)
    fake = tmp_path / "fake-datalab"
    fake.write_text(FAKE_DATALAB)
    packages = tmp_path / "release"
    packages.mkdir()
    return {
        "home": home,
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


def test_uv_installs_only_what_requirements_txt_pins_by_hash(machine):
    done = install(machine, "0.1.0a3", UV_INDEX_URL="https://evil.example/simple")
    assert done.returncode == 0, done.stdout + done.stderr
    [pip] = machine["uvlog"].read_text().splitlines()
    assert (
        "pip install -q --no-config --require-hashes --only-binary :all: "
        "--default-index https://pypi.org/simple --python "
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
    apps = machine["home"] / "Applications"
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
