"""/users and /broadcast - administrator only, re-checked on every call."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from app.database import repositories as repo
from app.database.session import session_scope
from app.services.permissions import admin_only
from app.utils.formatting import box, esc, footer

logger = logging.getLogger(__name__)

BROADCAST_DELAY = 0.05  # be gentle with the Bot API flood limits


@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        total = await repo.count_users(session)
        admins = await repo.get_admin_ids(session)
    configured = context.bot_data["settings"].administrators
    text = (
        f"{box('ANIMATRIXX USERS')}\n\n"
        f"  👥 Started the bot: {total}\n"
        f"  🛡 Administrators: {len(set(admins) | set(configured))}\n"
        f"{footer}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <message> - sends to every user who started the bot."""
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Usage: <code>/broadcast Your announcement</code>", parse_mode=ParseMode.HTML
        )
        return

    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        targets = await repo.list_user_ids(session)

    status = await update.effective_message.reply_text(
        f"📡 Broadcasting to {len(targets)} user(s)…"
    )
    ok = failed = blocked = 0
    for user_id in targets:
        try:
            await context.bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Forbidden:
            blocked += 1
            failed += 1
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await context.bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
                ok += 1
                failed -= 1
            except TelegramError:
                failed += 1
        except TelegramError:
            logger.debug("Broadcast to %s failed", user_id, exc_info=True)
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    summary = (
        f"{box('BROADCAST COMPLETE')}\n\n"
        f"  ✅ Delivered: {ok}\n"
        f"  ❌ Failed: {failed}\n"
        f"  🚫 Blocked the bot: {blocked}\n"
        f"{footer}"
    )
    try:
        await status.edit_text(summary, parse_mode=ParseMode.HTML)
    except TelegramError:
        await update.effective_message.reply_text(summary, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    settings = context.bot_data["settings"]
    is_owner = user.id == settings.owner_id
    is_configured = user.id in settings.administrators
    text = (
        f"{box('IDENTITY')}\n\n"
        f"  👤 Name: {esc(user.full_name)}\n"
        f"  🔢 User id: <code>{user.id}</code>\n"
        f"  👑 Owner: {'yes' if is_owner else 'no'}\n"
        f"  🛡 In ADMIN_IDS: {'yes' if is_configured else 'no'}\n"
        f"{footer}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


__all__ = ["cmd_users", "cmd_broadcast", "cmd_whoami"]
