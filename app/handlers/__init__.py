"""Handler registration - one place that wires every command and callback."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.handlers import admin, callbacks, library, media, search, series, start

logger = logging.getLogger(__name__)

# callback-data prefix -> handler
CALLBACK_ROUTES = {
    "close": callbacks.cb_close,
    "noop": callbacks.cb_noop,
    "res": callbacks.cb_result,
    "bk": callbacks.cb_back,
    "pg": callbacks.cb_page,
    "lib": callbacks.cb_library,
    "ep": callbacks.cb_episode,
    "src": callbacks.cb_source,
    "add": callbacks.cb_add_hint,
    "del": series.cb_series_delete,
    "libid": series.cb_series_library,
    "cf": series.cb_confirm,
}


async def dispatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = (update.callback_query.data or "") if update.callback_query else ""
    prefix = data.split(":", 1)[0]
    handler = CALLBACK_ROUTES.get(prefix, callbacks.cb_unexpected)
    await handler(update, context)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Never leak stack traces or tokens to users; log them instead."""
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. The details are in the log - try again shortly."
            )
        except Exception:  # pragma: no cover
            logger.debug("Could not deliver error message", exc_info=True)


def build_handlers() -> list[BaseHandler]:
    add_series = ConversationHandler(
        entry_points=[CommandHandler("addseries", series.cmd_addseries)],
        states={
            series.AWAIT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, series.on_title),
                CommandHandler("cancel", series.cmd_cancel),
            ],
            series.AWAIT_CHANNEL: [
                MessageHandler(filters.ALL & ~filters.COMMAND, series.on_channel),
                CommandHandler("cancel", series.cmd_cancel),
            ],
        },
        name="addseries",
        persistent=False,
        fallbacks=[CommandHandler("cancel", series.cmd_cancel)],
        allow_reentry=True,
    )

    handlers: list[BaseHandler] = [
        # general
        CommandHandler("start", start.cmd_start),
        CommandHandler("help", start.cmd_help),
        CommandHandler("storage", start.cmd_storage),
        CommandHandler("whoami", admin.cmd_whoami),
        # search
        CommandHandler("search", search.cmd_search),
        CommandHandler("anime", search.cmd_anime),
        CommandHandler("drama", search.cmd_drama),
        CommandHandler("movie", search.cmd_movie),
        CommandHandler("latest", search.cmd_latest),
        # series
        add_series,
        CommandHandler("myseries", series.cmd_myseries),
        CommandHandler("series", series.cmd_series),
        CommandHandler("delseries", series.cmd_delseries),
        CommandHandler("unmapped", series.cmd_unmapped),
        CommandHandler("clearunmapped", series.cmd_clearunmapped),
        # archive
        CommandHandler("library", library.cmd_library),
        CommandHandler("episode", library.cmd_episode),
        CommandHandler("archiveinfo", library.cmd_archiveinfo),
        CommandHandler("verifyarchive", library.cmd_verifyarchive),
        # admin
        CommandHandler("users", admin.cmd_users),
        CommandHandler("broadcast", admin.cmd_broadcast),
        # media pipeline
        CommandHandler("download", media.cmd_download),
        CommandHandler("addlink", media.cmd_addlink),
        CommandHandler("encode", media.cmd_encode),
        CommandHandler("profiles", media.cmd_profiles),
        CommandHandler("queue", media.cmd_queue),
        CommandHandler("progress", media.cmd_progress),
        CommandHandler("cancel", media.cmd_cancel),
        CommandHandler("retry", media.cmd_retry),
        CommandHandler("jobs", media.cmd_jobs),
        CommandHandler("pause", media.cmd_pause),
        CommandHandler("resume", media.cmd_resume),
        # inline buttons (registered last so command handlers win)
        CallbackQueryHandler(dispatch_callback),
    ]
    return handlers


__all__ = ["build_handlers", "dispatch_callback", "on_error", "CALLBACK_ROUTES"]
