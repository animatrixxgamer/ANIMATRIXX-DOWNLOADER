"""Upload pre-flight rules - checked before a single byte leaves the VPS."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import TelegramError

from app.services.telegram_storage import caption_for, preflight


class FakeBot:
    def __init__(self, status: str = "administrator", error: Exception | None = None):
        self.id = 777
        self.status = status
        self.error = error

    async def get_chat_member(self, chat_id, user_id):
        if self.error:
            raise self.error
        return SimpleNamespace(status=self.status)


def make_file(tmp_path: Path, size: int = 1024) -> Path:
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"\x00" * size)
    return path


async def test_preflight_ok(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(),
        chat_id=-1001,
        path=path,
        upload_limit_mb=49,
        temp_dir=tmp_path,
        min_free_mb=1,
        cancelled=False,
    )
    assert result.ok is True


async def test_preflight_requires_channel(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(), chat_id=None, path=path, upload_limit_mb=49,
        temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.ok is False
    assert result.code == "no_channel"


async def test_preflight_missing_file(tmp_path):
    result = await preflight(
        bot=FakeBot(), chat_id=-1001, path=tmp_path / "gone.mp4",
        upload_limit_mb=49, temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.code == "missing_file"


async def test_preflight_rejects_files_over_the_telegram_limit(tmp_path):
    path = make_file(tmp_path, size=60 * 2**20)
    result = await preflight(
        bot=FakeBot(), chat_id=-1001, path=path, upload_limit_mb=49,
        temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.code == "too_large"
    assert "Local Bot API" in result.reason


async def test_preflight_accepts_large_files_when_local_api_enabled(tmp_path):
    path = make_file(tmp_path, size=60 * 2**20)
    result = await preflight(
        bot=FakeBot(), chat_id=-1001, path=path, upload_limit_mb=2000,
        temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.ok is True


async def test_preflight_detects_lost_channel_access(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(status="member"), chat_id=-1001, path=path,
        upload_limit_mb=49, temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.code == "no_access"


async def test_preflight_survives_telegram_errors(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(error=TelegramError("boom")), chat_id=-1001, path=path,
        upload_limit_mb=49, temp_dir=tmp_path, min_free_mb=1, cancelled=False,
    )
    assert result.ok is False
    assert result.code == "no_access"


async def test_preflight_reports_insufficient_disk(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(), chat_id=-1001, path=path, upload_limit_mb=49,
        temp_dir=tmp_path, min_free_mb=10**9, cancelled=False,  # 1 TB reserve
    )
    assert result.code == "disk"


async def test_preflight_reports_cancellation_first(tmp_path):
    path = make_file(tmp_path)
    result = await preflight(
        bot=FakeBot(), chat_id=-1001, path=path, upload_limit_mb=49,
        temp_dir=tmp_path, min_free_mb=1, cancelled=True,
    )
    assert result.code == "cancelled"


def test_caption_identifies_the_file():
    caption = caption_for(
        series_title="Solo Leveling",
        season=1,
        episode=4,
        resolution="1080p",
        profile="balanced",
        filename="solo.s01e04.mkv",
        duration="24:00",
    )
    assert "Solo Leveling" in caption
    assert "S01E04" in caption
    assert "1080P" in caption
    assert "BALANCED" in caption
    assert "solo.s01e04.mkv" in caption


def test_caption_without_series():
    caption = caption_for(
        series_title=None, season=1, episode=None, resolution=None,
        profile="original", filename="clip.mp4",
    )
    assert "clip.mp4" in caption
