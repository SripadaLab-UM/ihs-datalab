"""scripts/dev-copy.sh's settings step (scripts/dev_copy_settings.py): a dev
copy gets the lab's settings and nothing else of the real DataLab's."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_copy_settings.py"

REAL = """\
port = 8765
catalog_dir = "/somewhere/private"
agent_image = "registry.example/agent@sha256:abc"
default_model = "gpt-5.5"

[oracle]
host = "db.example.invalid"
port = 1521
service = "TEST"
user = "TEST_RO"
read_only_roles = ["TEST_READ"]
allowed_schemas = ["IHS_2025", "IHS_2026"]

[repos]
knowledge = "Lab/knowledge"
client_id = "Iv23testclient"
access_contact = "The \\"maintainer\\""

[dropbox]
app_key = "abcdefghij12"

[limits]
max_rows = 10
"""


def run(real: Path, dev: Path, port: int = 8775) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(real), str(dev), str(port)],
        capture_output=True,
        text=True,
    )


def test_a_dev_copy_gets_the_labs_settings_only(tmp_path):
    real = tmp_path / "real" / "settings.toml"
    real.parent.mkdir()
    real.write_text(REAL, encoding="utf-8")
    dev = tmp_path / "dev-real" / "settings.toml"
    assert run(real, dev).returncode == 0
    written = tomllib.loads(dev.read_text(encoding="utf-8"))
    original = tomllib.loads(REAL)
    for section in ("oracle", "repos", "dropbox"):
        assert written[section] == original[section]
    # Its own port, never an update check, and nothing else of the real DataLab's.
    assert written["port"] == 8775
    assert written["updates"] == {"check_on_start": False, "check_every_hour": False}
    assert set(written) == {"port", "oracle", "repos", "dropbox", "updates"}


def test_its_own_settings_are_kept_and_a_missing_real_install_is_said(tmp_path):
    dev = tmp_path / "dev-real" / "settings.toml"
    dev.parent.mkdir()
    dev.write_text("port = 9999\n", encoding="utf-8")
    assert run(tmp_path / "nothing.toml", dev).returncode == 0
    assert dev.read_text(encoding="utf-8") == "port = 9999\n"
    missing = run(tmp_path / "nothing.toml", tmp_path / "other" / "settings.toml")
    assert missing.returncode == 1 and "install DataLab first" in missing.stderr
