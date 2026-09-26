"""`datalab setup` and `datalab uninstall`: the parts of installing that are
the same on every platform. The platform installers (installer/) call these.
"""

from __future__ import annotations

import contextlib
import getpass
import shutil
import subprocess
import tomllib
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from datalab.config import PRACTICE_ORACLE, Profile, default_data_dir, load_settings
from datalab.credentials import (
    MODEL_KEY_ACCOUNT,
    MODEL_KEY_SERVICE,
    MissingCredential,
    model_api_key,
    oracle_password,
    save_model_api_key,
    save_oracle_password,
)

AGENT_IMAGE_REPOSITORIES = ("datalab-agent", "ghcr.io/sripadalab-um/datalab-agent")


def setup(profile: Profile, lab_settings: Path | None, *, update: bool) -> int:
    """Save the lab's connection settings and ask for the secrets DataLab needs."""
    data_dir = default_data_dir(profile)
    data_dir.mkdir(parents=True, exist_ok=True)
    if lab_settings is not None:
        tomllib.loads(lab_settings.read_text())  # refuse a broken file before copying it
        shutil.copyfile(lab_settings, data_dir / "settings.toml")
        print(f"Saved the lab's DataLab settings to {data_dir / 'settings.toml'}")
    settings = load_settings(profile)

    if update or not _saved(model_api_key):
        key = getpass.getpass("U-M GPT API key (input hidden): ").strip()
        if key:
            save_model_api_key(key)
            print("Saved the U-M GPT key to this computer's keychain.")

    oracle = settings.oracle
    if oracle is not None and oracle is not PRACTICE_ORACLE:
        if update or not _saved(lambda: oracle_password(oracle)):
            password = getpass.getpass(f"Database password for {oracle.user} (input hidden): ")
            if password:
                save_oracle_password(oracle, password)
                print("Saved the database password to this computer's keychain.")
    elif profile == "real":
        print(
            "No database is configured yet. Ask the DataLab maintainer for the lab's "
            "settings file, then run: datalab setup --settings <file>"
        )
    return 0


def uninstall(*, delete_data: bool | None) -> int:
    """Remove what DataLab put on this computer, except the app itself and exports.

    The installer's uninstall script removes the app afterwards. Export
    folders are never touched: those files belong to the user.
    """
    if _datalab_running():
        print("DataLab is running. Quit it first, then run the uninstaller again.")
        return 1

    print("Removing DataLab's containers, networks, and images…")
    _docker_quiet("ps", "-aq", "--filter", "label=datalab.session", then="rm -f")
    _docker_quiet("network", "ls", "-q", "--filter", "label=datalab.session", then="network rm")
    for repository in AGENT_IMAGE_REPOSITORIES:
        _docker_quiet("images", "-q", repository, then="rmi -f")

    print("Removing DataLab's saved keys from the keychain…")
    _forget(MODEL_KEY_SERVICE, MODEL_KEY_ACCOUNT)
    _forget(PRACTICE_ORACLE.keychain_service, PRACTICE_ORACLE.user)
    for profile in ("real", "practice"):
        oracle = load_settings(profile).oracle  # type: ignore[arg-type]
        # Only entries DataLab created. Anything else in the keychain (for
        # example a password another tool saved) is left alone.
        if oracle is not None and oracle.keychain_service.startswith("datalab-"):
            _forget(oracle.keychain_service, oracle.user)

    folders = [default_data_dir(p) for p in ("real", "practice")]  # type: ignore[arg-type]
    existing = [f for f in folders if f.exists()]
    if existing:
        print("\nDataLab's data folders (conversations, query results, settings):")
        for folder in existing:
            print(f"  {folder}  ({_size(folder)})")
        if delete_data is None:
            answer = input("Delete them? This can't be undone. [y/N] ").strip().lower()
            delete_data = answer in ("y", "yes")
        if delete_data:
            for folder in existing:
                shutil.rmtree(folder, ignore_errors=True)
            print("Deleted.")
        else:
            print("Kept. You can delete them yourself later.")
    print("Your export folders were not touched.")
    return 0


def _saved(get) -> bool:
    try:
        get()
        return True
    except MissingCredential:
        return False


def _forget(service: str, account: str) -> None:
    with contextlib.suppress(PasswordDeleteError, KeyringError):
        keyring.delete_password(service, account)


def _datalab_running() -> bool:
    import socket

    for profile in ("real", "practice"):
        port = load_settings(profile).port  # type: ignore[arg-type]
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
    return False


def _docker_quiet(*query: str, then: str) -> None:
    """Run a docker listing, then apply `then` to whatever it returned."""
    if shutil.which("docker") is None:
        return
    listing = subprocess.run(["docker", *query], capture_output=True, text=True)
    ids = listing.stdout.split()
    if ids:
        subprocess.run(["docker", *then.split(), *ids], capture_output=True)


def _size(folder: Path) -> str:
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
    for unit in ("bytes", "KB", "MB", "GB"):
        if total < 1024 or unit == "GB":
            return f"{total:.0f} {unit}" if unit == "bytes" else f"{total:.1f} {unit}"
        total /= 1024
    return ""
