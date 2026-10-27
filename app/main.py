"""Entry point: ``python -m app.main`` or ``python app/main.py``."""

from __future__ import annotations

import logging
import sys

from telegram import Update

from app.bot import build_application
from app.config import load_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> int:
    try:
        settings = load_settings()
    except Exception as exc:  # pydantic.ValidationError
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_dir, settings.log_level)
    logger.info("Starting Animatrixx Anime Bot…")

    app = build_application(settings)
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        close_loop=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
