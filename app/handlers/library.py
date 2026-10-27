"""/library /episode /archiveinfo /verifyarchive - the Telegram archive view."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.database import repositories as repo
from app.database.session import session_scope
from app.services.permissions import admin_only
from app.utils.formatting import box, esc, footer, kv
from app.utils.validation import parse_episode_ref

logger = logging.getLogger(__name__)

MAX_ROWS = 40


def _row_label(row) -> str:
    size = f" • {row.file_size // 2**20} MiB" if row.file_size else ""
    return (
        f"  • S{row.season_number:02d}E{row.episode_number:02d} "
        f"{esc(row.episode_title or row.archived_filename or '')} "
        f"[{esc(row.resolution)}]{esc(size)}"
    )


async def cmd_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    factory = context.bot_data["session_factory"]
    header = box("ANIMATRIXX LIBRARY")

    async with session_scope(factory) as session:
        if query:
            rows = await repo.search_archive(session, query)
        else:
            series_rows = await repo.list_series(session)
            rows = []
            for series in series_rows:
                rows.extend(await repo.list_archive_for_series(session, series.id))

    if not rows:
        text = (
            f"{header}\n\n"
            + (f"  🔎 Query: <b>{esc(query)}</b>\n\n" if query else "")
            + "  The archive is empty.\n"
            "  Register a series with /addseries, then archive an authorised file.\n"
            f"{footer}"
        )
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    lines = [header, ""]
    if query:
        lines.append(f"  🔎 Query: <b>{esc(query)}</b>")
        lines.append("")
    for row in rows[:MAX_ROWS]:
        lines.append(_row_label(row))
    if len(rows) > MAX_ROWS:
        lines.append(f"  … and {len(rows) - MAX_ROWS} more (narrow the query)")
    lines += ["", f"  {len(rows)} archived item(s).", footer]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_episode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /episode <title> S01E04"""
    args = list(context.args)
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: <code>/episode &lt;title&gt; S01E04</code>", parse_mode=ParseMode.HTML
        )
        return
    ref = parse_episode_ref(args[-1])
    if ref is None:
        await update.effective_message.reply_text(
            "I could not read that episode reference. Use the form <code>S01E04</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    season, episode = ref
    title = " ".join(args[:-1])

    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        rows = await repo.search_archive(session, title, season=season, episode=episode)

    header = box("ANIMATRIXX EPISODE")
    if not rows:
        await update.effective_message.reply_text(
            f"{header}\n\n"
            f"  🎬 <b>{esc(title)}</b> • S{season:02d}E{episode:02d}\n\n"
            "  Not archived yet. No video source is configured for this episode.\n"
            "  The administrator can add one with /addseries + an authorised file.\n"
            f"{footer}",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [header, "", f"  🎬 <b>{esc(title)}</b> • S{season:02d}E{episode:02d}", ""]
    for row in rows:
        lines.append(_row_label(row))
    lines += ["", "  File delivery from the archive arrives in a later phase.", footer]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_archiveinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        channels = await repo.list_channels(session)
        total = await repo.count_archived(session)
    header = box("ANIMATRIXX ARCHIVE INFO")
    if not channels:
        await update.effective_message.reply_text(
            f"{header}\n\n  No archive channel configured yet.\n  Use /addseries.\n{footer}",
            parse_mode=ParseMode.HTML,
        )
        return
    lines = [header, ""]
    for channel in channels:
        lines.append(
            f"  {'✅' if channel.status == 'active' else '⚠️'} <b>{esc(channel.title or channel.telegram_chat_id)}</b>\n"
            f"     id: <code>{channel.telegram_chat_id}</code> • status: {esc(channel.status)}"
        )
    lines += ["", f"  Archived items: {total}", footer]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_verifyarchive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-check bot access to every configured archive channel."""
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        channels = await repo.list_channels(session)

    header = box("ARCHIVE VERIFICATION")
    if not channels:
        await update.effective_message.reply_text(
            f"{header}\n\n  No channels to verify.\n{footer}", parse_mode=ParseMode.HTML
        )
        return

    lines = [header, ""]
    for channel in channels:
        status = "missing"
        try:
            chat = await context.bot.get_chat(channel.telegram_chat_id)
            member = await context.bot.get_chat_member(chat.id, context.bot.id)
            status = "active" if member.status in {"administrator", "creator"} else "denied"
            channel_title = getattr(chat, "title", None) or channel.title
        except Exception as exc:
            logger.warning("Channel %s unreachable: %s", channel.telegram_chat_id, exc)
            channel_title = channel.title
            status = "missing"

        async with session_scope(factory) as session:
            stored = await repo.get_channel_by_id(session, channel.id)
            if stored is not None:
                stored.status = status

        icon = {"active": "✅", "denied": "🚫", "missing": "❌"}.get(status, "⚠️")
        lines.append(f"  {icon} <b>{esc(channel_title)}</b> • {status}")

    lines += ["", "  repair: re-add me as channel admin, then rerun /verifyarchive", footer]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


__all__ = ["cmd_library", "cmd_episode", "cmd_archiveinfo", "cmd_verifyarchive"]
