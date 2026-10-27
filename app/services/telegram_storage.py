"""Telegram archive uploads with pre-flight checks and idempotent retries."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TelegramError

logger = logging.getLogger(__name__)


class UploadError(Exception):
    def __init__(self, message: str, code: str = "upload_failed", permanent: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.permanent = permanent


class UploadCancelled(Exception):
    pass


@dataclass
class Preflight:
    ok: bool
    reason: str = ""
    code: str = "not_ready"


@dataclass
class UploadOutcome:
    chat_id: int
    message_id: int
    file_id: Optional[str]
    unique_file_id: Optional[str]


def caption_for(
    *,
    series_title: str | None,
    season: int,
    episode: int | None,
    resolution: str | None,
    profile: str | None,
    filename: str,
    duration: str | None = None,
) -> str:
    """Identify the file so the archive stays browsable in Telegram."""
    bits = []
    if series_title:
        bits.append(series_title)
    if episode is not None:
        bits.append(f"S{season:02d}E{episode:02d}")
    if resolution and resolution != "unknown":
        bits.append(resolution.upper())
    if profile and profile != "original":
        bits.append(profile.upper())
    header = " • ".join(bits) if bits else filename
    lines = [f"<b>{header}</b>"]
    if duration and duration != "unknown":
        lines.append(f"⏱ {duration}")
    lines.append(f"<code>{filename}</code>")
    return "\n".join(lines)


async def preflight(
    *,
    bot: Bot,
    chat_id: Optional[int],
    path: Optional[Path],
    upload_limit_mb: int,
    temp_dir: Path,
    min_free_mb: int,
    cancelled: bool,
    check_membership: bool = True,
) -> Preflight:
    """All checks from the spec, before a single byte is uploaded."""
    if cancelled:
        return Preflight(False, "The job was cancelled.", "cancelled")
    if chat_id is None:
        return Preflight(
            False,
            "No archive channel is configured for this job. Use /addseries first.",
            "no_channel",
        )
    if path is None or not Path(path).exists():
        return Preflight(False, "The file to upload no longer exists on disk.", "missing_file")

    size = Path(path).stat().st_size
    if size == 0:
        return Preflight(False, "The file is empty.", "empty_file")
    if size > upload_limit_mb * 2**20:
        return Preflight(
            False,
            f"The file is {size // 2**20} MiB but this bot can only upload "
            f"{upload_limit_mb} MiB. Enable the Local Bot API Server for larger files.",
            "too_large",
        )

    usage = shutil.disk_usage(temp_dir if Path(temp_dir).exists() else ".")
    if usage.free < min_free_mb * 2**20:
        return Preflight(
            False,
            f"Only {usage.free // 2**20} MiB free (minimum {min_free_mb} MiB).",
            "disk",
        )

    if check_membership:
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
        except TelegramError as exc:
            logger.warning("Cannot verify access to %s: %s", chat_id, exc)
            return Preflight(
                False,
                "I cannot reach the archive channel - check that I am still an admin there.",
                "no_access",
            )
        if member.status not in {"administrator", "creator"}:
            return Preflight(
                False,
                "I am not an administrator in that channel any more.",
                "no_access",
            )
    return Preflight(True)


async def upload(
    bot: Bot,
    *,
    chat_id: int,
    path: Path,
    caption: str,
    should_cancel=None,
    retry_after: int = 3,
) -> UploadOutcome:
    """Send one document, tolerating Telegram flood control."""
    path = Path(path)
    if not path.exists():
        raise UploadError("The file disappeared before the upload finished.", "missing_file", permanent=False)
    if should_cancel and should_cancel():
        raise UploadCancelled()

    attempt = 0
    last_error: TelegramError | None = None
    while attempt < retry_after:
        attempt += 1
        if should_cancel and should_cancel():
            raise UploadCancelled()
        try:
            with path.open("rb") as handle:
                message = await bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    caption=caption[:1024],
                    parse_mode="HTML",
                    supports_streaming=path.suffix.lower() in {".mp4", ".m4v", ".mov"},
                    read_timeout=180.0,
                    write_timeout=180.0,
                )
        except RetryAfter as exc:
            last_error = exc
            logger.warning("Flood control: waiting %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
            continue
        except Forbidden as exc:
            raise UploadError(
                "Telegram refused the upload - I was blocked or lost channel access.",
                "forbidden",
                permanent=True,
            ) from exc
        except TelegramError as exc:
            last_error = exc
            logger.warning("Upload attempt %s failed: %s", attempt, exc)
            await asyncio.sleep(min(10, 2 * attempt))
            continue

        return UploadOutcome(
            chat_id=chat_id,
            message_id=message.id,
            file_id=message.document.file_id if message.document else None,
            unique_file_id=message.document.file_unique_id if message.document else None,
        )

    raise UploadError(
        f"Upload failed after {retry_after} attempts: {last_error}",
        "telegram_error",
        permanent=isinstance(last_error, Forbidden),
    )


__all__ = [
    "UploadError",
    "UploadCancelled",
    "UploadOutcome",
    "Preflight",
    "preflight",
    "upload",
    "caption_for",
]
