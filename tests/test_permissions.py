"""Administrator authorization - checked on every sensitive command."""

import pytest

from app.config import Settings
from app.services.permissions import admin_only, is_admin


def make_settings(**kw) -> Settings:
    base = {"bot_token": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "owner_id": 100}
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_owner_is_admin():
    assert is_admin(100, make_settings(admin_ids=""))


def test_configured_admin_is_admin():
    assert is_admin(7, make_settings(admin_ids="7"))


def test_db_admin_is_admin():
    assert is_admin(55, make_settings(admin_ids=""), db_admin_ids={55})


def test_stranger_is_not_admin():
    assert not is_admin(999, make_settings(admin_ids="7"), db_admin_ids={55})


def test_missing_user_is_not_admin():
    assert not is_admin(None, make_settings())


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int):
        self.effective_user = FakeUser(user_id)
        self.effective_message = FakeMessage()


class FakeContext:
    def __init__(self, settings: Settings, db_admins=frozenset()):
        self.bot_data = {"settings": settings, "db_admin_ids": db_admins}


@pytest.mark.asyncio
async def test_admin_only_blocks_stranger():
    calls = []

    @admin_only
    async def secret(update, context):
        calls.append("ran")
        return "ok"

    update = FakeUpdate(999)
    result = await secret(update, FakeContext(make_settings(admin_ids="7")))
    assert result is None
    assert calls == []
    assert update.effective_message.replies
    assert "Administration only" in update.effective_message.replies[0]


@pytest.mark.asyncio
async def test_admin_only_allows_owner():
    calls = []

    @admin_only
    async def secret(update, context):
        calls.append("ran")
        return "ok"

    update = FakeUpdate(100)
    result = await secret(update, FakeContext(make_settings()))
    assert result == "ok"
    assert calls == ["ran"]
