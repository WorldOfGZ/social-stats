from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass(slots=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    timeout_seconds: int = 12


@dataclass(slots=True)
class TargetsConfig:
    instagram: list[str] = field(default_factory=list)
    youtube: list[str] = field(default_factory=list)
    github_users: list[str] = field(default_factory=list)
    github_repos: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CacheConfig:
    enabled: bool = True
    refresh_seconds: int = 3600


@dataclass(slots=True)
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    targets: TargetsConfig = field(default_factory=TargetsConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)


def _as_string_list(raw: object, key_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError(f"'{key_name}' must be a list of strings")
    return raw


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ConfigError("Config root must be a mapping")

    server_raw = parsed.get("server", {})
    targets_raw = parsed.get("targets", {})
    cache_raw = parsed.get("cache", {})

    if not isinstance(server_raw, dict):
        raise ConfigError("'server' must be a mapping")
    if not isinstance(targets_raw, dict):
        raise ConfigError("'targets' must be a mapping")
    if not isinstance(cache_raw, dict):
        raise ConfigError("'cache' must be a mapping")

    server = ServerConfig(
        host=str(server_raw.get("host", "0.0.0.0")),
        port=int(server_raw.get("port", 8000)),
        timeout_seconds=int(server_raw.get("timeout_seconds", 12)),
    )

    targets = TargetsConfig(
        instagram=_as_string_list(targets_raw.get("instagram"), "targets.instagram"),
        youtube=_as_string_list(targets_raw.get("youtube"), "targets.youtube"),
        github_users=_as_string_list(targets_raw.get("github_users"), "targets.github_users"),
        github_repos=_as_string_list(targets_raw.get("github_repos"), "targets.github_repos"),
    )

    cache = CacheConfig(
        enabled=bool(cache_raw.get("enabled", True)),
        refresh_seconds=max(1, int(cache_raw.get("refresh_seconds", 3600))),
    )

    return AppConfig(server=server, targets=targets, cache=cache)
