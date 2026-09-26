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
