"""Permission helpers. Re-checked inside every sensitive handler."""

from __future__ import annotations

import functools
from typing import Awaitable, Callable, Iterable

from app.config import Settings


def is_admin(
    user_id: int | None,
    settings: Settings,
    db_admin_ids: Iterable[int] = (),
) -> bool:
    """Owner, configured ADMIN_IDS, and persisted administrators are allowed."""
    if user_id is None:
        return False
    if user_id in settings.administrators:
        return True
    return user_id in set(db_admin_ids)


def admin_only(
    handler: Callable[..., Awaitable[object]],
) -> Callable[..., Awaitable[object]]:
    """Decorator: reject the call before the handler body runs.

    Reads settings from ``context.bot_data`` so the check happens on every
    invocation rather than being hidden behind an invisible button.
    """

    @functools.wraps(handler)
    async def wrapper(update, context, *args, **kwargs):
        settings: Settings = context.bot_data["settings"]
        db_admins = context.bot_data.get("db_admin_ids", frozenset())
        user = update.effective_user
        if not is_admin(user.id if user else None, settings, db_admins):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⛔ Administration only.\n"
                    "Your account is not authorised to use this command."
                )
            return None
        return await handler(update, context, *args, **kwargs)

    return wrapper


__all__ = ["is_admin", "admin_only"]
