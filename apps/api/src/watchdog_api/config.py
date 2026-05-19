"""Pydantic Settings v2 for wk-watchdog API.

Reads `WATCHDOG_*` environment variables (optionally via a `.env`
file). Validates the SQLite URL scheme at settings-load time;
filesystem writability is checked at app-startup time (see
`watchdog_api.main._lifespan`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SQLITE_URL_PREFIX = "sqlite+aiosqlite:///"


class Settings(BaseSettings):
    """Runtime settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_prefix="WATCHDOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104  bind-all needed for Docker / Traefik
    api_port: int = 8000
    db_url: str = f"{_SQLITE_URL_PREFIX}./data/watchdog.sqlite"

    # --- Alerting (Turn 6) ---
    webhook_target_url: str = ""  # empty → worker is NOT started by lifespan
    webhook_secret: str = "dev-secret-change-me"  # noqa: S105 — placeholder; production uses op://
    worker_poll_interval_seconds: float = 1.0

    # --- Observability (Turn 7) ---
    otel_exporter_otlp_endpoint: str = (
        ""  # empty → OTel still configured but exporter is a no-op-friendly default
    )
    otel_enabled: bool = True  # tests may set False to skip OTel setup entirely

    @field_validator("db_url")
    @classmethod
    def _ensure_sqlite_url(cls, v: str) -> str:
        if not v.startswith(_SQLITE_URL_PREFIX):
            msg = f"db_url must start with {_SQLITE_URL_PREFIX!r}; got {v!r}"
            raise ValueError(msg)
        return v

    @property
    def db_path(self) -> Path:
        """Bare filesystem path inside the SQLite URL."""
        return Path(self.db_url[len(_SQLITE_URL_PREFIX) :])


def load_settings() -> Settings:
    """Load settings from environment.

    A free function (not a module-level singleton) so tests can
    construct Settings with overrides without monkeypatching globals.
    """
    return Settings()
