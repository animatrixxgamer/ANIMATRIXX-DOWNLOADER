"""/search /anime /drama /movie /latest - metadata discovery.

Metadata providers never supply video files. When a title is found but no
authorised source or archive entry exists, the message says exactly that.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.database import repositories as repo
from app.database.session import session_scope
from app.keyboards.inline import search_results_keyboard
from app.services.anime_metadata import AniListClient, MediaResult, MetadataError, SearchResult
from app.services.movie_metadata import TMDBClient, TMDBNotConfigured
from app.services.result_store import ResultStore
from app.utils.formatting import box, esc, footer, kv

logger = logging.getLogger(__name__)

MAX_SYNOPSIS = 700


def get_anilist(context: ContextTypes.DEFAULT_TYPE) -> AniListClient:
    return context.bot_data["anilist"]


def get_tmdb(context: ContextTypes.DEFAULT_TYPE) -> TMDBClient:
    return context.bot_data["tmdb"]


def get_store(context: ContextTypes.DEFAULT_TYPE) -> ResultStore:
    return context.bot_data["results"]


async def run_search(
    context: ContextTypes.DEFAULT_TYPE, kind: str, query: str, page: int = 1
) -> SearchResult:
    """Dispatch a query to the right provider for the requested kind."""
    if kind == "anime":
        return await get_anilist(context).search(query, page=page, media_type="anime")
    if kind == "latest":
        return await get_anilist(context).latest(page=page)
    if kind == "drama":
        return await get_tmdb(context).search_tv(query, page=page)
    if kind == "movie":
        return await get_tmdb(context).search_movie(query, page=page)
    raise ValueError(f"Unknown search kind: {kind}")


KIND_LABEL = {
    "anime": "ANIMATRIXX ANIME SEARCH",
    "drama": "ANIMATRIXX K-DRAMA SEARCH",
    "movie": "ANIMATRIXX MOVIE SEARCH",
    "latest": "ANIMATRIXX CURRENTLY AIRING",
}


def render_results(result: SearchResult, kind: str, query: str, token: str) -> tuple[str, object]:
    """Build the result message (text, keyboard)."""
    header = box(KIND_LABEL.get(kind, "ANIMATRIXX SEARCH"))
    if not result.items:
        text = (
            f"{header}\n\n"
            f"  🔎 Query: <b>{esc(query)}</b>\n\n"
            "  No matching titles found.\n"
            "  Check the spelling, or try the original language title.\n"
            f"{footer}"
        )
        return text, None

    lines = [header, "", f"  🔎 Query: <b>{esc(query)}</b>", ""]
    for idx, item in enumerate(result.items):
        marker = item.media_type.upper()
        year = f" ({item.year})" if item.year else ""
        lines.append(f"  <b>{idx + 1}.</b> {esc(item.title)}{year}  [{marker}]")
    lines += [
        "",
        f"  Page {result.page} • {len(result.items)} results",
        "  Choose a title for details:",
        footer,
    ]
    keyboard = search_results_keyboard(token, result.items, result.page, result.has_next)
    return "\n".join(lines), keyboard


def render_detail(item: MediaResult, archived_count: int = 0) -> str:
    header = box("ANIMATRIXX DETAILS")
    parts = [header, "", f"  🎬 <b>{esc(item.title)}</b>"]
    if item.alt_titles:
        parts.append(f"  🔤 Also known as: {esc(', '.join(item.alt_titles[:3]))}")
    parts += [
        "",
        kv("Format", item.media_format or "unknown"),
        kv("Year", item.year or "unknown"),
        kv("Status", item.status or "unknown"),
        kv("Episodes", item.episodes or "according to the metadata provider"),
        kv("Genres", ", ".join(item.genres) if item.genres else "unknown"),
        kv("Rating", f"{item.score}/10" if item.score else "unknown"),
        kv("Provider", item.provider),
        kv("In archive", f"{archived_count} episode(s)" if archived_count else "not archived yet"),
        "",
    ]
    if item.synopsis:
        synopsis = item.synopsis
        if len(synopsis) > MAX_SYNOPSIS:
            synopsis = synopsis[:MAX_SYNOPSIS].rstrip() + "…"
        parts += ["  <b>Synopsis</b>", f"  {esc(synopsis).replace(chr(10), chr(10) + '  ')}", ""]
    parts += [
        "  ⚠️ This is <b>metadata only</b>. The bot does not fetch video from",
        "  streaming sites. To make an episode available, archive a file you",
        "  own or an authorised source with /addseries.",
        footer,
    ]
    return "\n".join(parts)


async def _respond(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, query: str, page: int = 1
) -> None:
    message = update.effective_message
    try:
        result = await run_search(context, kind, query, page)
    except TMDBNotConfigured as exc:
        await message.reply_text(f"⚠️ {exc}", parse_mode=ParseMode.HTML)
        return
    except MetadataError as exc:
        await message.reply_text(f"⚠️ {esc(exc)}", parse_mode=ParseMode.HTML)
        return

    store = get_store(context)
    token = store.put(query, kind, result.provider, result.items, result.page, result.has_next)
    text, keyboard = render_results(result, kind, query, token)
    if not result.items:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    if result.items[0].poster_url:
        caption = text[:1000]
        await message.reply_photo(
            photo=result.items[0].poster_url,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


def _query_from_args(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    query = " ".join(context.args).strip()
    return query or None


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = _query_from_args(context)
    if not query:
        await update.effective_message.reply_text(
            "Usage: <code>/search &lt;title&gt;</code>\n"
            "Searches anime first, then K-drama when TMDB is configured.",
            parse_mode=ParseMode.HTML,
        )
        return
    await _respond(update, context, "anime", query)


async def cmd_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = _query_from_args(context)
    if not query:
        await update.effective_message.reply_text("Usage: <code>/anime &lt;title&gt;</code>", parse_mode=ParseMode.HTML)
        return
    await _respond(update, context, "anime", query)


async def cmd_drama(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = _query_from_args(context)
    if not query:
        await update.effective_message.reply_text("Usage: <code>/drama &lt;title&gt;</code>", parse_mode=ParseMode.HTML)
        return
    await _respond(update, context, "drama", query)


async def cmd_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = _query_from_args(context)
    if not query:
        await update.effective_message.reply_text("Usage: <code>/movie &lt;title&gt;</code>", parse_mode=ParseMode.HTML)
        return
    await _respond(update, context, "movie", query)


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond(update, context, "latest", "airing now")


__all__ = [
    "cmd_search",
    "cmd_anime",
    "cmd_drama",
    "cmd_movie",
    "cmd_latest",
    "render_detail",
    "render_results",
    "run_search",
    "get_store",
    "KIND_LABEL",
    "logger",
]
