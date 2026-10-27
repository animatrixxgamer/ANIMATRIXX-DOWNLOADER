"""/start and /help."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app import APP_NAME, BRAND, __version__
from app.database import repositories as repo
from app.database.session import session_scope
from app.utils.formatting import box, esc, footer

logger = logging.getLogger(__name__)

HELP_TEXT = """{header}
<b>Search</b>
  /search &lt;title&gt;  - anime first, then K-drama
  /anime &lt;title&gt;   - anime metadata (AniList)
  /drama &lt;title&gt;   - K-drama metadata (TMDB)
  /movie &lt;title&gt;   - movie metadata (TMDB)
  /latest           - currently airing anime

<b>Series</b>
  /addseries        - register a series + archive channel
  /myseries         - list registered series
  /series &lt;title&gt;   - series details
  /delseries &lt;title&gt; - remove a series (channel untouched)

<b>Archive</b>
  /library [title]  - browse archived episodes
  /episode &lt;title&gt; S01E04
  /archiveinfo      - archive channel status
  /verifyarchive    - check bot access to every channel

<b>General</b>
  /help             - this message
  /storage          - VPS storage overview
{footer}"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.bot_data["settings"]
    user = update.effective_user
    factory = context.bot_data["session_factory"]
    try:
        async with session_scope(factory) as session:
            await repo.upsert_user(
                session,
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
    except Exception:  # pragma: no cover - startup must not crash on a write
        logger.exception("Could not persist user %s", user.id)

    text = (
        f"{box('ANIMATRIXX ANIME')}\n\n"
        f"  {BRAND}\n\n"
        f"  🎬 <b>{esc(APP_NAME)}</b>  v{esc(__version__)}\n"
        "  Your personal anime and K-drama library, powered by Telegram.\n\n"
        "  📌 You are "
        + ("<b>administrator</b>" if user.id in settings.administrators else "<b>viewer</b>")
        + "\n"
        "  Type /help to see every command.\n"
        f"{footer}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    header = box("ANIMATRIXX COMMANDS")
    await update.effective_message.reply_text(
        HELP_TEXT.format(header=header, footer=footer()),
        parse_mode=ParseMode.HTML,
    )


async def cmd_storage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disk overview - phase 12 will add automatic cleanup on top of this."""
    import shutil

    settings = context.bot_data["settings"]
    usage = shutil.disk_usage("/")
    temp_dir = settings.temp_dir
    used_by_temp = 0
    if temp_dir.exists():
        for path in temp_dir.rglob("*"):
            if path.is_file():
                try:
                    used_by_temp += path.stat().st_size
                except OSError:
                    continue
    text = (
        f"{box('ANIMATRIXX STORAGE')}\n\n"
        f"  💾 Total: {usage.total // 2**30} GiB\n"
        f"  🟢 Free: {usage.free // 2**30} GiB\n"
        f"  📁 Temp dir: {temp_dir} ({used_by_temp // 2**20} MiB)\n\n"
        "  Automatic cleanup is not enabled in this build.\n"
        f"{footer}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


__all__ = ["cmd_start", "cmd_help", "cmd_storage"]
