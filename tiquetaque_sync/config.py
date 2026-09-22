"""Configuration management via Pydantic Settings.

Precedence, highest first: environment variables > ``.env`` in the working
directory > the per-user ``config.json`` written by the app > defaults.
Docker and Kubernetes keep driving everything through environment variables;
a desktop install has none set, so the settings screen in the web UI wins.
"""

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from . import paths, store


class UserConfigSettingsSource(PydanticBaseSettingsSource):
    """Settings source backed by the per-user ``config.json``."""

    def get_field_value(self, field, field_name):  # pragma: no cover - unused hook
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return store.load()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # TiqueTaque Credentials
    tiquetaque_email: str = Field(default="")
    tiquetaque_code: str = Field(default="")
    tiquetaque_token: str | None = None
    tiquetaque_employee_id: str | None = None
    tiquetaque_full_name: str | None = None

    # Workday & Polling Settings
    poll_interval_seconds: int = Field(default=180, ge=30, description="Interval in seconds between TiqueTaque API polls")
    timezone: str = Field(default="America/Sao_Paulo")
    work_hours_per_day: float = Field(default=8.0, gt=0, le=24)
    lunch_duration_minutes: int = Field(default=60, ge=0)
    lunch_warning_advance_minutes: int = Field(default=10, ge=0)
    lunch_warning_final_minutes: int = Field(default=1, ge=0)
    end_work_warning_advance_minutes: int = Field(default=15, ge=0)
    end_work_warning_final_minutes: int = Field(default=1, ge=0)
    continuous_work_limit_hours: float = Field(default=6.0, gt=0, le=24)
    continuous_work_warning_advance_minutes: int = Field(default=10, ge=0)
    continuous_work_warning_final_minutes: int = Field(default=1, ge=0)
    alert_ticker_interval_seconds: int = Field(default=15, ge=5)

    # Telegram
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Slack
    slack_enabled: bool = False
    slack_webhook_url: str | None = None

    # Server & Storage
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Field(default_factory=paths.data_dir)
    open_browser_on_start: bool = True
    start_minimized: bool = False
    auto_check_updates: bool = True
    api_secret_key: str = "default-insecure-secret"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            UserConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tiquetaque.db"

    @property
    def is_configured(self) -> bool:
        """True once the TiqueTaque credentials needed to sync are present."""
        return bool(self.tiquetaque_email and self.tiquetaque_code) or bool(self.tiquetaque_token)

    @property
    def dashboard_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"


settings = Settings()


def reload_settings() -> Settings:
    """Re-read every source and update the shared ``settings`` object in place.

    Mutating the singleton keeps every ``from .config import settings`` import
    pointing at the fresh values after the user saves the settings screen.
    """
    fresh = Settings()
    for key, value in fresh.model_dump().items():
        setattr(settings, key, value)
    return settings


__all__ = ["Settings", "settings", "reload_settings", "paths"]
