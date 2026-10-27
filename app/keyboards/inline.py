"""Inline keyboards. Every payload is validated again in the callback handler."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.anime_metadata import MediaResult

MAX_LABEL = 42


def _label(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


def search_results_keyboard(token: str, items: list[MediaResult], page: int, has_next: bool) -> InlineKeyboardMarkup:
    """One button per result, plus pagination and close."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(_label(item.title), callback_data=f"res:{token}:{idx}")]
        for idx, item in enumerate(items)
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"pg:{token}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"Page {page}", callback_data="noop:0"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"pg:{token}:{page + 1}"))
    nav.append(InlineKeyboardButton("✕ Close", callback_data="close:0"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def detail_keyboard(token: str, index: int, archived: bool = False, can_archive: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🔎 Find in Library", callback_data=f"lib:{token}:{index}"),
            InlineKeyboardButton("📺 Select Episode", callback_data=f"ep:{token}:{index}"),
        ]
    ]
    if can_archive:
        rows.append(
            [
                InlineKeyboardButton("➕ Add to Series", callback_data=f"add:{token}:{index}"),
                InlineKeyboardButton("🌐 Provider page", callback_data=f"src:{token}:{index}"),
            ]
        )
    rows.append([InlineKeyboardButton("◀ Back", callback_data=f"bk:{token}:0"), InlineKeyboardButton("✕ Close", callback_data="close:0")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(action: str, payload: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"cf:{action}:{payload}"),
                InlineKeyboardButton("❌ Cancel", callback_data="close:0"),
            ]
        ]
    )


def series_keyboard(series_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 Browse Archive", callback_data=f"libid:{series_id}:0")],
            [InlineKeyboardButton("🗑 Remove", callback_data=f"del:{series_id}:0")],
        ]
    )


def noop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Dismiss", callback_data="close:0")]])


__all__ = [
    "search_results_keyboard",
    "detail_keyboard",
    "confirm_keyboard",
    "series_keyboard",
    "noop_keyboard",
]
