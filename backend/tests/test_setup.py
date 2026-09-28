import dataclasses

import pytest

import datalab.setup as installing
from datalab import credentials
from datalab.config import PRACTICE_ORACLE, Settings, load_settings

from .conftest import MemoryKeychain


def test_setup_saves_settings_where_datalab_reads_them(tmp_path, monkeypatch, keychain):
    # The profile and folder come from the environment, as for the app itself.
    monkeypatch.setenv("DATALAB_PROFILE", "practice")
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "custom"))
    lab = tmp_path / "lab.toml"
    lab.write_text("port = 8799\n", encoding="utf-8")

    assert installing.setup(None, lab, update=False) == 0

    assert (tmp_path / "custom" / "settings.toml").exists()
    settings = load_settings()
    assert settings.profile == "practice" and settings.port == 8799


def test_the_summary_never_calls_an_unverified_check_a_pass():
    from datalab.safety import CheckResult, SafetyReport
    from datalab.trial import summary

    ok = CheckResult("a", "p", "A", "pass")
    optional = CheckResult("b", "p", "B", "skip", required=False)
    unverified = CheckResult("c", "p", "The database is read-only", "skip")
    assert summary(SafetyReport("", results=[ok, optional]), strict=False) == "All checks passed."
    for strict in (False, True):
        line = summary(SafetyReport("", results=[ok, unverified]), strict=strict)
        assert "All checks passed" not in line and "The database is read-only" in line
    failed = SafetyReport("", results=[CheckResult("d", "p", "D", "fail")])
    assert summary(failed, strict=False) == "SOME CHECKS FAILED."


def test_a_failed_image_download_gets_the_hint_for_its_cause():
    from datalab.cli import _pull_failure_hint as hint

    not_allowed = [
        # Windows, not in docker-users (or not signed in again since being added)
        'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/'
        'images/create": open //./pipe/dockerDesktopLinuxEngine: Access is denied.',
        # Mac and Linux
        "permission denied while trying to connect to the Docker daemon socket at "
        'unix:///var/run/docker.sock: Post "http://%2Fvar%2Frun%2Fdocker.sock/v1.47/images/create"',
    ]
    not_running = [
        "error during connect: this error may indicate that the docker daemon is not running: "
        "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?",
    ]
    refused = [
        'Error response from daemon: Head "https://ghcr.io/v2/org/agent/manifests/sha256:ab": '
        "unauthorized",
        "Error response from daemon: pull access denied for ghcr.io/org/agent, repository does "
        "not exist or may require 'docker login': "
        "denied: requested access to the resource is denied",
    ]
    for error in not_allowed:
        assert "docker-users" in hint(error), error
    for error in not_running:
        assert "doesn't seem to be running" in hint(error), error
    for error in refused:
        assert "registry" in hint(error), error
    offline = (
        'Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: '
        "no such host"
    )
    assert "internet connection" in hint(offline)


def test_uninstalling_carries_on_when_docker_does_not_answer(monkeypatch, capsys):
    import subprocess

    calls = []

    def stuck(command, **options):
        calls.append((command, options.get("timeout")))
        raise subprocess.TimeoutExpired(command, options.get("timeout") or 0)

    monkeypatch.setattr(installing.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(installing.subprocess, "run", stuck)

    installing._docker_quiet("ps", "-aq", then="rm -f")  # returns instead of hanging

    assert calls == [(["docker", "ps", "-aq"], 30)]
    assert "Docker didn't answer" in capsys.readouterr().out


def test_uninstalling_removes_what_docker_lists_with_a_time_limit(monkeypatch):
    import subprocess

    calls = []

    def docker(command, **options):
        calls.append((command, options.get("timeout")))
        return subprocess.CompletedProcess(command, 0, stdout="a1\nb2\n" if len(calls) == 1 else "")

    monkeypatch.setattr(installing.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(installing.subprocess, "run", docker)

    installing._docker_quiet("ps", "-aq", then="rm -f")

    assert calls == [(["docker", "ps", "-aq"], 30), (["docker", "rm", "-f", "a1", "b2"], 120)]


@pytest.fixture
def keychain(monkeypatch, memory_keychain: MemoryKeychain) -> MemoryKeychain:
    """The in-memory keychain every test gets (conftest.py), with no prompts."""
    monkeypatch.delenv("DATALAB_ORACLE_PASSWORD", raising=False)
    monkeypatch.setattr(installing, "model_api_key", lambda: "saved")  # no prompt
    return memory_keychain


def test_practice_setup_saves_the_synthetic_databases_password(
    tmp_path, monkeypatch, keychain, capsys
):
    """A fresh practice DataLab can reach its database: the synthetic one's
    password is fixed and public, so setup saves it (without printing it)."""
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "practice"))

    assert installing.setup("practice", None, update=False) == 0

    assert keychain.saved[("datalab-practice", "DATALAB_RO")] == "datalab_ro"
    assert credentials.oracle_password(PRACTICE_ORACLE) == "datalab_ro"
    assert "datalab_ro" not in capsys.readouterr().out


def test_practice_setup_keeps_a_password_already_saved(tmp_path, monkeypatch, keychain):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "practice"))
    keychain.saved[("datalab-practice", "DATALAB_RO")] = "changed-by-a-developer"

    assert installing.setup("practice", None, update=False) == 0
    assert keychain.writes == 0
    monkeypatch.setattr(installing.getpass, "getpass", lambda prompt: "")  # no new key
    assert installing.setup("practice", None, update=True) == 0
    assert keychain.saved[("datalab-practice", "DATALAB_RO")] == "datalab_ro"


def test_the_real_profile_never_gets_the_synthetic_password(tmp_path, monkeypatch, keychain):
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "real"))
    lab = tmp_path / "lab.toml"
    lab.write_text(
        '[oracle]\nhost = "db.example.org"\nport = 1521\nservice = "SVC"\n'
        'user = "DATALAB_RO"\nread_only_roles = ["IHS_2025_RO"]\n'
        'allowed_schemas = ["IHS_2025"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(installing.getpass, "getpass", lambda prompt: "")  # skipped

    assert installing.setup("real", lab, update=True) == 0
    assert "datalab_ro" not in keychain.saved.values()
    # Not even when asked directly, with settings that look like practice's.
    real = Settings(profile="real", data_dir=tmp_path / "real", oracle=PRACTICE_ORACLE)
    assert installing.save_practice_password(real, update=True) is False
    lookalike = Settings(
        profile="practice",
        data_dir=tmp_path / "practice",
        oracle=dataclasses.replace(PRACTICE_ORACLE, host="db.example.org"),
    )
    assert installing.save_practice_password(lookalike, update=True) is False
    assert "datalab_ro" not in keychain.saved.values()


def test_a_missing_practice_password_says_how_to_save_it(keychain):
    with pytest.raises(credentials.MissingCredential, match="datalab --profile practice setup"):
        credentials.oracle_password(PRACTICE_ORACLE)


def test_practice_datalab_saves_its_password_when_it_starts(tmp_path, keychain):
    """Practice DataLabs set up by 0.1.0, whose setup didn't save it."""
    from datalab.cli import _save_practice_password

    practice = Settings(profile="practice", data_dir=tmp_path, oracle=PRACTICE_ORACLE)
    _save_practice_password(practice)
    assert keychain.saved == {("datalab-practice", "DATALAB_RO"): "datalab_ro"}
    _save_practice_password(Settings(profile="real", data_dir=tmp_path, oracle=PRACTICE_ORACLE))
    assert keychain.writes == 1


def test_a_keychain_that_fails_its_own_way_doesnt_stop_practice_datalab(
    tmp_path, keychain, monkeypatch, capsys
):
    """Windows' keychain raises pywintypes.error, which isn't a KeyringError."""
    import keyring

    from datalab.cli import _save_practice_password

    class NotAKeyringError(Exception):
        pass

    def refuse(service, account, value):
        raise NotAKeyringError("secret-looking detail")

    monkeypatch.setattr(keyring, "set_password", refuse)
    _save_practice_password(Settings(profile="practice", data_dir=tmp_path, oracle=PRACTICE_ORACLE))
    out = capsys.readouterr().out
    assert "NotAKeyringError" in out and "secret-looking" not in out


def test_the_installers_practice_command_saves_the_password(tmp_path, monkeypatch, keychain):
    """Both installers run exactly `datalab --profile practice setup`."""
    from pathlib import Path

    from datalab import cli

    repo = Path(__file__).resolve().parents[2]
    mac = (repo / "installer" / "macos" / "install.sh").read_text(encoding="utf-8")
    windows = (repo / "installer" / "windows" / "install.ps1").read_text(encoding="utf-8")
    assert '"$DATALAB" --profile "$PROFILE" setup' in mac
    assert "& $DataLab --profile $DataLabProfile setup" in windows
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "practice"))
    assert cli.main(["--profile", "practice", "setup"]) == 0
    assert keychain.saved[("datalab-practice", "DATALAB_RO")] == "datalab_ro"


def test_no_key_entered_says_conversations_need_one_and_how_to_add_it(
    tmp_path, monkeypatch, keychain, capsys
):
    """Pressing Enter at the key prompt is allowed (the SQL Playground and
    workflows don't need it), but it mustn't look like everything is set up."""
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path / "practice"))
    monkeypatch.setattr(installing, "model_api_key", credentials.model_api_key)  # none saved
    monkeypatch.setattr(installing.getpass, "getpass", lambda prompt: "")

    assert installing.setup("practice", None, update=False) == 0
    said = capsys.readouterr().out
    assert "No U-M GPT key was entered, so none was saved." in said
    assert "datalab --profile practice setup --update" in said
