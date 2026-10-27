"""Inline keyboards. Every payload is validated again in the callback handler."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.anime_metadata import MediaResult

MAX_LABEL = 34  # leaves room for the emoji prefix

# Telegram buttons cannot carry coloured text (the client owns button styling),
# so colour comes from emoji - one per action, consistently placed first.
ICONS = {
    "result": "🎬",
    "prev": "◀️",
    "next": "▶️",
    "page": "📄",
    "close": "❎",
    "library": "📚",
    "episode": "📺",
    "add": "➕",
    "source": "🌐",
    "confirm": "✅",
    "cancel": "🛑",
    "remove": "🗑️",
    "dismiss": "🧹",
}


def _label(text: str, icon: str = "") -> str:
    text = " ".join((text or "").split())
    if len(text) > MAX_LABEL:
        text = text[: MAX_LABEL - 1] + "…"
    return f"{icon} {text}".strip()


def search_results_keyboard(token: str, items: list[MediaResult], page: int, has_next: bool) -> InlineKeyboardMarkup:
    """One button per result, plus pagination and close."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(_label(item.title, ICONS["result"]), callback_data=f"res:{token}:{idx}")]
        for idx, item in enumerate(items)
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(f"{ICONS['prev']} Prev", callback_data=f"pg:{token}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{ICONS['page']} Page {page}", callback_data="noop:0"))
    if has_next:
        nav.append(InlineKeyboardButton(f"{ICONS['next']} Next", callback_data=f"pg:{token}:{page + 1}"))
    nav.append(InlineKeyboardButton(f"{ICONS['close']} Close", callback_data="close:0"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def detail_keyboard(token: str, index: int, archived: bool = False, can_archive: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(f"{ICONS['library']} Find in Library", callback_data=f"lib:{token}:{index}"),
            InlineKeyboardButton(f"{ICONS['episode']} Select Episode", callback_data=f"ep:{token}:{index}"),
        ]
    ]
    if can_archive:
        rows.append(
            [
                InlineKeyboardButton(f"{ICONS['add']} Add to Series", callback_data=f"add:{token}:{index}"),
                InlineKeyboardButton(f"{ICONS['source']} Provider page", callback_data=f"src:{token}:{index}"),
            ]
        )
    rows.append([
        InlineKeyboardButton(f"{ICONS['prev']} Back", callback_data=f"bk:{token}:0"),
        InlineKeyboardButton(f"{ICONS['close']} Close", callback_data="close:0"),
    ])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(action: str, payload: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{ICONS['confirm']} Confirm", callback_data=f"cf:{action}:{payload}"),
                InlineKeyboardButton(f"{ICONS['cancel']} Cancel", callback_data="close:0"),
            ]
        ]
    )


def series_keyboard(series_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{ICONS['library']} Browse Archive", callback_data=f"libid:{series_id}:0")],
            [InlineKeyboardButton(f"{ICONS['remove']} Remove", callback_data=f"del:{series_id}:0")],
        ]
    )


def noop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{ICONS['dismiss']} Dismiss", callback_data="close:0")]])


__all__ = [
    "search_results_keyboard",
    "detail_keyboard",
    "confirm_keyboard",
    "series_keyboard",
    "noop_keyboard",
    "ICONS",
]
