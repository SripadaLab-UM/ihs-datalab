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


@pytest.fixture
def keychain(monkeypatch):
    """The OS keychain, in memory: no test writes to this computer's."""
    fake = MemoryKeychain()
    monkeypatch.setattr(credentials, "keyring", fake)
    monkeypatch.setattr(
        credentials, "_from_keychain", lambda service, account: fake.saved.get((service, account))
    )
    monkeypatch.delenv("DATALAB_ORACLE_PASSWORD", raising=False)
    monkeypatch.setattr(installing, "model_api_key", lambda: "saved")  # no prompt
    return fake


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
