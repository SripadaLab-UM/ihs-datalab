"""DataLab settings.

Settings come from `settings.toml` in the profile's data folder, with a few
environment-variable overrides for development and CI. Nothing
environment-specific (hostnames, account names) is hard-coded here: the real
profile's database details live only in the user's settings file.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

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
    # a marker table that exists only there (see synthetic/guard.py).
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

    None means not configured: nothing is cloned or synced. To come: the
    GitHub App's client id, how often to sync, and the branch to follow.
    """

    # e.g. "SripadaLab-UM/ihs-knowledge"
    knowledge: str | None = None
    # e.g. "SripadaLab-UM/ihs-pipelines"
    pipelines: str | None = None


@dataclass(frozen=True)
class WorkflowSettings:
    """`[workflows]`: the workflow runner (milestone 6).

    To come: time limits and container limits for R steps, how long run
    folders are kept.
    """

    # Workflow runs going at once. Their SQL steps also share the data
    # service's query slots (`limits.max_concurrent_queries`).
    max_concurrent_runs: int = 1

    def __post_init__(self) -> None:
        if self.max_concurrent_runs < 1:
            raise ValueError("workflows.max_concurrent_runs must be at least 1")


@dataclass(frozen=True)
class UpdateSettings:
    """`[updates]`: checking for and installing new releases (milestone 7).

    To come: where backups go and how many are kept (see DISTRIBUTION.md).
    """

    # Whether DataLab asks GitHub for a newer release when it starts. Nothing
    # checks yet; the updater will read this.
    check_on_start: bool = True
    # Where releases come from.
    repository: str = "SripadaLab-UM/ihs_datalab"


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


# The practice profile always uses the local synthetic database, so its
# connection details are fixed (see synthetic/README.md).
PRACTICE_ORACLE = OracleSettings(
    host="127.0.0.1",
    port=1522,
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

        return json.loads(release.read_text()).get("agent_image")
    return None


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
    raw = tomllib.loads(file.read_text()) if file.exists() else {}

    if profile == "practice":
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
        agent_image=os.environ.get("DATALAB_AGENT_IMAGE")
        or raw.get("agent_image")
        or _release_agent_image()
        or Settings.agent_image,
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
    known = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(table) - set(known))
    if unknown:
        raise ValueError(f"Unknown settings in [{name}] in settings.toml: {', '.join(unknown)}")
    for key, value in table.items():
        if not _fits(known[key].default, value):
            raise ValueError(f"{name}.{key} in settings.toml has the wrong type: {value!r}")
    return cls(**table)


def _fits(default: Any, value: Any) -> bool:
    """Whether a settings value has the type its field's default has."""
    if default is None:  # the optional text fields
        return isinstance(value, str)
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, type(default))


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
    return OracleSettings(
        host=raw["host"],
        port=int(raw.get("port", 1521)),
        service=raw["service"],
        user=raw["user"],
        keychain_service=raw.get("keychain_service", "datalab-oracle"),
        read_only_roles=tuple(r.upper() for r in raw.get("read_only_roles", [])),
        allowed_schemas=frozenset(s.upper() for s in raw["allowed_schemas"]),
    )
