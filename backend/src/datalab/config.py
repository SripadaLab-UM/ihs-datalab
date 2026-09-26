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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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


def load_settings(profile: Profile | None = None) -> Settings:
    profile = profile or _env_profile()
    data_dir = Path(os.environ.get("DATALAB_DATA_DIR") or default_data_dir(profile))
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
    )
    return _check_models(settings)


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
