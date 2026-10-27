"""Configuration validation - startup must fail loudly, not half configured."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides) -> Settings:
    base = {"bot_token": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_valid_settings_load():
    s = _settings(owner_id=42, admin_ids="7, 8")
    assert s.owner_id == 42
    assert s.administrators == frozenset({42, 7, 8})
    assert s.metadata_cache_ttl == 3600
    assert s.tmdb_enabled is False


def test_missing_bot_token_rejected(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_empty_bot_token_rejected():
    with pytest.raises(ValidationError):
        _settings(bot_token="")


def test_whitespace_token_rejected():
    with pytest.raises(ValidationError):
        _settings(bot_token="123456:AAAA AAAA")


def test_bad_admin_ids_rejected():
    with pytest.raises(ValidationError):
        _settings(admin_ids="not-a-number")


def test_admin_ids_accepts_spaces_and_commas():
    s = _settings(owner_id=1, admin_ids=" 2,3  ,4")
    assert s.administrators == frozenset({1, 2, 3, 4})


def test_invalid_log_level_rejected():
    with pytest.raises(ValidationError):
        _settings(log_level="LOUD")


def test_negative_cache_ttl_rejected():
    with pytest.raises(ValidationError):
        _settings(metadata_cache_ttl=-1)


def test_tmdb_flag_follows_key():
    assert _settings(tmdb_api_key="").tmdb_enabled is False
    assert _settings(tmdb_api_key="abc123").tmdb_enabled is True
