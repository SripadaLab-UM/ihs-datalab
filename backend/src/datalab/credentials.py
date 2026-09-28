"""Secrets live in the OS keychain (macOS Keychain, Windows Credential Manager).

`DATALAB_ORACLE_PASSWORD` overrides the keychain for CI and the synthetic
database only; it is never needed on a colleague's machine.
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError

from datalab.config import PRACTICE_ORACLE, OracleSettings

MODEL_KEY_SERVICE = "datalab-umgpt"
MODEL_KEY_ACCOUNT = "api-key"


class MissingCredential(RuntimeError):
    pass


def _from_keychain(service: str, account: str) -> str | None:
    # A computer with no usable keychain (e.g. a CI runner) has no saved secrets.
    try:
        return keyring.get_password(service, account)
    except KeyringError:
        return None


def model_api_key() -> str:
    """The U-M GPT key. Only the model relay uses it; it never leaves this process."""
    key = os.environ.get("DATALAB_MODEL_API_KEY") or _from_keychain(
        MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT
    )
    if not key:
        raise MissingCredential("No U-M GPT key saved. Add it in Settings → Connections.")
    return key


def save_model_api_key(key: str) -> None:
    keyring.set_password(MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT, key)


def oracle_password(oracle: OracleSettings) -> str:
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
    keyring.set_password(oracle.keychain_service, oracle.user, password)
