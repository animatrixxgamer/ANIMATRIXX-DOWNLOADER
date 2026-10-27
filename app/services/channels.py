"""Resolving and verifying Telegram channels (shared by every channel flow)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from telegram import Chat
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

ADMIN_STATUSES = {"administrator", "creator"}


@dataclass
class ChannelCheck:
    """Result of asking Telegram whether the bot can actually post here."""

    ok: bool
    chat_id: Optional[int] = None
    title: Optional[str] = None
    username: Optional[str] = None
    status: str = "missing"  # active | denied | missing
    reason: str = ""

    @property
    def display(self) -> str:
        if self.title:
            return self.title
        if self.username:
            return f"@{self.username}"
        return str(self.chat_id or "?")


def channel_from_message(message) -> Optional[Chat]:
    """The channel/group a forwarded message came from, if any."""
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin is not None else None
    if chat is None:
        chat = getattr(message, "forward_from_chat", None)
    if chat is not None and getattr(chat, "type", None) in {"channel", "group", "supergroup"}:
        return chat
    return None


async def resolve_channel(bot, ref: int | str) -> Chat:
    """Turn an id / @username / forwarded chat into a Chat.

    Raises ``ValueError`` with a message that is safe to show the admin.
    """
    try:
        chat = await bot.get_chat(ref)
    except TelegramError as exc:
        raise ValueError(
            "I could not open that chat. Check the @username or id, and make "
            "sure I am added to the channel."
        ) from exc
    if chat.type not in {"channel", "group", "supergroup"}:
        raise ValueError("That is not a channel or group - use a private channel.")
    return chat


async def verify_channel(bot, chat: Chat | int | str) -> ChannelCheck:
    """Check that the bot can post in the channel right now."""
    try:
        if not isinstance(chat, Chat):
            chat = await resolve_channel(bot, chat)
    except ValueError as exc:
        return ChannelCheck(ok=False, status="missing", reason=str(exc))

    try:
        member = await bot.get_chat_member(chat.id, bot.id)
    except TelegramError as exc:
        logger.warning("Cannot verify access to %s: %s", chat.id, exc)
        return ChannelCheck(
            ok=False,
            chat_id=chat.id,
            title=getattr(chat, "title", None),
            username=getattr(chat, "username", None),
            status="missing",
            reason="I cannot reach that chat - it may have been deleted.",
        )

    if member.status not in ADMIN_STATUSES:
        return ChannelCheck(
            ok=False,
            chat_id=chat.id,
            title=getattr(chat, "title", None),
            username=getattr(chat, "username", None),
            status="denied",
            reason=(
                "I am not an administrator there. Add me as an admin with "
                "permission to post messages."
            ),
        )
    return ChannelCheck(
        ok=True,
        chat_id=chat.id,
        title=getattr(chat, "title", None),
        username=getattr(chat, "username", None),
        status="active",
    )


async def invite_link(bot, chat_id: int) -> Optional[str]:
    """Best-effort invite link for a private channel.

    Private channel messages have no public link, so we ask Telegram to mint
    an invite link instead of inventing one.
    """
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramError as exc:
        logger.info("Cannot read chat %s for invite link: %s", chat_id, exc)
        return None

    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}"

    try:
        link = await bot.create_chat_invite_link(chat_id, name="Animatrixx archive")
        return link.invite_link
    except TelegramError as exc:
        logger.info("create_chat_invite_link failed for %s: %s", chat_id, exc)
    try:
        # Fallback: export the channel's primary invite link as a plain string.
        return await bot.export_chat_invite_link(chat_id)
    except TelegramError:
        return None


__all__ = [
    "ChannelCheck",
    "ADMIN_STATUSES",
    "channel_from_message",
    "resolve_channel",
    "verify_channel",
    "invite_link",
]