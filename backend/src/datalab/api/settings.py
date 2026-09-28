"""Settings (milestone 7): Connections, Storage, Updates and Copy diagnostics.

- **Connections** shows the database connection and whether the database
  password and U-M GPT key are saved, lets the person save or replace them
  (into the keychain only: nothing here ever returns a secret), and tests
  them. It shares `datalab setup`'s logic (setup.py). The practice profile
  only shows its synthetic database; nothing can be changed there.
- **Storage** lists what uses disk in the data folder, and removes one item
  the person chose (storage.py says what can go, and what never does).
- **Updates** shows the installed version, what the last check for a newer
  release found (releases.py), an update in progress, what the startup
  recovery did, recent updates and the backups. **Install update** runs the
  updater (updater.py), only once the person has confirmed.
- **Diagnostics** builds the metadata-only report (diagnostics.py).
- **Feedback contact** says whom the toolbar's Feedback goes to: the lab's
  `repos.access_contact`, and the email address in it, if it has one.

The GitHub sign-in (knowledge base and pipelines) is its own section, with
its own routes in knowledge.py.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from keyring.errors import KeyringError
from pydantic import BaseModel, SecretStr

from datalab import __version__, db, diagnostics, setup, updates
from datalab.config import Settings
from datalab.credentials import model_api_key
from datalab.db import backups as backups_module
from datalab.relay.policy import model_allowed
from datalab.releases import UpdateChecker
from datalab.storage import Storage, StorageRefused
from datalab.updater import UpdateFailed, Updater


def _no_turns(_: str) -> bool:
    return False


def _nothing() -> None:
    return None


@dataclass(frozen=True)
class SettingsServices:
    """What the Settings routes use, given by the app (app.py)."""

    settings: Settings
    database: sqlite3.Connection
    # Whether a conversation's agent is working (SessionManager.turn_running).
    turn_running: Callable[[str], bool] = _no_turns
    # For "Test connection" to U-M GPT. None: a client of its own per test.
    model_http: httpx.AsyncClient | None = None
    model_key: Callable[[], str] = model_api_key
    # Called after a new database password is saved, so the data service
    # connects with it from the next query on.
    password_changed: Callable[[], None] = _nothing
    # What the startup recovery did about an interrupted update, if anything.
    recovery: updates.Recovery | None = None
    # The check for a newer release, and the updater. None: ones of the
    # router's own (tests); the updater then can't restart anything.
    checker: UpdateChecker | None = None
    updater: Updater | None = None
    # Stand-ins for tests: the database check, and Docker's version.
    check_database: Callable[..., setup.DatabaseCheck] = setup.check_database
    docker_version: Callable[[], str] = diagnostics.docker_version


class SettingsStatus(BaseModel):
    available: bool


# ------------------------------------------------------------ connections

SecretSourceOut = Literal["keychain", "environment", "missing"]


class OracleConnectionOut(BaseModel):
    configured: bool
    practice: bool
    dsn: str | None
    user: str | None
    read_only_roles: list[str]
    allowed_schemas: list[str]
    password: SecretSourceOut | None  # None: the practice database's is fixed
    can_set_password: bool


class ModelKeyOut(BaseModel):
    base_url: str
    key: SecretSourceOut
    can_set_key: bool


class ConnectionsOut(BaseModel):
    profile: Literal["real", "practice"]
    settings_file: str
    oracle: OracleConnectionOut
    model: ModelKeyOut
    # Why nothing can be changed here, when it can't.
    read_only_because: str | None


class OraclePasswordIn(BaseModel):
    password: SecretStr


class ModelKeyIn(BaseModel):
    key: SecretStr


class ConnectionCheckOut(BaseModel):
    ok: bool
    message: str


class DatabaseCheckOut(ConnectionCheckOut):
    enabled_roles: list[str]
    read_only: bool | None


class ConnectionTestOut(BaseModel):
    database: DatabaseCheckOut
    model: ConnectionCheckOut


# ---------------------------------------------------------------- storage


class StorageItemOut(BaseModel):
    kind: str
    id: str
    label: str
    detail: str
    size_bytes: int
    modified_at: str | None
    removable: bool
    not_removable_because: str | None
    kept: bool
    kept_because: str | None
    removing_loses: str | None
    needs_confirmation: bool


class StorageGroupOut(BaseModel):
    id: str
    title: str
    about: str
    size_bytes: int
    items: list[StorageItemOut]


class StorageOut(BaseModel):
    data_dir: str
    size_bytes: int
    free_bytes: int | None
    groups: list[StorageGroupOut]


class StorageRemoveIn(BaseModel):
    kind: Literal["playground-result", "run-files", "backup"]
    id: str
    # For a backup taken before a rollback: the person read what's lost.
    confirmed: bool = False


class StorageRemovedOut(BaseModel):
    kind: str
    id: str
    freed_bytes: int


# ---------------------------------------------------------------- updates


class UpdateMarkerOut(BaseModel):
    from_version: str
    to_version: str
    state: str
    started_at: str
    updated_at: str
    backup: str | None


class RecoveryOut(BaseModel):
    outcome: str  # finishing, abandoned, undone, needs-you
    message: str


class UpdateHistoryOut(BaseModel):
    at: str
    outcome: str
    from_version: str | None
    to_version: str | None


class BackupInfoOut(BaseModel):
    name: str
    app_version: str
    from_version: str | None
    reason: str
    created_at: str
    size_bytes: int
    schema_version: str | None
    # Whether this DataLab could roll back to it (it has every migration).
    usable: bool
    kept: bool


class ReleaseOut(BaseModel):
    version: str
    tag: str
    title: str
    # As written on GitHub (Markdown). Shown as plain text, never as HTML.
    notes: str
    published_at: str | None
    page: str | None  # the release's page on github.com
    prerelease: bool
    size_bytes: int


class UpdateInstallOut(BaseModel):
    state: Literal[
        "idle",
        "downloading",
        "stopping",
        "backing-up",
        "installing",
        "pulling-images",
        "switching",
        "restarting",
        "failed",
    ]
    version: str | None
    message: str
    started_at: str | None
    updated_at: str | None


class UpdateCheckOut(BaseModel):
    state: Literal[
        "not-checked",
        "not-configured",
        "up-to-date",
        "available",
        "offline",
        "rate-limited",
        "not-visible",
        "failed",
    ]
    message: str
    current_version: str
    channel: str
    checked_at: str | None
    available: ReleaseOut | None
    can_install: bool
    # Why Install update can't be used here or now, when it can't.
    cannot_install_because: str | None
    install: UpdateInstallOut
    # An update is being installed: nothing new can start until DataLab restarts.
    updating: bool


class UpdateInstallIn(BaseModel):
    version: str
    # The person read what installing does and said yes.
    confirmed: bool = False


class UpdatesOut(BaseModel):
    version: str
    check: UpdateCheckOut
    marker: UpdateMarkerOut | None
    marker_unreadable: bool
    recovery: RecoveryOut | None
    history: list[UpdateHistoryOut]
    set_aside_notes: list[str]
    backups: list[BackupInfoOut]
    migrations_applied: int
    latest_migration: str | None


class FeedbackContactOut(BaseModel):
    # The lab's `repos.access_contact` as written ("Ali <ali@umich.edu>"), if set.
    contact: str | None
    # The email address in it, for a mailto: link; None when there isn't one.
    email: str | None


_EMAIL = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def feedback_contact(contact: str | None) -> FeedbackContactOut:
    text = contact.strip() if contact else None
    found = _EMAIL.search(text) if text else None
    return FeedbackContactOut(contact=text or None, email=found.group(0) if found else None)


class DiagnosticsOut(BaseModel):
    text: str


# ----------------------------------------------------------------- router


def build_settings_router(services: SettingsServices) -> APIRouter:
    settings = services.settings
    practice = settings.profile == "practice"
    storage = Storage(settings, services.database, turn_running=services.turn_running)
    problems = diagnostics.recent_problems()
    checker = services.checker or UpdateChecker(settings)
    updater = services.updater or Updater(settings, checker)
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("/status")
    def status() -> SettingsStatus:
        return SettingsStatus(available=True)

    # Connections --------------------------------------------------------

    @router.get("/connections")
    def connections() -> ConnectionsOut:
        oracle = settings.oracle
        lab = setup.asks_for_oracle_password(settings)
        return ConnectionsOut(
            profile=settings.profile,
            settings_file=str(settings.settings_file),
            oracle=OracleConnectionOut(
                configured=oracle is not None,
                practice=practice,
                dsn=oracle.dsn if oracle else None,
                user=oracle.user if oracle else None,
                read_only_roles=list(oracle.read_only_roles) if oracle else [],
                allowed_schemas=sorted(oracle.allowed_schemas) if oracle else [],
                password=setup.oracle_password_source(lab) if lab else None,
                can_set_password=lab is not None,
            ),
            model=ModelKeyOut(
                base_url=settings.model_base_url,
                key=setup.model_key_source(),
                can_set_key=not practice,
            ),
            read_only_because=(
                "Practice DataLab uses the local synthetic database, whose settings are "
                "fixed. It uses the U-M GPT key the real DataLab saved, or one saved with "
                "datalab setup --profile practice --update."
                if practice
                else None
            ),
        )

    @router.put("/connections/database-password", status_code=204)
    def save_password(body: OraclePasswordIn) -> None:
        """Save or replace the database password, in the keychain only."""
        if practice:
            raise HTTPException(403, "Practice DataLab's database settings are fixed.")
        oracle = setup.asks_for_oracle_password(settings)
        if oracle is None:
            raise HTTPException(
                409,
                "No database is set up yet. Ask the DataLab maintainer for the lab's "
                "settings file.",
            )
        try:
            setup.store_oracle_password(oracle, body.password.get_secret_value())
        except setup.SecretRefused as refused:
            raise HTTPException(422, str(refused)) from None
        except KeyringError:
            raise HTTPException(503, _KEYCHAIN_FAILED) from None
        services.password_changed()

    @router.put("/connections/model-key", status_code=204)
    def save_model_key(body: ModelKeyIn) -> None:
        """Save or replace the U-M GPT key, in the keychain only."""
        if practice:
            raise HTTPException(403, "Practice DataLab uses the key the real DataLab saved.")
        try:
            setup.store_model_key(body.key.get_secret_value())
        except setup.SecretRefused as refused:
            raise HTTPException(422, str(refused)) from None
        except KeyringError:
            raise HTTPException(503, _KEYCHAIN_FAILED) from None

    @router.post("/connections/test")
    async def test_connections() -> ConnectionTestOut:
        database, model = await asyncio.gather(asyncio.to_thread(check_database), check_model())
        return ConnectionTestOut(database=database, model=model)

    def check_database() -> DatabaseCheckOut:
        oracle = settings.oracle
        if oracle is None:
            return DatabaseCheckOut(
                ok=False, message="No database is set up yet.", enabled_roles=[], read_only=None
            )
        result = services.check_database(oracle, settings.limits, practice=practice)
        return DatabaseCheckOut(
            ok=result.ok,
            message=result.message,
            enabled_roles=list(result.enabled_roles),
            read_only=result.read_only,
        )

    async def check_model() -> ConnectionCheckOut:
        try:
            key = await asyncio.to_thread(services.model_key)
        except Exception as error:  # MissingCredential, or a keychain that won't answer
            return ConnectionCheckOut(ok=False, message=_safe_key_error(error))
        url = f"{settings.model_base_url.rstrip('/')}/models"
        client = services.model_http or httpx.AsyncClient()
        try:
            response = await client.get(url, headers={"authorization": f"Bearer {key}"}, timeout=15)
        except httpx.HTTPError:
            return ConnectionCheckOut(
                ok=False, message="Can't reach U-M GPT. Check your internet connection."
            )
        finally:
            if services.model_http is None:
                await client.aclose()
        if response.status_code in (401, 403):
            return ConnectionCheckOut(ok=False, message="U-M GPT refused the key.")
        if response.status_code != 200:
            return ConnectionCheckOut(
                ok=False, message=f"U-M GPT answered with an error ({response.status_code})."
            )
        try:
            listed = [m.get("id") for m in response.json().get("data", [])]
        except (ValueError, AttributeError):
            listed = []
        approved = [
            m for m in listed if isinstance(m, str) and model_allowed(m, settings.allowed_models)
        ]
        plural = "s" if len(approved) != 1 else ""
        return ConnectionCheckOut(
            ok=True,
            message=f"U-M GPT accepted the key and offers {len(approved)} approved model{plural}.",
        )

    # Storage ------------------------------------------------------------

    @router.get("/storage")
    def storage_usage() -> StorageOut:
        usage = storage.usage()
        return StorageOut(
            data_dir=str(usage.data_dir),
            size_bytes=usage.size_bytes,
            free_bytes=usage.free_bytes,
            groups=[
                StorageGroupOut(
                    id=g.id,
                    title=g.title,
                    about=g.about,
                    size_bytes=g.size_bytes,
                    items=[StorageItemOut(**asdict(i)) for i in g.items],
                )
                for g in usage.groups
            ],
        )

    @router.post("/storage/remove")
    def storage_remove(body: StorageRemoveIn) -> StorageRemovedOut:
        """Remove one item the person chose and confirmed. Refuses, changing nothing,
        when it's in use or isn't something that can go."""
        try:
            removed = storage.remove(body.kind, body.id, confirmed=body.confirmed)
        except StorageRefused as refused:
            raise HTTPException(refused.status, str(refused)) from None
        return StorageRemovedOut(**asdict(removed))

    # Updates ------------------------------------------------------------

    @router.get("/updates")
    def updates_status() -> UpdatesOut:
        data_dir = settings.data_dir
        marker = None
        unreadable = False
        try:
            found = updates.read_marker(data_dir)
        except updates.UnreadableMarker:
            found, unreadable = None, True
        if found is not None:
            marker = UpdateMarkerOut(
                from_version=found.from_version,
                to_version=found.to_version,
                state=found.state,
                started_at=found.started_at,
                updated_at=found.updated_at,
                backup=found.backup,
            )
        known = db.known_migrations()
        applied = backups_module.applied_migrations(services.database)
        recovery = services.recovery
        found_backups = backups_module.list_backups(
            backups_module.backups_dir(settings.database_file)
        )
        return UpdatesOut(
            version=__version__,
            check=check_state(),
            marker=marker,
            marker_unreadable=unreadable,
            recovery=RecoveryOut(outcome=recovery.outcome, message=recovery.message)
            if recovery
            else None,
            history=[
                UpdateHistoryOut(
                    at=str(e.get("at", "")),
                    outcome=str(e.get("outcome", "")),
                    from_version=_text(e.get("from_version")),
                    to_version=_text(e.get("to_version")),
                )
                for e in updates.history(data_dir)
            ],
            set_aside_notes=updates.set_aside_notes(data_dir),
            backups=[
                BackupInfoOut(
                    name=b.name,
                    app_version=b.app_version,
                    from_version=b.from_version,
                    reason=b.reason,
                    created_at=b.created_at,
                    size_bytes=b.size_bytes,
                    schema_version=b.schema_version,
                    usable=set(b.migrations) <= known,
                    kept=b.reason in backups_module.KEPT_REASONS,
                )
                for b in reversed(found_backups)
            ],
            migrations_applied=len(applied),
            latest_migration=applied[-1] if applied else None,
        )

    def check_state() -> UpdateCheckOut:
        last = checker.last
        release = last.release
        progress = updater.progress
        why_not = None
        if release is not None and last.state == "available":
            why_not = updater.why_not()
            if why_not is None and updater.running():
                why_not = "The update is being installed."
        return UpdateCheckOut(
            state=last.state,
            message=last.message,
            current_version=last.current,
            channel=last.channel,
            checked_at=last.checked_at,
            available=ReleaseOut(
                version=release.version,
                tag=release.tag,
                title=release.title,
                notes=release.notes,
                published_at=release.published_at,
                page=release.page,
                prerelease=release.prerelease,
                size_bytes=release.wheel.size,
            )
            if release is not None and last.state == "available"
            else None,
            can_install=release is not None and last.state == "available" and why_not is None,
            cannot_install_because=why_not,
            install=UpdateInstallOut(**asdict(progress)),
            updating=updater.gate.closed_for is not None,
        )

    @router.get("/updates/check")
    def update_check() -> UpdateCheckOut:
        """What the last check found (no network): for the Update available pill."""
        return check_state()

    @router.post("/updates/check")
    async def check_now() -> UpdateCheckOut:
        """Ask GitHub now (at most once a minute; never while GitHub asked to wait)."""
        await asyncio.to_thread(checker.check)
        return check_state()

    @router.post("/updates/install")
    async def install_update(body: UpdateInstallIn) -> UpdateCheckOut:
        """Install the release on offer, once the person has confirmed. Returns at
        once; GET /updates/check follows it. DataLab restarts at the end."""
        if not body.confirmed:
            raise HTTPException(400, "Installing an update needs your confirmation.")
        try:
            await updater.start(body.version)
        except UpdateFailed as refused:
            raise HTTPException(409, str(refused)) from None
        return check_state()

    # Diagnostics --------------------------------------------------------

    @router.get("/diagnostics")
    def diagnostics_text() -> DiagnosticsOut:
        return DiagnosticsOut(
            text=diagnostics.build(
                settings,
                services.database,
                problems=problems.latest(),
                recovery=services.recovery,
                docker=services.docker_version(),
            )
        )

    @router.get("/feedback-contact")
    def feedback_contact_route() -> FeedbackContactOut:
        return feedback_contact(settings.repos.access_contact)

    return router


_KEYCHAIN_FAILED = (
    "This computer's keychain didn't save it (it may be locked, or have refused DataLab). "
    "Nothing was saved. Unlock the keychain and try again."
)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _safe_key_error(error: Exception) -> str:
    from datalab.credentials import MissingCredential

    if isinstance(error, MissingCredential):
        return str(error)
    return "Couldn't read the U-M GPT key from the keychain."
