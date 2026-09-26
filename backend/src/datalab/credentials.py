"""Secrets live in the OS keychain (macOS Keychain, Windows Credential Manager).

`DATALAB_ORACLE_PASSWORD` overrides the keychain for CI and the synthetic
database only; it is never needed on a colleague's machine.
"""

from __future__ import annotations

import os

import keyring

from datalab.config import OracleSettings

MODEL_KEY_SERVICE = "datalab-umgpt"
MODEL_KEY_ACCOUNT = "api-key"


class MissingCredential(RuntimeError):
    pass


def model_api_key() -> str:
    """The U-M GPT key. Only the model relay uses it; it never leaves this process."""
    key = os.environ.get("DATALAB_MODEL_API_KEY") or keyring.get_password(
        MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT
    )
    if not key:
        raise MissingCredential("No U-M GPT key saved. Add it in Settings → Connections.")
    return key


def save_model_api_key(key: str) -> None:
    keyring.set_password(MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT, key)


def oracle_password(oracle: OracleSettings) -> str:
    password = os.environ.get("DATALAB_ORACLE_PASSWORD") or keyring.get_password(
        oracle.keychain_service, oracle.user
    )
    if not password:
        raise MissingCredential(
            f"No database password saved for {oracle.user}. Add it in Settings → Connections."
        )
    return password


def save_oracle_password(oracle: OracleSettings, password: str) -> None:
    keyring.set_password(oracle.keychain_service, oracle.user, password)
