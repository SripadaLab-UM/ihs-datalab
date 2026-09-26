"""Secrets live in the OS keychain (macOS Keychain, Windows Credential Manager).

`DATALAB_ORACLE_PASSWORD` overrides the keychain for CI and the synthetic
database only; it is never needed on a colleague's machine.
"""

from __future__ import annotations

import os

import keyring

from datalab.config import OracleSettings


class MissingCredential(RuntimeError):
    pass


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
