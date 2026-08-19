"""Configuration loading.

One TOML file holds everything. Every scalar can be overridden by an
environment variable named FITBIT_SYNC_<SECTION>_<KEY>, which is what makes the
systemd unit and the Dockerfile able to relocate paths without editing files.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

ENV_PREFIX = "FITBIT_SYNC"

# Scope aliases that config.auth.extra_scopes accepts.
OPTIONAL_SCOPES = {
    "ecg": "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
    "irn": "https://www.googleapis.com/auth/googlehealth.irn.readonly",
    "location": "https://www.googleapis.com/auth/googlehealth.location.readonly",
}

# The minimum set needed for the data types in FR-2, plus the two OIDC scopes
# that make the Workspace account precondition checkable before any health call.
BASE_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
)


class ConfigError(Exception):
    """Raised for a malformed or unusable configuration."""


@dataclass
class AuthConfig:
    client_secret_file: Path = Path("./secrets/client_secret.json")
    token_file: Path = Path("./secrets/token.json")
    extra_scopes: list[str] = field(default_factory=list)
    oauth_local_port: int = 0

    def scopes(self) -> list[str]:
        scopes = list(BASE_SCOPES)
        for alias in self.extra_scopes:
            key = alias.strip().lower()
            if key in OPTIONAL_SCOPES:
                scopes.append(OPTIONAL_SCOPES[key])
            elif key.startswith("https://"):
                scopes.append(alias.strip())
            else:
                raise ConfigError(
                    f"auth.extra_scopes: unknown scope {alias!r}. "
                    f"Known aliases: {', '.join(sorted(OPTIONAL_SCOPES))}"
                )
        # Preserve order, drop duplicates.
        return list(dict.fromkeys(scopes))


@dataclass
class DatabaseConfig:
    path: Path = Path("./data/fitbit.sqlite3")


@dataclass
class SyncConfig:
    window_days: int = 3
    data_source_family: str = "all-sources"
    prefer_reconciled: bool = True
    max_requests_per_minute: int = 60
    max_retries: int = 6
    backfill_start: str = ""
    skip_data_types: list[str] = field(default_factory=list)
    # Override the API root. Only useful for pointing at a local mock server
    # during testing; leave it empty to talk to Google.
    base_url: str = ""


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8722
    expose_lan: bool = False

    def bind_host(self) -> str:
        return "0.0.0.0" if self.expose_lan else self.host


@dataclass
class ReportConfig:
    timezone: str = ""


@dataclass
class LoggingConfig:
    format: str = "text"
    level: str = "INFO"
    file: str = ""


@dataclass
class Config:
    auth: AuthConfig = field(default_factory=AuthConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    source_path: Path | None = None


def _coerce(value: Any, target_type: Any, where: str) -> Any:
    if target_type is Path:
        return Path(str(value)).expanduser()
    if target_type is bool:
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"{where}: expected a boolean, got {value!r}")
    if target_type is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where}: expected an integer, got {value!r}") from exc
    if target_type is str:
        return str(value)
    if target_type == list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item) for item in value]
        raise ConfigError(f"{where}: expected a list of strings, got {value!r}")
    return value


def _field_types(section: Any) -> dict[str, Any]:
    """Real types for a dataclass's fields.

    `from __future__ import annotations` leaves dataclasses.fields()[i].type as
    the *string* "int", so coercion has to resolve the annotations first.
    """
    hints = get_type_hints(type(section))
    return {f.name: hints[f.name] for f in fields(section)}


def _apply_section(section: Any, values: dict[str, Any], name: str) -> None:
    known = _field_types(section)
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"[{name}]: unknown key {key!r}")
        setattr(section, key, _coerce(value, known[key], f"[{name}].{key}"))


def _apply_env(config: Config) -> None:
    for section_field in fields(config):
        section = getattr(config, section_field.name)
        if not is_dataclass(section):
            continue
        types = _field_types(section)
        for value_field in fields(section):
            env_name = f"{ENV_PREFIX}_{section_field.name.upper()}_{value_field.name.upper()}"
            if env_name in os.environ:
                setattr(
                    section,
                    value_field.name,
                    _coerce(os.environ[env_name], types[value_field.name], env_name),
                )


def default_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve which config file to read.

    Order: explicit argument, FITBIT_SYNC_CONFIG, ./config.toml.
    """
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(f"{ENV_PREFIX}_CONFIG")
    if from_env:
        return Path(from_env).expanduser()
    return Path("config.toml")


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration, tolerating a missing file so defaults just work."""
    config_path = default_config_path(path)
    config = Config()

    if config_path.exists():
        with config_path.open("rb") as handle:
            try:
                raw = tomllib.load(handle)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{config_path}: {exc}") from exc
        known_sections = {f.name for f in fields(config)} - {"source_path"}
        for section_name, values in raw.items():
            if section_name not in known_sections:
                raise ConfigError(f"{config_path}: unknown section [{section_name}]")
            if not isinstance(values, dict):
                raise ConfigError(f"{config_path}: [{section_name}] must be a table")
            _apply_section(getattr(config, section_name), values, section_name)
        config.source_path = config_path
    elif path is not None:
        raise ConfigError(f"config file not found: {config_path}")

    _apply_env(config)

    # Relative paths resolve against the config file's directory when there is
    # one, so a systemd unit with a different working directory still works.
    base = config_path.parent.resolve() if config.source_path else Path.cwd()
    config.auth.client_secret_file = _resolve(base, config.auth.client_secret_file)
    config.auth.token_file = _resolve(base, config.auth.token_file)
    config.database.path = _resolve(base, config.database.path)

    if config.sync.window_days < 1:
        raise ConfigError("[sync].window_days must be at least 1")
    if config.sync.max_requests_per_minute < 1:
        raise ConfigError("[sync].max_requests_per_minute must be at least 1")
    if config.sync.data_source_family not in {
        "all-sources",
        "google-wearables",
        "google-sources",
    }:
        raise ConfigError(
            "[sync].data_source_family must be one of: all-sources, "
            "google-wearables, google-sources"
        )
    return config


def _resolve(base: Path, value: Path) -> Path:
    return value if value.is_absolute() else (base / value).resolve()
