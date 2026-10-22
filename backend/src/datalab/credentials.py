"""Secrets live in the OS keychain (macOS Keychain, Windows Credential Manager).

`DATALAB_ORACLE_PASSWORD` overrides the keychain for CI and the synthetic
database only; it is never needed on a colleague's machine.

A server deployment (`deployment = "server"`) has no keychain and no
person at the keyboard: its secrets are files in systemd's credentials
folder ($CREDENTIALS_DIRECTORY, from `LoadCredential=`), readable only by
DataLab's service. There, nothing else is read, environment variables
included, and nothing is saved: the administrator puts them in place.
"""

from __future__ import annotations

import os
from pathlib import Path

import keyring
from keyring.errors import KeyringError

from datalab.config import PRACTICE_ORACLE, OracleSettings

MODEL_KEY_SERVICE = "datalab-umgpt"
MODEL_KEY_ACCOUNT = "api-key"

# A server's secrets, by file name in its credentials folder.
SERVER_MODEL_KEY = "model-api-key"
SERVER_ORACLE_PASSWORD = "oracle-password"
SERVER_GITHUB_CLIENT_SECRET = "github-client-secret"
# The key that encrypts people's GitHub sign-ins in DataLab's database.
SERVER_TOKEN_KEY = "token-key"

_server_secrets: Path | None = None


class MissingCredential(RuntimeError):
    pass


class SetOnServer(RuntimeError):
    """A secret the server's administrator sets, not anyone in DataLab."""


def use_server_secrets(directory: Path | None) -> None:
    """From now on, read secrets only from `directory` (None: the keychain
    again, for tests). Called once at start on a server."""
    global _server_secrets
    _server_secrets = directory


def on_server() -> bool:
    return _server_secrets is not None


def server_secret(name: str) -> str | None:
    """One of the server's secrets, or None if it isn't there."""
    if _server_secrets is None:
        return None
    try:
        value = (_server_secrets / name).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def _from_keychain(service: str, account: str) -> str | None:
    # A computer with no usable keychain (e.g. a CI runner) has no saved secrets.
    try:
        return keyring.get_password(service, account)
    except KeyringError:
        return None


def model_api_key() -> str:
    """The U-M GPT key. Only the model relay uses it; it never leaves this process."""
    if on_server():
        key = server_secret(SERVER_MODEL_KEY)
        if not key:
            raise MissingCredential(
                f"No U-M GPT key on the server: the administrator puts it in {SERVER_MODEL_KEY}."
            )
        return key
    key = os.environ.get("DATALAB_MODEL_API_KEY") or _from_keychain(
        MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT
    )
    if not key:
        raise MissingCredential("No U-M GPT key saved. Add it in Settings → Connections.")
    return key


def save_model_api_key(key: str) -> None:
    if on_server():
        raise SetOnServer("The U-M GPT key is set by the administrator on the server.")
    keyring.set_password(MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT, key)


def oracle_password(oracle: OracleSettings) -> str:
    if on_server():
        password = server_secret(SERVER_ORACLE_PASSWORD)
        if not password:
            raise MissingCredential(
                f"No database password on the server: the administrator puts it in "
                f"{SERVER_ORACLE_PASSWORD}."
            )
        return password
    password = os.environ.get("DATALAB_ORACLE_PASSWORD") or _from_keychain(
        oracle.keychain_service, oracle.user
    )
    if not password:
        where = (
            "Run: datalab --profile practice setup"
            if oracle is PRACTICE_ORACLE
            else "Add it in Settings → Connections."
        )
        raise MissingCredential(f"No database password saved for {oracle.user}. {where}")
    return password


def save_oracle_password(oracle: OracleSettings, password: str) -> None:
    if on_server():
        raise SetOnServer("The database password is set by the administrator on the server.")
    keyring.set_password(oracle.keychain_service, oracle.user, password)
