"""Channel management: menu, general channel, mapping flows, access checks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.error import TelegramError

from app.config import Settings
from app.database import repositories as repo
from app.database.session import build_engine, build_session_factory, init_db, session_scope
from app.handlers import channels
from app.keyboards.inline import ICONS
from app.services.channels import invite_link, resolve_channel, verify_channel

OWNER = 100


# ------------------------------------------------------------------- fakes
class FakeChat:
    def __init__(self, chat_id=-100555, kind="channel", title="General", username=None):
        self.id = chat_id
        self.type = kind
        self.title = title
        self.username = username


class FakeBot:
    id = 424242

    def __init__(self, chat=None, status="administrator", error=None):
        self.chat = chat or FakeChat()
        self.status = status
        self.error = error
        self.sent: list[tuple[int, str]] = []
        self.invites = 0

    async def get_chat(self, ref):
        if self.error:
            raise self.error
        if isinstance(ref, str) and ref.startswith("@missing"):
            raise TelegramError("chat not found")
        return self.chat

    async def get_chat_member(self, chat_id, user_id):
        if self.error:
            raise self.error
        return SimpleNamespace(status=self.status)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return SimpleNamespace(id=7)

    async def create_chat_invite_link(self, chat_id, name=None):
        self.invites += 1
        return SimpleNamespace(invite_link="https://t.me/+INVITE")

    async def export_chat_invite_link(self, chat_id):
        return "https://t.me/+EXPORTED"


class FakeMessage:
    def __init__(self, text=None, chat=None, forwarded=False, caption=None):
        self.text = text
        self.caption = caption
        self.replies: list[str] = []
        self.forward_origin = (
            SimpleNamespace(chat=chat) if forwarded and chat is not None else None
        )

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self


class FakeUpdate:
    def __init__(self, message: FakeMessage):
        self.effective_message = message
        self.effective_user = SimpleNamespace(id=OWNER, username="boss", full_name="Boss")
        self.callback_query = None


class FakeContext:
    def __init__(self, settings, factory, bot, user_data=None):
        self.bot = bot
        self.bot_data = {
            "settings": settings,
            "session_factory": factory,
            "db_admin_ids": frozenset(),
        }
        self.user_data = user_data if user_data is not None else {}


@pytest.fixture
async def env(tmp_path):
    settings = Settings(
        _env_file=None,
        bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg",
        owner_id=OWNER,
    )
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = build_session_factory(engine)
    yield settings, factory
    await engine.dispose()


def make_context(settings, factory, bot, user_data=None) -> FakeContext:
    return FakeContext(settings, factory, bot, user_data=user_data)


# -------------------------------------------------------------------- menu
def test_menu_keyboard_offers_every_action():
    markup = channels.menu_keyboard()
    labels = [b.text for row in markup.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert len(labels) == 6
    assert all(data.startswith("ch:") for data in callbacks)
    assert any("General channel" in label for label in labels)
    assert any("Add archive channel" in label for label in labels)
    assert all(any(ord(ch) > 0x2600 for ch in label) for label in labels)


def test_render_menu_reports_general_channel_and_count():
    text = channels.render_menu("My General", 3)
    assert "My General" in text
    assert "3" in text
    plain = channels.render_menu(None, 0)
    assert "not set" in plain


# ------------------------------------------------------------ channel checks
@pytest.mark.asyncio
async def test_verify_channel_ok_denied_and_missing():
    ok = await verify_channel(FakeBot(status="administrator"), -1001)
    assert (ok.ok, ok.status) == (True, "active")

    denied = await verify_channel(FakeBot(status="member"), -1001)
    assert denied.ok is False and denied.status == "denied"
    assert "administrator" in denied.reason

    missing = await verify_channel(FakeBot(error=TelegramError("gone")), -1001)
    assert missing.ok is False and missing.status == "missing"


@pytest.mark.asyncio
async def test_resolve_channel_rejects_non_channels():
    with pytest.raises(ValueError) as exc:
        await resolve_channel(FakeBot(chat=FakeChat(kind="private")), -1)
    assert "not a channel" in str(exc.value)

    with pytest.raises(ValueError):
        await resolve_channel(FakeBot(), "@missing")


@pytest.mark.asyncio
async def test_invite_link_prefers_public_username_then_minted_link():
    public = FakeBot(chat=FakeChat(username="my_channel"))
    assert await invite_link(public, -1001) == "https://t.me/my_channel"

    private = FakeBot(chat=FakeChat())
    assert await invite_link(private, -1001) == "https://t.me/+INVITE"

    fallback = FakeBot(chat=FakeChat())
    fallback.create_chat_invite_link = _raise
    assert await invite_link(fallback, -1001) == "https://t.me/+EXPORTED"


async def _raise(*args, **kwargs):
    raise TelegramError("no permission")


# ---------------------------------------------------------- general channel
@pytest.mark.asyncio
async def test_set_main_from_forwarded_message(env):
    settings, factory = env
    bot = FakeBot(chat=FakeChat(chat_id=-100777, title="Animatrixx General"))
    message = FakeMessage(chat=FakeChat(chat_id=-100777), forwarded=True)
    context = make_context(settings, factory, bot, user_data={channels.PENDING_KEY: "main"})

    await channels.on_channel_message(FakeUpdate(message), context)

    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, channels.MAIN_ROLE)
    assert main is not None
    assert main.telegram_chat_id == -100777
    assert main.status == "active"
    assert channels.PENDING_KEY not in context.user_data
    assert any("General channel set" in reply for reply in message.replies)


@pytest.mark.asyncio
async def test_set_main_keeps_only_one_row(env):
    settings, factory = env
    bot = FakeBot(chat=FakeChat(chat_id=-1, title="Old"))
    async with session_scope(factory) as session:
        await repo.set_channel_config(session, channels.MAIN_ROLE, -1, title="Old", single=True)
    async with session_scope(factory) as session:
        await repo.set_channel_config(session, channels.MAIN_ROLE, -2, title="New", single=True)
        rows = await repo.list_channel_configs(session, channels.MAIN_ROLE)
        assert len(rows) == 1
        assert rows[0].telegram_chat_id == -2


@pytest.mark.asyncio
async def test_set_main_rejects_non_admin_and_keeps_pending(env):
    settings, factory = env
    bot = FakeBot(status="member")
    message = FakeMessage(text="@somechannel")
    context = make_context(settings, factory, bot, user_data={channels.PENDING_KEY: "main"})

    await channels.on_channel_message(FakeUpdate(message), context)

    assert channels.PENDING_KEY in context.user_data  # flow stays open for a retry
    assert any("administrator" in reply for reply in message.replies)


@pytest.mark.asyncio
async def test_channel_message_needs_an_actual_channel(env):
    settings, factory = env
    bot = FakeBot()
    message = FakeMessage(text="just some words")
    context = make_context(settings, factory, bot, user_data={channels.PENDING_KEY: "main"})
    await channels.on_channel_message(FakeUpdate(message), context)
    assert any("forward a message" in reply.lower() for reply in message.replies)
    assert channels.PENDING_KEY in context.user_data


@pytest.mark.asyncio
async def test_no_pending_state_means_no_reply(env):
    settings, factory = env
    bot = FakeBot()
    message = FakeMessage(text="hello")
    context = make_context(settings, factory, bot)
    await channels.on_channel_message(FakeUpdate(message), context)
    assert message.replies == []


@pytest.mark.asyncio
async def test_post_sends_to_the_general_channel(env):
    settings, factory = env
    bot = FakeBot(chat=FakeChat(chat_id=-100999))
    async with session_scope(factory) as session:
        await repo.set_channel_config(
            session, channels.MAIN_ROLE, -100999, title="General", single=True
        )
    message = FakeMessage(text="New episode dropped")
    context = make_context(settings, factory, bot, user_data={channels.PENDING_KEY: "post"})

    await channels.on_channel_message(FakeUpdate(message), context)

    assert bot.sent and bot.sent[0][0] == -100999
    assert "New episode dropped" in bot.sent[0][1]
    assert channels.PENDING_KEY not in context.user_data


@pytest.mark.asyncio
async def test_post_without_general_channel_explains(env):
    settings, factory = env
    bot = FakeBot()
    update = FakeUpdate(FakeMessage(text="hello"))
    update.effective_message.text = "/post hello"
    context = make_context(settings, factory, bot)
    # /post with text goes through cmd_post; no general channel configured
    await channels._send_to_general(update, context, "hello")
    assert any("/set_main" in reply for reply in update.effective_message.replies)


@pytest.mark.asyncio
async def test_cancel_clears_pending_then_falls_back(env):
    settings, factory = env
    bot = FakeBot()
    context = make_context(settings, factory, bot, user_data={channels.PENDING_KEY: "main"})
    update = FakeUpdate(FakeMessage(text="/cancel"))
    await channels.cmd_cancel(update, context)
    assert channels.PENDING_KEY not in context.user_data
    assert any("Cancelled" in reply for reply in update.effective_message.replies)


# ---------------------------------------------------------- attach to series
@pytest.mark.asyncio
async def test_attach_channel_to_series(env):
    settings, factory = env
    bot = FakeBot(chat=FakeChat(chat_id=-100321, title="Solo Archive"))
    async with session_scope(factory) as session:
        series = await repo.create_series(session, "Solo Leveling")
        series_id = series.id
    message = FakeMessage(chat=FakeChat(chat_id=-100321), forwarded=True)
    context = make_context(
        settings, factory, bot, user_data={channels.PENDING_KEY: f"series:{series_id}"}
    )

    await channels.on_channel_message(FakeUpdate(message), context)

    async with session_scope(factory) as session:
        stored = await repo.get_series_by_title(session, "Solo Leveling")
        channel = await repo.get_series_channel(session, stored)
    assert channel is not None
    assert channel.telegram_chat_id == -100321
    assert channels.PENDING_KEY not in context.user_data
    assert any("Solo Archive" in reply and "verified" in reply for reply in message.replies)


# ------------------------------------------------------------------ listing
@pytest.mark.asyncio
async def test_list_channels_shows_general_and_series(env):
    settings, factory = env
    bot = FakeBot()
    async with session_scope(factory) as session:
        await repo.set_channel_config(session, channels.MAIN_ROLE, -1, title="General", single=True)
        channel = await repo.get_or_create_channel(session, -2, title="Archive A")
        await repo.create_series(session, "Alpha", archive_channel_id=channel.id)

    update = FakeUpdate(FakeMessage(text="/listchannels"))
    await channels.cmd_listchannels(update, make_context(settings, factory, bot))

    reply = update.effective_message.replies[0]
    assert "General" in reply and "Archive A" in reply and "Alpha" in reply
    # the list also carries a remove button per mapped channel
    assert "✅" in reply