"""Configuration from environment variables."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .timeutil import load_timezone

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})

DEFAULT_CACHE_TTL = 900.0


def _parse_bool(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of 1/0, true/false, yes/no or on/off.")


def _parse_seconds(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number of seconds.") from exc
    if not math.isfinite(value) or value < 0:
        raise ConfigError(f"{name} must be a non-negative number of seconds.")
    return value


def _read_token(environ: Mapping[str, str]) -> str | None:
    token = environ.get("TOGGL_API_TOKEN", "").strip()
    if token:
        return token
    token_file = environ.get("TOGGL_API_TOKEN_FILE", "").strip()
    if not token_file:
        return None
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(
            f"Cannot read TOGGL_API_TOKEN_FILE ({exc.strerror or type(exc).__name__})."
        ) from exc
    return token or None


@dataclass(frozen=True)
class Config:
    """Server settings.

    Environment variables:
        TOGGL_API_TOKEN       API token from https://track.toggl.com/profile
        TOGGL_API_TOKEN_FILE  Path to a file containing the token (used if
                              TOGGL_API_TOKEN is unset)
        TOGGL_READ_ONLY       If true, tools that change Toggl data are not exposed
        TOGGL_TIMEZONE        IANA timezone overriding the Toggl profile timezone
        TOGGL_CACHE_TTL       Seconds to cache workspaces/projects/clients (default 900)
    """

    api_token: str | None
    read_only: bool = False
    timezone: str | None = None
    cache_ttl: float = DEFAULT_CACHE_TTL

    def __repr__(self) -> str:
        token = "set" if self.api_token else "unset"
        return (
            f"Config(api_token=<{token}>, read_only={self.read_only}, "
            f"timezone={self.timezone!r}, cache_ttl={self.cache_ttl})"
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        env = os.environ if environ is None else environ
        timezone = env.get("TOGGL_TIMEZONE", "").strip() or None
        if timezone and load_timezone(timezone) is None:
            raise ConfigError(f"TOGGL_TIMEZONE {timezone!r} is not a known IANA timezone.")
        return cls(
            api_token=_read_token(env),
            read_only=_parse_bool(env, "TOGGL_READ_ONLY"),
            timezone=timezone,
            cache_ttl=_parse_seconds(env, "TOGGL_CACHE_TTL", DEFAULT_CACHE_TTL),
        )
