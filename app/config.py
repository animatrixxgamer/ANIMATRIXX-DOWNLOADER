"""Validated configuration loaded from the environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Every value can be supplied through an environment variable of the same
    name (upper case) or through a local ``.env`` file.  Startup fails loudly
    when a required value is missing - the bot never runs half configured.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core -------------------------------------------------------
    bot_token: str = Field(default="", description="Telegram bot token from @BotFather")
    owner_id: int = 0
    admin_ids: str = ""

    # --- storage ----------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/animatrixx.db"
    temp_dir: Path = Path("./temp")
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"

    # --- metadata providers ----------------------------------------
    tmdb_api_key: str = ""
    metadata_cache_ttl: int = 3600

    # --- branding ---------------------------------------------------
    bot_username: str = ""

    # --- validation -------------------------------------------------
    @field_validator("bot_token")
    @classmethod
    def _token_sane(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("BOT_TOKEN is required - create a bot with @BotFather")
        if any(ch.isspace() for ch in value):
            raise ValueError("BOT_TOKEN must not contain whitespace")
        return value

    @field_validator("admin_ids")
    @classmethod
    def _admins_sane(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return value
        for raw in value.replace(" ", ",").split(","):
            if not raw:
                continue
            if not raw.lstrip("-").isdigit():
                raise ValueError(f"ADMIN_IDS must be comma separated integers, got {raw!r}")
        return value

    @field_validator("log_level")
    @classmethod
    def _level_sane(cls, value: str) -> str:
        value = (value or "INFO").upper()
        if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"Invalid LOG_LEVEL: {value}")
        return value

    @field_validator("metadata_cache_ttl")
    @classmethod
    def _ttl_positive(cls, value: int) -> int:
        if value < 0:
            raise ValueError("METADATA_CACHE_TTL must be >= 0")
        return value

    # --- derived ----------------------------------------------------
    @property
    def administrators(self) -> frozenset[int]:
        """Owner plus every explicitly configured administrator."""
        ids: set[int] = set()
        if self.owner_id:
            ids.add(self.owner_id)
        for raw in self.admin_ids.replace(" ", ",").split(","):
            raw = raw.strip()
            if raw.lstrip("-").isdigit():
                ids.add(int(raw))
        return frozenset(ids)

    @property
    def tmdb_enabled(self) -> bool:
        return bool(self.tmdb_api_key.strip())


def load_settings() -> Settings:
    """Load and validate configuration. Raises ``pydantic.ValidationError``."""
    return Settings()


__all__ = ["Settings", "load_settings"]
