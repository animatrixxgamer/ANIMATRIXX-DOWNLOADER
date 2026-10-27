"""Validated configuration loaded from the environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
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

    # --- pipeline: concurrency -------------------------------------
    max_concurrent_downloads: int = 2
    max_concurrent_encodings: int = 1
    max_concurrent_uploads: int = 2
    max_jobs_per_user: int = 2

    # --- pipeline: download safety ---------------------------------
    max_download_size_mb: int = 2048
    min_free_disk_mb: int = 2048
    download_connect_timeout: float = 15.0
    download_read_timeout: float = 60.0
    max_redirects: int = 5
    allowed_download_ports: str = "80,443"
    # Development/test only: lets the downloader reach a local HTTP server.
    allow_private_downloads: bool = False

    # --- pipeline: retries -----------------------------------------
    max_job_attempts: int = 3
    retry_backoff_seconds: int = 30

    # --- pipeline: encoding ----------------------------------------
    default_output_format: str = "mp4"
    default_encoding_profile: str = "balanced"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    encode_timeout_seconds: int = 3600

    # --- pipeline: storage / upload --------------------------------
    autodel_enabled: bool = True
    autodel_hours: int = 24
    # Hosted Bot API caps bot uploads well below large anime files.
    telegram_upload_limit_mb: int = 49
    # Local Bot API Server (tdlib/telegram-bot-api) lifts uploads to ~2 GB.
    local_bot_api_enabled: bool = False
    local_bot_api_base_url: str = "http://localhost:8081/bot"
    local_bot_api_file_url: str = "http://localhost:8081/file/bot"
    local_bot_api_max_mb: int = 2000
    upload_timeout_seconds: int = 600

    # --- messaging -------------------------------------------------
    poll_interval_seconds: float = 2.0
    progress_update_interval: float = 2.0

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

    _positive_ints = (
        "max_concurrent_downloads",
        "max_concurrent_encodings",
        "max_concurrent_uploads",
        "max_jobs_per_user",
        "max_download_size_mb",
        "min_free_disk_mb",
        "max_redirects",
        "max_job_attempts",
        "retry_backoff_seconds",
        "encode_timeout_seconds",
        "autodel_hours",
        "telegram_upload_limit_mb",
        "local_bot_api_max_mb",
        "upload_timeout_seconds",
    )

    @classmethod
    def _check_positive(cls, name: str, value: int) -> int:
        if value < 1:
            raise ValueError(f"{name.upper()} must be >= 1")
        return value

    @field_validator(*_positive_ints)
    @classmethod
    def _positive(cls, value: int, info) -> int:
        return cls._check_positive(info.field_name, value)

    @field_validator("default_encoding_profile")
    @classmethod
    def _profile_known(cls, value: str) -> str:
        allowed = {"original", "high", "balanced", "small", "target"}
        value = (value or "").strip().lower()
        if value not in allowed:
            raise ValueError(f"DEFAULT_ENCODING_PROFILE must be one of {sorted(allowed)}")
        return value

    @field_validator("default_output_format")
    @classmethod
    def _format_known(cls, value: str) -> str:
        value = (value or "").strip().lower().lstrip(".")
        if value not in {"mp4", "mkv"}:
            raise ValueError("DEFAULT_OUTPUT_FORMAT must be mp4 or mkv")
        return value

    @field_validator("allowed_download_ports")
    @classmethod
    def _ports_sane(cls, value: str) -> str:
        ports = [p.strip() for p in (value or "").replace(" ", ",").split(",") if p.strip()]
        if not ports:
            raise ValueError("ALLOWED_DOWNLOAD_PORTS must list at least one port")
        for port in ports:
            if not port.isdigit() or not (1 <= int(port) <= 65535):
                raise ValueError(f"Invalid port in ALLOWED_DOWNLOAD_PORTS: {port}")
        return ",".join(ports)

    @field_validator("poll_interval_seconds", "progress_update_interval", "download_connect_timeout", "download_read_timeout")
    @classmethod
    def _positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timing values must be > 0")
        return value

    @property
    def allowed_ports(self) -> frozenset[int]:
        return frozenset(int(p) for p in self.allowed_download_ports.split(",") if p.strip())

    @model_validator(mode="after")
    def _local_api_sane(self) -> "Settings":
        if not self.local_bot_api_enabled:
            return self
        for name, url in (
            ("LOCAL_BOT_API_BASE_URL", self.local_bot_api_base_url),
            ("LOCAL_BOT_API_FILE_URL", self.local_bot_api_file_url),
        ):
            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"{name} must be an http(s) URL when LOCAL_BOT_API_ENABLED=true"
                )
        if not self.local_bot_api_base_url.rstrip("/").endswith("/bot"):
            raise ValueError(
                "LOCAL_BOT_API_BASE_URL must end with /bot "
                "(e.g. http://telegram-bot-api:8081/bot)"
            )
        if not self.local_bot_api_file_url.rstrip("/").endswith("/file/bot"):
            raise ValueError(
                "LOCAL_BOT_API_FILE_URL must end with /file/bot "
                "(e.g. http://telegram-bot-api:8081/file/bot)"
            )
        return self

    @property
    def upload_limit_mb(self) -> int:
        return self.local_bot_api_max_mb if self.local_bot_api_enabled else self.telegram_upload_limit_mb

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
