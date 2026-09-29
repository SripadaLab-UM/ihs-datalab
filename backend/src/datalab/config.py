"""DataLab settings.

Settings come from `settings.toml` in the profile's data folder, with a few
environment-variable overrides for development and CI. Nothing
environment-specific (hostnames, account names) is hard-coded here: the real
profile's database details live only in the user's settings file.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

Profile = Literal["real", "practice"]


@dataclass(frozen=True)
class OracleSettings:
    host: str
    port: int
    service: str
    user: str
    # Where the password lives in the OS keychain (service name; account = user).
    keychain_service: str
    # The only roles enabled on each connection. See docs/SAFETY.md.
    read_only_roles: tuple[str, ...]
    allowed_schemas: frozenset[str]
    # The practice profile must only ever talk to the synthetic database, even
    # if something else answers on its port (an SSH tunnel, say). It checks for
    # a marker table that exists only there (see practice_db/guard.py).
    require_synthetic_marker: bool = False

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service}"


@dataclass(frozen=True)
class QueryLimits:
    deadline_seconds: float = 600
    round_trip_timeout_seconds: float = 120
    max_rows: int = 2_000_000
    max_bytes: int = 2 * 1024**3
    preview_rows: int = 50
    min_free_disk_bytes: int = 2 * 1024**3
    max_concurrent_queries: int = 2


# One section of settings.toml per later area of DataLab. Each starts with
# the few fields known now, with safe defaults; the milestone that builds the
# area adds what it needs here, and nowhere else in this file.


# A GitHub repository as `owner/name`. Neither part may start with "-" (it
# could reach git as an option) or be "." or "..".
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.\.?$)[A-Za-z0-9._][A-Za-z0-9._-]*")


def _check_repo(key: str, value: str | None) -> None:
    if value is not None and not _REPO.fullmatch(value):
        raise ValueError(f"{key} must be a GitHub repository as owner/name, not {value!r}")


@dataclass(frozen=True)
class PlaygroundSettings:
    """`[playground]`: the SQL Playground (milestone 4).

    To come: how long results are kept, saved queries, a history limit.
    Queries themselves keep the data service's limits (`[limits]`).
    """

    # Rows shown in the results grid. The whole result is always in its CSV.
    preview_rows: int = 200

    def __post_init__(self) -> None:
        # The data service returns at most 500 preview rows.
        if not 1 <= self.preview_rows <= 500:
            raise ValueError("playground.preview_rows must be between 1 and 500")


@dataclass(frozen=True)
class RepoSettings:
    """`[repos]`: the lab's git repositories (milestones 5 and 6).

    None means not configured: nothing is cloned or synced. Both follow
    `main`. To come: how often to sync.
    """

    # e.g. "SripadaLab-UM/ihs-knowledge"
    knowledge: str | None = None
    # e.g. "SripadaLab-UM/ihs-pipelines"
    pipelines: str | None = None
    # The lab's GitHub App, for signing in with the device flow. Its client
    # id isn't a secret; DataLab holds no client secret or key for it.
    client_id: str | None = None
    # Whom to ask for access to the repos, shown when GitHub says no
    # (e.g. "Ali, the DataLab maintainer").
    access_contact: str | None = None
    # The lab's private repository for support reports (Send feedback → Send
    # to the lab), e.g. "SripadaLab-UM/ihs-support". It must be private:
    # DataLab checks, and won't send to a public one. See docs/SUPPORT.md.
    support: str | None = None

    def __post_init__(self) -> None:
        for key in ("knowledge", "pipelines", "support"):
            _check_repo(f"repos.{key}", getattr(self, key))
        if self.client_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", self.client_id):
            raise ValueError("repos.client_id must be a GitHub App client id, such as Iv23li…")


@dataclass(frozen=True)
class WorkflowSettings:
    """`[workflows]`: the workflow runner (milestone 6).

    To come: how long run folders are kept.
    """

    # Workflow runs going at once. Their SQL steps also share the data
    # service's query slots (`limits.max_concurrent_queries`).
    max_concurrent_runs: int = 1
    # Where workflow files are read from: a folder laid out like the
    # ihs-pipelines repo (`workflows/*.yaml`, `ihsDataR/`), or holding the
    # YAML files directly. Unset: the clone of `repos.pipelines` when that's
    # set, else `<data folder>/workflows-local` (workflows/source.py).
    folder: str | None = None
    # Each R, pipeline, or custom QC step's container.
    step_timeout_seconds: float = 30 * 60
    step_memory: str = "4g"
    step_cpus: str = "2"
    step_pids: int = 256
    # Everything one run may write in its run folder: extracts, outputs, logs.
    max_run_bytes: int = 20 * 1024**3

    def __post_init__(self) -> None:
        if self.max_concurrent_runs < 1:
            raise ValueError("workflows.max_concurrent_runs must be at least 1")
        if self.step_timeout_seconds <= 0:
            raise ValueError("workflows.step_timeout_seconds must be more than 0")
        if not re.fullmatch(r"[1-9][0-9]*[kmg]", self.step_memory):
            raise ValueError("workflows.step_memory must be a size such as 4g or 512m")
        if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", self.step_cpus) or float(self.step_cpus) <= 0:
            raise ValueError("workflows.step_cpus must be a number of CPUs, such as 2")
        if not 16 <= self.step_pids <= 4096:
            raise ValueError("workflows.step_pids must be between 16 and 4096")
        if self.max_run_bytes < 1024**2:
            raise ValueError("workflows.max_run_bytes must be at least 1 MB")


@dataclass(frozen=True)
class UpdateSettings:
    """`[updates]`: checking for and installing new releases (milestone 7).

    See docs/DISTRIBUTION.md, "Updating". To come: where backups go and how
    many are kept.
    """

    # Whether DataLab asks GitHub for a newer release when it starts. "Check
    # now" in Settings → Updates works either way.
    check_on_start: bool = True
    # Whether DataLab asks again about once an hour while it's open (60
    # minutes plus up to 5, so installs don't all ask at once). The person can
    # turn it off or on in Settings → Updates, for this computer (kept in the
    # data folder's update-preferences.json); this is the starting value.
    check_every_hour: bool = True
    # Where releases come from: the app repo's GitHub Releases, read without
    # signing in (the repo is public).
    repository: str = "SripadaLab-UM/ihs-datalab"
    # Which releases are offered: "stable" (full releases only),
    # "pre-release" (pre-releases too), or "auto": pre-releases while the
    # installed DataLab is itself a pre-release, else stable only.
    channel: str = "auto"

    def __post_init__(self) -> None:
        _check_repo("updates.repository", self.repository)
        if self.channel not in UPDATE_CHANNELS:
            raise ValueError(
                f"updates.channel must be one of {', '.join(UPDATE_CHANNELS)}, not {self.channel!r}"
            )


UPDATE_CHANNELS = ("auto", "stable", "pre-release")


@dataclass(frozen=True)
class Settings:
    profile: Profile
    data_dir: Path
    oracle: OracleSettings | None
    limits: QueryLimits = field(default_factory=QueryLimits)
    # Folder of catalog YAML files (the knowledge base's generated/schema).
    catalog_dir: Path | None = None
    # The pinned agent image. Releases set this to an image digest.
    agent_image: str = "datalab-agent:dev"
    model_base_url: str = "https://api.toolkit.umgpt.umich.edu/v1"
    default_model: str = "gpt-5.5"
    # Models approved for study data. None: OpenAI GPT and o-series text
    # models (see relay/policy.py). A lab can pin an explicit list.
    allowed_models: tuple[str, ...] | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    playground: PlaygroundSettings = field(default_factory=PlaygroundSettings)
    repos: RepoSettings = field(default_factory=RepoSettings)
    workflows: WorkflowSettings = field(default_factory=WorkflowSettings)
    updates: UpdateSettings = field(default_factory=UpdateSettings)

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.toml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "datalab.sqlite"


def _practice_db_port() -> int:
    """The synthetic database's port on this computer: 1522, or
    DATALAB_PRACTICE_DB_PORT (tests and development, beside another one).
    A value that isn't a port is refused where practice uses it
    (`practice_db_port_problem`), never at import: the real profile and
    every other command carry on regardless."""
    value = os.environ.get("DATALAB_PRACTICE_DB_PORT") or "1522"
    return int(value) if practice_db_port_problem() is None else 1522


def practice_db_port_problem() -> str | None:
    value = os.environ.get("DATALAB_PRACTICE_DB_PORT") or "1522"
    if not value.isdigit() or not 1024 <= int(value) <= 65535:
        return f"DATALAB_PRACTICE_DB_PORT must be a port number, not {value!r}"
    return None


# The practice profile always uses the local synthetic database, so its
# connection details are fixed (see synthetic/README.md). DataLab runs that
# database itself (datalab.practice_db), on 127.0.0.1 only.
PRACTICE_ORACLE = OracleSettings(
    host="127.0.0.1",
    port=_practice_db_port(),
    service="FREEPDB1",
    user="DATALAB_RO",
    keychain_service="datalab-practice",
    read_only_roles=("IHS_2025_RO", "IHS_2026_RO"),
    allowed_schemas=frozenset({"IHS_2024", "IHS_2025", "IHS_2026"}),
    require_synthetic_marker=True,
)


def _release_agent_image() -> str | None:
    """The agent image a release was built for (written by scripts/build-release.sh)."""
    release = Path(__file__).with_name("release.json")
    if release.exists():
        import json

        return json.loads(release.read_text(encoding="utf-8")).get("agent_image")
    return None


_PINNED = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


def _agent_image(profile: Profile, raw: dict) -> str:
    """The agent image: an override (DATALAB_AGENT_IMAGE, or agent_image in
    settings.toml), else the one the release pins, else the development image.

    In the real profile an override must be pinned by digest: refused in an
    installed release, which always pins its own, and warned about loudly in
    a development copy."""
    release = _release_agent_image()
    override = os.environ.get("DATALAB_AGENT_IMAGE") or raw.get("agent_image")
    if override and profile == "real" and not _PINNED.fullmatch(override):
        if release is not None:
            raise ValueError(
                f"The agent image {override!r} (DATALAB_AGENT_IMAGE or agent_image) isn't "
                "pinned by digest (…@sha256:…). The real DataLab only runs a pinned image."
            )
        import logging

        logging.getLogger(__name__).warning(
            "THE REAL PROFILE IS RUNNING AN AGENT IMAGE NOT PINNED BY DIGEST: %s", override
        )
    return override or release or Settings.agent_image


def default_data_dir(profile: Profile) -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "DataLab"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DataLab"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "datalab"
    return base / profile


def resolve_profile(profile: Profile | None = None) -> Profile:
    """The profile given, else DATALAB_PROFILE, else "real"."""
    return profile or _env_profile()


def data_dir_for(profile: Profile) -> Path:
    """Where a profile's data lives: DATALAB_DATA_DIR if set, else the default."""
    return Path(os.environ.get("DATALAB_DATA_DIR") or default_data_dir(profile))


def load_settings(profile: Profile | None = None) -> Settings:
    profile = resolve_profile(profile)
    data_dir = data_dir_for(profile)
    file = data_dir / "settings.toml"
    raw = tomllib.loads(file.read_text(encoding="utf-8")) if file.exists() else {}

    if profile == "practice":
        problem = practice_db_port_problem()
        if problem:
            raise ValueError(problem)
        oracle: OracleSettings | None = PRACTICE_ORACLE
    else:
        oracle = _oracle_from(raw["oracle"]) if "oracle" in raw else None

    catalog_dir = raw.get("catalog_dir") or os.environ.get("DATALAB_CATALOG_DIR")
    settings = Settings(
        profile=profile,
        data_dir=data_dir,
        oracle=oracle,
        limits=QueryLimits(**raw.get("limits", {})),
        catalog_dir=Path(catalog_dir) if catalog_dir else None,
        agent_image=_agent_image(profile, raw),
        model_base_url=raw.get("model_base_url", Settings.model_base_url),
        default_model=raw.get("default_model", Settings.default_model),
        allowed_models=_allowed_models(raw),
        port=int(raw.get("port", 8766 if profile == "practice" else 8765)),
        # CI only: on Linux, containers reach the host through the Docker
        # bridge, so DataLab must listen beyond 127.0.0.1 there.
        host=os.environ.get("DATALAB_HOST", Settings.host),
        playground=_section(PlaygroundSettings, raw, "playground"),
        repos=_section(RepoSettings, raw, "repos"),
        workflows=_section(WorkflowSettings, raw, "workflows"),
        updates=_section(UpdateSettings, raw, "updates"),
    )
    return _check_models(settings)


def _section[T](cls: type[T], raw: dict, name: str) -> T:
    """One `[name]` table of settings.toml, checked against its fields.

    Unknown keys and values of the wrong type are refused with a message
    naming them, rather than being ignored or failing later.
    """
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ValueError(f"[{name}] in settings.toml must be a table")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(table) - known)
    if unknown:
        raise ValueError(f"Unknown settings in [{name}] in settings.toml: {', '.join(unknown)}")
    for key, value in table.items():
        if not _fits(hints[key], value):
            raise ValueError(f"{name}.{key} in settings.toml has the wrong type: {value!r}")
    return cls(**table)


def _fits(hint: Any, value: Any) -> bool:
    """Whether a settings value has its field's declared type. Covers the
    types these sections use: bool, int, float, str, and unions such as
    `str | None` (TOML has no null, so None never arrives)."""
    if get_origin(hint) in (UnionType, Union):
        return any(_fits(option, value) for option in get_args(hint))
    if hint is type(None):
        return value is None
    if hint is bool:
        return isinstance(value, bool)
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if hint is float:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if hint is str:
        return isinstance(value, str)
    raise TypeError(f"settings of type {hint!r} aren't supported in sections yet")


def _allowed_models(raw: dict) -> tuple[str, ...] | None:
    if "allowed_models" not in raw:
        return None
    value = raw["allowed_models"]
    if not isinstance(value, list) or not all(isinstance(m, str) for m in value):
        raise ValueError("allowed_models in settings.toml must be a list of model names")
    return tuple(value)


def _check_models(settings: Settings) -> Settings:
    from datalab.relay.policy import model_allowed

    if not model_allowed(settings.default_model, settings.allowed_models):
        raise ValueError(
            f"default_model {settings.default_model!r} isn't an approved model "
            "(see allowed_models in settings.toml)"
        )
    return settings


def _env_profile() -> Profile:
    value = os.environ.get("DATALAB_PROFILE", "real")
    if value not in ("real", "practice"):
        raise ValueError(f"DATALAB_PROFILE must be 'real' or 'practice', not {value!r}")
    return value  # type: ignore[return-value]


def _oracle_from(raw: dict) -> OracleSettings:
    keychain_service = raw.get("keychain_service", "datalab-oracle")
    if keychain_service == PRACTICE_ORACLE.keychain_service:
        # Practice DataLab saves the synthetic database's public password there.
        raise ValueError(
            f"[oracle] keychain_service can't be {keychain_service!r}: that's practice "
            "DataLab's own keychain entry."
        )
    return OracleSettings(
        host=raw["host"],
        port=int(raw.get("port", 1521)),
        service=raw["service"],
        user=raw["user"],
        keychain_service=keychain_service,
        read_only_roles=tuple(r.upper() for r in raw.get("read_only_roles", [])),
        allowed_schemas=frozenset(s.upper() for s in raw["allowed_schemas"]),
    )
