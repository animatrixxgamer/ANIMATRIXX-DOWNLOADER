"""Throttled progress messages for long-running jobs."""

from __future__ import annotations

import logging
import time
from typing import Optional

from telegram import Message
from telegram.error import TelegramError

from app.utils.formatting import box, esc, footer, format_duration, format_size, progress_bar

logger = logging.getLogger(__name__)


class StatusReporter:
    """Edits one Telegram status message, at most once per interval.

    Telegram rate limits matter more than a perfectly smooth progress bar, so
    updates are throttled and the caller passes honest values only.
    """

    def __init__(
        self,
        bot,
        chat_id: int,
        message: Optional[Message] = None,
        interval: float = 2.0,
        message_id: Optional[int] = None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message = message
        self.interval = interval
        self._message_id = message_id or (message.id if message else None)
        self._last_sent = 0.0

    @property
    def message_id(self) -> Optional[int]:
        return self._message_id

    def _due(self, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self._last_sent >= self.interval:
            self._last_sent = now
            return True
        return False

    async def ensure(self) -> None:
        if self.message is None:
            self.message = await self.bot.send_message(self.chat_id, "⏳ Starting…")

    async def send(self, text: str, force: bool = False, reply_markup=None) -> None:
        if not self._due(force=force):
            return
        try:
            if self._message_id is None:
                self.message = await self.bot.send_message(
                    self.chat_id, text, reply_markup=reply_markup
                )
                self._message_id = self.message.id
            else:
                await self.bot.edit_message_text(
                    text,
                    chat_id=self.chat_id,
                    message_id=self._message_id,
                    reply_markup=reply_markup,
                )
        except TelegramError as exc:
            # "message is not modified" and deleted messages are not fatal.
            logger.debug("Status update skipped: %s", exc)

    async def fail(self, reason: str) -> None:
        text = (
            f"{box('ANIMATRIXX JOB FAILED')}\n\n"
            f"  ❌ {esc(reason)}\n\n"
            "  Use <code>/retry &lt;id&gt;</code> to try again,\n"
            "  or <code>/jobs</code> to see failures.\n"
            f"{footer}"
        )
        await self.send(text, force=True)

    async def done(self, detail: str) -> None:
        text = f"{box('ANIMATRIXX JOB DONE')}\n\n  ✅ {esc(detail)}\n{footer}"
        await self.send(text, force=True)


def render_job(job, *, title: str = "ANIMATRIXX PIPELINE") -> str:
    """Build an honest job status card from database values."""
    header = box(title)
    state = str(job.state).upper()
    percent = job.percent
    bar = progress_bar(percent) if percent is not None else "[" + "·" * 10 + "]"

    lines = [
        header,
        "",
        f"  🔢 Job: <b>#{job.id}</b>  [{esc(state)}]",
        f"  🎬 {esc(job.filename or job.source_url or 'file')}",
        f"  📦 Profile: {esc(job.profile)}"
        + (f" • {esc(job.resolution)}" if job.resolution else ""),
    ]
    if job.series_id:
        lines.append(f"  📺 Series id: {job.series_id} • S{job.season_number:02d}E{job.episode_number or 0:02d}")

    if job.stage_detail:
        lines.append(f"  🔄 {esc(job.stage_detail)}")
    if percent is not None:
        lines.append(f"  {bar} {percent:.0f}%")
    elif state in {"DOWNLOADING", "ENCODING", "UPLOADING"}:
        lines.append(f"  {bar} working (total unknown)")

    if job.total_bytes:
        lines.append(
            f"  📥 {format_size(job.done_bytes)} / {format_size(job.total_bytes)}"
            + (f"  •  {format_size(int(job.speed_bps))}/s" if job.speed_bps else "")
        )
    elif job.done_bytes:
        lines.append(f"  📥 {format_size(job.done_bytes)} downloaded (total unknown)")

    if job.attempts:
        lines.append(f"  🔁 Attempts: {job.attempts}")
    if job.last_error:
        lines.append(f"  ⚠️ {esc(job.last_error[:200])}")
    lines.append(footer)
    return "\n".join(lines)


__all__ = ["StatusReporter", "render_job"]
