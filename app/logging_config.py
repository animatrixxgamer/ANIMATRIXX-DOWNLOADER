"""Structured logging setup with secret redaction."""

from __future__ import annotations

import logging
import logging.config
import re
from pathlib import Path

# Telegram bot tokens look like `123456789:AA...`; never let one reach a log file.
_SECRET_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


class RedactingFilter(logging.Filter):
    """Scrub anything that looks like a bot token from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = _SECRET_RE.sub("***REDACTED***", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(log_dir: Path | str, level: str = "INFO") -> None:
    """Configure console + rotating file logging for the whole process."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "animatrixx.log"

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"redact": {"()": "app.logging_config.RedactingFilter"}},
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "verbose": {
                "format": "%(asctime)s %(levelname)-8s %(name)s [%(filename)s:%(lineno)d]: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "standard",
                "filters": ["redact"],
                "stream": "ext://sys.stderr",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "verbose",
                "filters": ["redact"],
                "filename": str(log_file),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
        },
        "root": {"level": level, "handlers": ["console", "file"]},
    }
    logging.config.dictConfig(config)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


__all__ = ["configure_logging", "RedactingFilter"]
