"""Inline-button callbacks. Every payload is re-validated before it is used."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.database import repositories as repo
from app.database.session import session_scope
from app.keyboards.inline import detail_keyboard, noop_keyboard, search_results_keyboard
from app.handlers.search import (
    KIND_LABEL,
    get_store,
    render_detail,
    render_results,
    run_search,
)
from app.utils.formatting import box, esc, footer, kv

logger = logging.getLogger(__name__)


async def _safe_delete(message) -> None:
    try:
        await message.delete()
    except Exception:  # pragma: no cover - best effort
        logger.debug("Could not delete message", exc_info=True)


async def _archive_count(context: ContextTypes.DEFAULT_TYPE, title: str) -> int:
    factory = context.bot_data["session_factory"]
    try:
        async with session_scope(factory) as session:
            series = await repo.get_series_by_title(session, title)
            if series is None:
                return 0
            return len(await repo.list_archive_for_series(session, series.id))
    except Exception:  # pragma: no cover
        logger.exception("Archive lookup failed for %r", title)
        return 0


async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _safe_delete(update.callback_query.message)


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def cb_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show full metadata for one search result."""
    query = update.callback_query
    await query.answer()
    try:
        _, token, raw_idx = query.data.split(":", 2)
        index = int(raw_idx)
    except (ValueError, AttributeError):
        await query.answer("Invalid button payload.", show_alert=True)
        return

    store = get_store(context)
    stored = store.get(token)
    if stored is None or not (0 <= index < len(stored.items)):
        await query.answer("These results expired - run the search again.", show_alert=True)
        return

    item = stored.items[index]
    archived = await _archive_count(context, item.title)
    text = render_detail(item, archived)
    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=detail_keyboard(token, index, archived=archived),
    )
    await query.answer()


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dismiss the detail message and return to the results list."""
    query = update.callback_query
    await query.answer()
    await _safe_delete(query.message)


async def cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch the next/previous page of a stored query."""
    query = update.callback_query
    try:
        _, token, raw_page = query.data.split(":", 2)
        page = max(1, int(raw_page))
    except (ValueError, AttributeError):
        await query.answer("Invalid page.", show_alert=True)
        return

    store = get_store(context)
    stored = store.get(token)
    if stored is None:
        await query.answer("These results expired - run the search again.", show_alert=True)
        return

    try:
        result = await run_search(context, stored.kind, stored.query, page)
    except Exception as exc:  # MetadataError hierarchy
        await query.answer(str(exc)[:180], show_alert=True)
        return

    new_token = store.put(
        stored.query, stored.kind, result.provider, result.items, result.page, result.has_next
    )
    text, keyboard = render_results(result, stored.kind, stored.query, new_token)
    try:
        if result.items and result.items[0].poster_url and query.message.photo:
            await query.message.edit_caption(
                caption=text[:1000], parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        else:
            await query.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
    except Exception:  # unchanged content or media mismatch
        logger.debug("Could not edit results message", exc_info=True)
    await query.answer()


async def cb_library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search the local archive for the selected title."""
    query = update.callback_query
    await query.answer()
    try:
        _, token, raw_idx = query.data.split(":", 2)
        index = int(raw_idx)
    except (ValueError, AttributeError):
        return
    stored = get_store(context).get(token)
    if stored is None or not (0 <= index < len(stored.items)):
        await query.answer("These results expired.", show_alert=True)
        return
    title = stored.items[index].title

    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        rows = await repo.search_archive(session, title)

    header = box("ANIMATRIXX LIBRARY")
    if not rows:
        text = (
            f"{header}\n\n"
            f"  📚 <b>{esc(title)}</b>\n\n"
            "  Nothing archived for this title yet.\n"
            "  Ask the administrator to add it with /addseries,\n"
            "  then archive an authorised file.\n"
            f"{footer}"
        )
    else:
        lines = [header, "", f"  📚 <b>{esc(title)}</b>"]
        for row in rows[:25]:
            lines.append(
                f"  • S{row.season_number:02d}E{row.episode_number:02d}"
                f"  {esc(row.episode_title or row.archived_filename or '')}"
                f"  [{esc(row.resolution)}]"
            )
        lines += ["", f"  {len(rows)} archived item(s).", footer]
        text = "\n".join(lines)
    await query.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=noop_keyboard()
    )


async def cb_episode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Episode picker - only offered when archived episodes really exist."""
    query = update.callback_query
    try:
        _, token, raw_idx = query.data.split(":", 2)
        index = int(raw_idx)
    except (ValueError, AttributeError):
        await query.answer("Invalid button payload.", show_alert=True)
        return
    stored = get_store(context).get(token)
    if stored is None or not (0 <= index < len(stored.items)):
        await query.answer("These results expired - run the search again.", show_alert=True)
        return
    title = stored.items[index].title
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        series = await repo.get_series_by_title(session, title)
        rows = await repo.list_archive_for_series(session, series.id) if series else []

    if not rows:
        await query.answer(
            "No archived episodes exist for this title yet - nothing to select.",
            show_alert=True,
        )
        return

    buttons = []
    for row in rows[:40]:
        label = f"S{row.season_number:02d}E{row.episode_number:02d}"
        buttons.append([InlineKeyboardButton(label, callback_data="noop:0")])
    buttons.append([InlineKeyboardButton("✕ Close", callback_data="close:0")])

    await query.message.reply_text(
        f"{box('ANIMATRIXX EPISODES')}\n\n  🎬 <b>{esc(title)}</b>\n\n"
        "  Archived episodes (download delivery arrives in a later phase):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


async def cb_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, token, raw_idx = query.data.split(":", 2)
        index = int(raw_idx)
    except (ValueError, AttributeError):
        await query.answer("Invalid button payload.", show_alert=True)
        return
    stored = get_store(context).get(token)
    if stored is None or not (0 <= index < len(stored.items)):
        await query.answer("These results expired.", show_alert=True)
        return
    item = stored.items[index]
    if item.external_url:
        await query.answer()
        await query.message.reply_text(
            f"  🌐 Provider page:\n{esc(item.external_url)}", parse_mode=ParseMode.HTML
        )
    else:
        await query.answer("No provider page available.", show_alert=True)


async def cb_add_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, token, raw_idx = query.data.split(":", 2)
        index = int(raw_idx)
    except (ValueError, AttributeError):
        await query.answer("Invalid button payload.", show_alert=True)
        return
    stored = get_store(context).get(token)
    if stored is None or not (0 <= index < len(stored.items)):
        await query.answer("These results expired.", show_alert=True)
        return
    title = stored.items[index].title
    await query.answer()
    await query.message.reply_text(
        f"{box('ADD SERIES')}\n\n"
        f"  Run <code>/addseries</code> and send this title:\n"
        f"  <b>{esc(title)}</b>\n\n"
        "  You will then be asked for the private archive channel.",
        parse_mode=ParseMode.HTML,
    )


async def cb_unexpected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback for callback data we do not recognise."""
    if update.callback_query:
        await update.callback_query.answer("This button has expired.", show_alert=True)


__all__ = [
    "cb_close",
    "cb_noop",
    "cb_result",
    "cb_back",
    "cb_page",
    "cb_library",
    "cb_episode",
    "cb_source",
    "cb_add_hint",
    "cb_unexpected",
]
