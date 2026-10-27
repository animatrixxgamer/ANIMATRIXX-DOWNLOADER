"""The bot must point at the right Telegram API server (hosted vs local)."""

from __future__ import annotations

import pytest

from app.bot import build_application
from app.config import Settings

TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg"


def build(**overrides) -> Settings:
    settings = Settings(_env_file=None, bot_token=TOKEN, **overrides)
    return settings, build_application(settings)


def test_default_targets_hosted_telegram():
    _, app = build()
    assert app.bot.base_url.startswith("https://api.telegram.org/bot")
    assert app.bot.base_file_url.startswith("https://api.telegram.org/file/bot")


def test_local_api_rewires_both_endpoints():
    settings, app = build(
        local_bot_api_enabled=True,
        local_bot_api_base_url="http://telegram-bot-api:8081/bot",
        local_bot_api_file_url="http://telegram-bot-api:8081/file/bot",
    )
    assert app.bot.base_url.startswith(settings.local_bot_api_base_url)
    assert app.bot.base_file_url.startswith(settings.local_bot_api_file_url)
    # the token is appended by PTB, so requests carry credentials
    assert TOKEN in app.bot.base_url
    assert TOKEN in app.bot.base_file_url


def test_local_loopback_urls_are_accepted_for_local_dev():
    settings, app = build(
        local_bot_api_enabled=True,
        local_bot_api_base_url="http://localhost:8081/bot",
        local_bot_api_file_url="http://localhost:8081/file/bot",
    )
    assert settings.upload_limit_mb == 2000
    assert app.bot.base_url.startswith("http://localhost:8081/bot")


def test_job_manager_sees_the_lifted_limit():
    settings, app = build(local_bot_api_enabled=True)
    manager = app.bot_data["job_manager"]
    assert manager.settings.upload_limit_mb == 2000
    assert settings is manager.settings