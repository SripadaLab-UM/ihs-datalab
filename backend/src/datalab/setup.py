"""`datalab setup` and `datalab uninstall`: the parts of installing that are
the same on every platform. The platform installers (installer/) call these.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from datalab.config import (
    PRACTICE_ORACLE,
    OracleSettings,
    Profile,
    QueryLimits,
    Settings,
    data_dir_for,
    default_data_dir,
    load_settings,
    resolve_profile,
)
from datalab.credentials import (
    MODEL_KEY_ACCOUNT,
    MODEL_KEY_SERVICE,
    MissingCredential,
    model_api_key,
    oracle_password,
    save_model_api_key,
    save_oracle_password,
)
from datalab.repos import github

AGENT_IMAGE_REPOSITORIES = ("datalab-agent", "ghcr.io/sripadalab-um/datalab-agent")


def setup(profile: Profile | None, lab_settings: Path | None, *, update: bool) -> int:
    """Save the lab's connection settings and ask for the secrets DataLab needs.

    The profile and folder are chosen as DataLab itself chooses them (the
    --profile flag, DATALAB_PROFILE, DATALAB_DATA_DIR), so the settings land
    where DataLab will read them.
    """
    profile = resolve_profile(profile)
    data_dir = data_dir_for(profile)
    data_dir.mkdir(parents=True, exist_ok=True)
    if lab_settings is not None:
        tomllib.loads(
            lab_settings.read_text(encoding="utf-8")
        )  # refuse a broken file before copying it
        shutil.copyfile(lab_settings, data_dir / "settings.toml")
        print(f"Saved the lab's DataLab settings to {data_dir / 'settings.toml'}")
    settings = load_settings(profile)
    print(f"Setting up DataLab ({profile}) in {data_dir}")

    if update or not _saved(model_api_key):
        key = getpass.getpass("U-M GPT API key (input hidden): ")
        if key.strip():
            try:
                store_model_key(key)
                print("Saved the U-M GPT key to this computer's keychain.")
            except SecretRefused as refused:
                print(f"{refused} Nothing was saved.")
                return 1

    oracle = asks_for_oracle_password(settings)
    if oracle is not None:
        if update or not _saved(lambda: oracle_password(oracle)):
            password = getpass.getpass(f"Database password for {oracle.user} (input hidden): ")
            if password:
                try:
                    store_oracle_password(oracle, password)
                    print("Saved the database password to this computer's keychain.")
                except SecretRefused as refused:
                    print(f"{refused} Nothing was saved.")
                    return 1
    elif profile == "practice":
        try:
            if save_practice_password(settings, update=update):
                print(
                    "Saved the practice database's password to this computer's keychain "
                    "(the synthetic database's fixed one, for fake data only)."
                )
        except KeyringError:
            print("Couldn't save the practice database's password: the keychain refused.")
            return 1
    elif profile == "real":
        print(
            "No database is configured yet. Ask the DataLab maintainer for the lab's "
            "settings file, then run: datalab setup --settings <file>"
        )
    return 0


# The synthetic database's app-user password: fixed, public and dev-only
# (synthetic/README.md). It unlocks fake data on this computer and nothing else.
SYNTHETIC_ORACLE_PASSWORD = "datalab_ro"


def save_practice_password(settings: Settings, *, update: bool = False) -> bool:
    """Save the synthetic database's password for practice DataLab, unless one
    is saved already (or `update`). Returns whether it saved it.

    Only ever for the practice profile's own synthetic database: the real
    profile's password always comes from the person.
    """
    if settings.profile != "practice" or settings.oracle is not PRACTICE_ORACLE:
        return False
    if not update and _saved(lambda: oracle_password(PRACTICE_ORACLE)):
        return False
    save_oracle_password(PRACTICE_ORACLE, SYNTHETIC_ORACLE_PASSWORD)
    return True


# ------------------------------------------------------------ connections
#
# What `datalab setup` asks for, shared with Settings → Connections, which
# shows the same things and saves them the same way. Secrets only ever go
# into the keychain: nothing here returns one.

SecretSource = Literal["keychain", "environment", "missing"]
# Development and CI overrides (credentials.py); never needed on a colleague's machine.
MODEL_KEY_ENV = "DATALAB_MODEL_API_KEY"
ORACLE_PASSWORD_ENV = "DATALAB_ORACLE_PASSWORD"
_MAX_SECRET = 4096


def asks_for_oracle_password(settings: Settings) -> OracleSettings | None:
    """The database whose password DataLab asks for: the lab's, never the
    practice one (its synthetic database's password is fixed, and setup saves
    it: save_practice_password)."""
    oracle = settings.oracle
    if settings.profile == "practice" or oracle is None or oracle is PRACTICE_ORACLE:
        return None
    return oracle


def model_key_source() -> SecretSource:
    """Where the U-M GPT key comes from, without returning it."""
    if os.environ.get(MODEL_KEY_ENV):
        return "environment"
    return "keychain" if _saved(model_api_key) else "missing"


def oracle_password_source(oracle: OracleSettings) -> SecretSource:
    """Where the database password comes from, without returning it."""
    if os.environ.get(ORACLE_PASSWORD_ENV):
        return "environment"
    return "keychain" if _saved(lambda: oracle_password(oracle)) else "missing"


class SecretRefused(ValueError):
    """A key or password that can't be saved. The message never contains it."""


def store_model_key(key: str) -> None:
    """Save the U-M GPT key to the keychain, replacing any saved before."""
    key = key.strip()
    _check_secret(key, "The U-M GPT key")
    if any(c.isspace() for c in key):
        raise SecretRefused("The U-M GPT key can't contain spaces or line breaks.")
    save_model_api_key(key)


def store_oracle_password(oracle: OracleSettings, password: str) -> None:
    """Save the database password to the keychain, replacing any saved before."""
    _check_secret(password, "The database password")
    save_oracle_password(oracle, password)


def _check_secret(value: str, what: str) -> None:
    if not value:
        raise SecretRefused(f"{what} is empty.")
    if len(value) > _MAX_SECRET:
        raise SecretRefused(f"{what} is too long.")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SecretRefused(f"{what} can't contain line breaks or control characters.")


@dataclass(frozen=True)
class DatabaseCheck:
    ok: bool
    message: str
    enabled_roles: tuple[str, ...] = ()
    read_only: bool | None = None


def check_database(
    oracle: OracleSettings, limits: QueryLimits, *, practice: bool, timeout: float = 15
) -> DatabaseCheck:
    """Connect as DataLab does (read-only roles only) and ask what the session may do.

    The same question as `datalab db-check` and the Safety check's database
    check. The message is safe to show: it never contains the password.
    """
    import oracledb

    from datalab.data.oracle import NotSyntheticDatabase, OracleDatabase

    try:
        password = oracle_password(oracle)
    except MissingCredential as missing:
        return DatabaseCheck(False, str(missing))
    try:
        privileges = OracleDatabase(oracle, password, limits).session_privileges(timeout=timeout)
    except NotSyntheticDatabase as error:
        return DatabaseCheck(False, str(error))
    except oracledb.Error as error:
        return DatabaseCheck(False, _database_problem(error, practice=practice))
    roles = tuple(sorted(privileges.enabled_roles))
    if not privileges.is_read_only:
        return DatabaseCheck(
            False,
            "Connected, but the session isn't read-only, so the Safety check will fail. "
            "Tell the DataLab maintainer.",
            roles,
            False,
        )
    return DatabaseCheck(True, "Connected, read-only.", roles, True)


def _database_problem(error: Exception, *, practice: bool) -> str:
    # Only the error's code: its text can name the server.
    found = re.match(r"\s*((?:ORA|DPY)-\d+)", str(error))
    code = found.group(1) if found else type(error).__name__
    if code in ("ORA-01017", "ORA-01005"):
        return "The database refused the user name or password."
    if code == "ORA-28000":
        return "The database account is locked. Ask the DataLab maintainer."
    if code in ("ORA-28001", "ORA-28002"):
        return "The database password has expired. Ask the DataLab maintainer for a new one."
    unreachable = found is None or code.startswith(("DPY-6", "DPY-4011", "ORA-12", "ORA-03"))
    if not unreachable:
        return f"The database refused the connection ({code}). Tell the DataLab maintainer."
    if practice:
        return f"Can't reach the practice database. Is the synthetic database running? ({code})"
    return f"Can't reach the database — are you on the VPN? ({code})"


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
    _forget(github.KEYCHAIN_SERVICE, github.KEYCHAIN_ACCOUNT)
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
    # A Docker Desktop whose engine is stuck can leave `docker` waiting forever;
    # uninstalling carries on without it rather than hanging.
    try:
        listing = subprocess.run(
            ["docker", *query],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        ids = listing.stdout.split()
        if ids:
            subprocess.run(["docker", *then.split(), *ids], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"  Docker didn't answer, so this was skipped: docker {' '.join(query)}")


def _size(folder: Path) -> str:
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
    for unit in ("bytes", "KB", "MB", "GB"):
        if total < 1024 or unit == "GB":
            return f"{total:.0f} {unit}" if unit == "bytes" else f"{total:.1f} {unit}"
        total /= 1024
    return ""
