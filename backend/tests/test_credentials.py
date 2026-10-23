import keyring
import pytest
from keyring.errors import NoKeyringError

from datalab import credentials
from datalab.config import PRACTICE_ORACLE


@pytest.fixture
def no_keychain(monkeypatch):
    def unavailable(*_):
        raise NoKeyringError("no keychain backend on this computer")

    monkeypatch.setattr(keyring, "get_password", unavailable)
    monkeypatch.delenv("DATALAB_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("DATALAB_ORACLE_PASSWORD", raising=False)


def test_a_computer_without_a_keychain_has_no_saved_secrets(no_keychain):
    with pytest.raises(credentials.MissingCredential):
        credentials.model_api_key()
    with pytest.raises(credentials.MissingCredential):
        credentials.oracle_password(PRACTICE_ORACLE)


def test_environment_overrides_work_without_a_keychain(no_keychain, monkeypatch):
    monkeypatch.setenv("DATALAB_ORACLE_PASSWORD", "from-env")
    assert credentials.oracle_password(PRACTICE_ORACLE) == "from-env"


@pytest.fixture
def server_secrets(tmp_path, monkeypatch):
    folder = tmp_path / "credentials"
    folder.mkdir()
    credentials.use_server_secrets(folder)
    yield folder
    credentials.use_server_secrets(None)


def test_a_server_reads_only_its_credentials_folder(server_secrets, monkeypatch):
    # Not the environment, not the keychain: only what the administrator put there.
    monkeypatch.setenv("DATALAB_MODEL_API_KEY", "from-env")
    monkeypatch.setenv("DATALAB_ORACLE_PASSWORD", "from-env")
    monkeypatch.setattr(keyring, "get_password", lambda *_: "from-keychain")
    with pytest.raises(credentials.MissingCredential, match="administrator"):
        credentials.model_api_key()
    with pytest.raises(credentials.MissingCredential, match="administrator"):
        credentials.oracle_password(PRACTICE_ORACLE)
    (server_secrets / credentials.SERVER_MODEL_KEY).write_text("lab-key\n")
    (server_secrets / credentials.SERVER_ORACLE_PASSWORD).write_text("db-password")
    assert credentials.model_api_key() == "lab-key"
    assert credentials.oracle_password(PRACTICE_ORACLE) == "db-password"


def test_nothing_is_saved_from_inside_a_server_datalab(server_secrets, monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(keyring, "set_password", lambda *args: saved.append(args[0]))
    with pytest.raises(credentials.SetOnServer):
        credentials.save_model_api_key("k")
    with pytest.raises(credentials.SetOnServer):
        credentials.save_oracle_password(PRACTICE_ORACLE, "p")
    assert saved == [] and not list(server_secrets.iterdir())
