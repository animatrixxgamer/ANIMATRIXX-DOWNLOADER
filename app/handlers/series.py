"""/addseries conversation plus /myseries /series /delseries /unmapped /clearunmapped."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.database import repositories as repo
from app.database.models import ArchiveChannel
from app.database.session import session_scope
from app.keyboards.inline import confirm_keyboard, series_keyboard
from app.services.permissions import admin_only
from app.utils.formatting import box, esc, footer, kv
from app.utils.validation import parse_channel_ref

logger = logging.getLogger(__name__)

AWAIT_TITLE, AWAIT_CHANNEL = range(2)
VALID_TYPES = {"anime", "drama", "movie", "series", "other"}


def _channel_from_message(message) -> object | None:
    """Return the chat a forwarded/channel message belongs to, if any."""
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin is not None else None
    if chat is None:
        chat = getattr(message, "forward_from_chat", None)
    if chat is not None and getattr(chat, "type", None) in {"channel", "group", "supergroup"}:
        return chat
    return None


async def _verify_access(bot, ref: int | str) -> tuple[int, str | None, str | None]:
    """Resolve and validate the channel. Raises ValueError with a user message."""
    chat = await bot.get_chat(ref)
    if chat.type not in {"channel", "group", "supergroup"}:
        raise ValueError("That chat is not a channel or group. Use a private channel.")
    try:
        member = await bot.get_chat_member(chat.id, bot.id)
    except Exception as exc:  # TelegramError
        raise ValueError(
            "I could not check my access to that chat. Make sure I am added as an administrator."
        ) from exc
    if member.status not in {"administrator", "creator"}:
        raise ValueError(
            "I am not an administrator there. Add me to the private channel as an admin "
            "with the permission to post messages, then try again."
        )
    return chat.id, getattr(chat, "title", None), getattr(chat, "username", None)


async def _ask(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_addseries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/addseries [anime|drama|movie] <title> - starts the guided flow."""
    args = list(context.args)
    series_type = "anime"
    if args and args[0].lower() in VALID_TYPES:
        series_type = args.pop(0).lower()
    title = " ".join(args).strip()

    header = box("ADD SERIES")
    if title:
        context.user_data["pending_title"] = title
        context.user_data["pending_type"] = series_type
        await _ask(
            update,
            f"{header}\n\n"
            f"  🎬 <b>{esc(title)}</b>\n\n"
            "  Now forward a message from the private archive channel\n"
            "  (or send <code>@channelname</code> / the numeric chat id).\n\n"
            "  Setup: create a private channel → add me as administrator\n"
            "  → send me a message forwarded from it.\n"
            f"{footer}",
        )
        return AWAIT_CHANNEL

    context.user_data["pending_type"] = series_type
    await _ask(
        update,
        f"{header}\n\n"
        "  Send the series title (e.g. <b>Solo Leveling</b>).\n"
        "  Reply with <code>/cancel</code> to abort.\n"
        f"{footer}",
    )
    return AWAIT_TITLE


async def on_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = (update.effective_message.text or "").strip()
    if not title or title.startswith("/"):
        await _ask(update, "Please send a plain series title.")
        return AWAIT_TITLE
    context.user_data["pending_title"] = title
    header = box("ADD SERIES")
    await _ask(
        update,
        f"{header}\n\n"
        f"  🎬 <b>{esc(title)}</b>\n\n"
        "  Now forward a message from the private archive channel\n"
        "  (or send <code>@channelname</code> / numeric chat id).\n"
        f"{footer}",
    )
    return AWAIT_CHANNEL


@admin_only
async def on_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    title = context.user_data.get("pending_title")
    if not title:
        await _ask(update, "Session expired. Start again with /addseries.")
        return ConversationHandler.END

    chat = _channel_from_message(message)
    if chat is not None:
        ref: int | str = chat.id
    else:
        ref = parse_channel_ref(message.text or "")
        if ref is None:
            await _ask(
                update,
                "I could not read a channel from that message.\n"
                "Forward a message from the archive channel, or send "
                "<code>@channelname</code> or the numeric id.",
            )
            return AWAIT_CHANNEL

    try:
        chat_id, chat_title, username = await _verify_access(context.bot, ref)
    except ValueError as exc:
        await _ask(update, f"⚠️ {esc(exc)}")
        return AWAIT_CHANNEL
    except Exception:
        logger.exception("Channel verification failed for %r", ref)
        await _ask(update, "⚠️ Telegram rejected that channel reference. Try another one.")
        return AWAIT_CHANNEL

    factory = context.bot_data["session_factory"]
    series_type = context.user_data.get("pending_type", "anime")
    try:
        async with session_scope(factory) as session:
            channel = await repo.get_or_create_channel(
                session, chat_id, title=chat_title, username=username
            )
            series = await repo.get_series_by_title(session, title)
            if series is None:
                series = await repo.create_series(
                    session, title, series_type=series_type, archive_channel_id=channel.id
                )
            else:
                series = await repo.attach_archive_channel(session, title, channel)
            channel.verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    except Exception:
        logger.exception("Could not save series %r", title)
        await _ask(update, "⚠️ I could not save that mapping. Details are in the log.")
        return ConversationHandler.END

    context.user_data.pop("pending_title", None)
    context.user_data.pop("pending_type", None)

    header = box("SERIES REGISTERED")
    await _ask(
        update,
        f"{header}\n\n"
        f"  🎬 <b>{esc(series.title)}</b>\n"
        f"  {kv('Type', series.series_type)}\n"
        f"  {kv('Archive channel', chat_title or chat_id)}\n"
        f"  {kv('Channel id', chat_id)}\n\n"
        "  ✅ Verified: I can post there.\n"
        f"{footer}",
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending_title", None)
    context.user_data.pop("pending_type", None)
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END


@admin_only
async def cmd_myseries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        rows = await repo.list_series(session)
    header = box("ANIMATRIXX MY SERIES")
    if not rows:
        await _ask(
            update,
            f"{header}\n\n  No series registered yet.\n  Use /addseries to add your first one.\n{footer}",
        )
        return
    lines = [header, ""]
    for row in rows:
        icon = "📦" if row.archive_channel_id else "⚠️"
        lines.append(f"  {icon} <b>{esc(row.title)}</b>  ({esc(row.series_type)})")
    lines += ["", f"  {len(rows)} series • ⚠️ = no archive channel", footer]
    await _ask(update, "\n".join(lines))


@admin_only
async def cmd_series(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = " ".join(context.args).strip()
    if not title:
        await _ask(update, "Usage: <code>/series &lt;title&gt;</code>", )
        return
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        series = await repo.get_series_by_title(session, title)
        if series is None:
            found = await repo.find_series(session, title, limit=1)
            series = found[0] if found else None
        episodes = await repo.list_archive_for_series(session, series.id) if series else []
        channel = await repo.get_series_channel(session, series)

    header = box("ANIMATRIXX SERIES")
    if series is None:
        await _ask(update, f"{header}\n\n  No series matches <b>{esc(title)}</b>.\n{footer}")
        return

    text = (
        f"{header}\n\n"
        f"  🎬 <b>{esc(series.title)}</b>\n"
        f"  {kv('Type', series.series_type)}\n"
        f"  {kv('Provider', series.provider)}\n"
        f"  {kv('Year', series.release_year or 'unknown')}\n"
        f"  {kv('Archived episodes', len(episodes))}\n"
        f"  {kv('Archive channel', channel.title if channel else 'not configured')}\n"
        f"{footer}"
    )
    await _ask(update, text)
    await update.effective_message.reply_text(
        "Choose an action:", reply_markup=series_keyboard(series.id)
    )


@admin_only
async def cmd_delseries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = " ".join(context.args).strip()
    if not title:
        await _ask(update, "Usage: <code>/delseries &lt;title&gt;</code>")
        return
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        series = await repo.get_series_by_title(session, title)
        if series is None:
            found = await repo.find_series(session, title, limit=1)
            series = found[0] if found else None
        if series is None:
            await _ask(update, f"  No series matches <b>{esc(title)}</b>.")
            return
        series_id, series_title = series.id, series.title
        archived = len(await repo.list_archive_for_series(session, series_id))

    await _ask(
        update,
        f"{box('CONFIRM DELETE')}\n\n"
        f"  🎬 <b>{esc(series_title)}</b>\n"
        f"  {kv('Archived rows', archived)}\n\n"
        "  Only the database mapping is removed. The Telegram channel\n"
        "  and every file inside it are left untouched.\n"
        f"{footer}",
    )
    await update.effective_message.reply_text(
        "Proceed?", reply_markup=confirm_keyboard("del", str(series_id))
    )


@admin_only
async def cmd_unmapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        rows = await repo.series_without_channel(session)
    header = box("UNMAPPED SERIES")
    if not rows:
        await _ask(update, f"{header}\n\n  ✅ Every series has an archive channel.\n{footer}")
        return
    lines = [header, ""]
    for row in rows:
        lines.append(f"  ⚠️ {esc(row.title)}")
    lines += ["", "  Use /addseries to attach a channel to these.", footer]
    await _ask(update, "\n".join(lines))


@admin_only
async def cmd_clearunmapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ask(
        update,
        f"{box('CONFIRM CLEAR')}\n\n"
        "  This removes <b>manually created</b> series that have no archive\n"
        "  channel. Channels and messages are never touched.\n"
        f"{footer}",
    )
    await update.effective_message.reply_text(
        "Proceed?", reply_markup=confirm_keyboard("clearunmapped", "0")
    )


# ------------------------------------------------------------- callbacks
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    from app.services.permissions import is_admin

    settings = context.bot_data["settings"]
    if not is_admin(query.from_user.id, settings, context.bot_data.get("db_admin_ids", ())):
        await query.answer("Administration only.", show_alert=True)
        return
    try:
        _, action, payload = query.data.split(":", 2)
    except (ValueError, AttributeError):
        await query.answer("Invalid payload.", show_alert=True)
        return

    factory = context.bot_data["session_factory"]
    if action == "del":
        async with session_scope(factory) as session:
            series = await repo.get_series_by_id(session, int(payload))
            title = series.title if series else "?"
            if series is not None:
                await session.delete(series)
                await session.flush()
        await query.answer("Series removed.")
        await query.message.reply_text(f"  🗑 Removed <b>{esc(title)}</b>.\n  Channel untouched.")
    elif action == "clearunmapped":
        async with session_scope(factory) as session:
            removed = await repo.clear_unmapped(session)
        await query.answer(f"Removed {removed} rows.")
        await query.message.reply_text(f"  🗑 Removed {removed} unmapped series row(s).")
    else:
        await query.answer("Unknown action.", show_alert=True)


async def cb_series_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Confirm removal of this series from the database?",
        reply_markup=confirm_keyboard("del", query.data.split(":")[1]),
    )


async def cb_series_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        series_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        return
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        series = await repo.get_series_by_id(session, series_id)
        rows = await repo.list_archive_for_series(session, series_id) if series else []

    header = box("ANIMATRIXX ARCHIVE")
    if series is None:
        await query.message.reply_text(f"{header}\n\n  That series no longer exists.\n{footer}")
        return
    lines = [header, "", f"  🎬 <b>{esc(series.title)}</b>"]
    if not rows:
        lines += ["", "  No archived episodes yet."]
    for row in rows[:40]:
        lines.append(
            f"  • S{row.season_number:02d}E{row.episode_number:02d} "
            f"{esc(row.episode_title or row.archived_filename or '')} [{esc(row.resolution)}]"
        )
    lines += ["", footer]
    await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


__all__ = [
    "AWAIT_TITLE",
    "AWAIT_CHANNEL",
    "cmd_addseries",
    "on_title",
    "on_channel",
    "cmd_cancel",
    "cmd_myseries",
    "cmd_series",
    "cmd_delseries",
    "cmd_unmapped",
    "cmd_clearunmapped",
    "cb_confirm",
    "cb_series_delete",
    "cb_series_library",
]
