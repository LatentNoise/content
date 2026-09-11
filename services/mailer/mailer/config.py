"""Settings, read once from the environment.

Every secret arrives as an environment variable and never as a literal in
this repository. The SMTP host is treated as a secret too: the provider
names it after the account, and that account name must not appear in public
output. `describe()` is what the API is allowed to show.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised when the process is asked to start without what it needs."""


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _parse_api_keys(raw: str) -> dict[str, str]:
    """`name:secret,other:secret` — a named key per calling product.

    Names exist so a log line says *which* product sent a message, and so one
    product's key can be revoked without touching the others.
    """
    keys: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, secret = chunk.partition(":")
        if not sep or not name.strip() or not secret.strip():
            raise ConfigError("MAILER_API_KEYS entries must look like 'name:secret'")
        keys[secret.strip()] = name.strip()
    if not keys:
        raise ConfigError("MAILER_API_KEYS must define at least one key")
    return keys


@dataclass(frozen=True)
class Settings:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_security: str
    default_from: str
    api_keys: dict[str, str] = field(repr=False, default_factory=dict)
    db_path: str = "/data/mailer.db"
    max_attempts: int = 5
    retry_base_seconds: int = 30
    poll_seconds: float = 2.0
    dry_run: bool = False

    def describe(self) -> dict[str, object]:
        """Facts safe to return over HTTP. No host, no user, no secret."""
        return {
            "smtp_security": self.smtp_security,
            "default_from": self.default_from,
            "clients": sorted(set(self.api_keys.values())),
            "max_attempts": self.max_attempts,
            "dry_run": self.dry_run,
        }


def load_settings() -> Settings:
    dry_run = _bool("MAILER_DRY_RUN", False)
    security = (os.getenv("MAILER_SMTP_SECURITY") or "ssl").strip().lower()
    if security not in {"ssl", "starttls"}:
        raise ConfigError("MAILER_SMTP_SECURITY must be 'ssl' or 'starttls'")
    return Settings(
        smtp_host="" if dry_run else _require("MAILER_SMTP_HOST"),
        smtp_port=_int("MAILER_SMTP_PORT", 465 if security == "ssl" else 587),
        smtp_user="" if dry_run else _require("MAILER_SMTP_USER"),
        smtp_password="" if dry_run else _require("MAILER_SMTP_PASSWORD"),
        smtp_security=security,
        default_from=_require("MAILER_DEFAULT_FROM"),
        api_keys=_parse_api_keys(_require("MAILER_API_KEYS")),
        db_path=(os.getenv("MAILER_DB_PATH") or "/data/mailer.db").strip(),
        max_attempts=_int("MAILER_MAX_ATTEMPTS", 5),
        retry_base_seconds=_int("MAILER_RETRY_BASE_SECONDS", 30),
        dry_run=dry_run,
    )
