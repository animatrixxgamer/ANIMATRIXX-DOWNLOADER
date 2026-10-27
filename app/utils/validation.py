"""Input validation and title normalisation helpers."""

from __future__ import annotations

import re
from typing import Optional

_EPISODE_RE = re.compile(r"^\s*s?\s*(\d{1,3})\s*[ex]\s*(\d{1,4})\s*$", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# Keep letters/digits (unicode aware) and spaces; drop punctuation noise.
_NOISE_RE = re.compile(r"[^\w\s]", re.UNICODE)
_QUALITY_RE = re.compile(r"\b(2160p|1080p|720p|480p|360p|4k|hd|sd)\b", re.IGNORECASE)


def normalize_title(title: str) -> str:
    """Lower-cased, punctuation-free key used for duplicate-safe lookups."""
    text = (title or "").strip().lower()
    text = text.replace("&", " and ")
    text = _NOISE_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def strip_quality_tokens(title: str) -> str:
    """Remove resolution tokens a user may have pasted into a search."""
    return _WS_RE.sub(" ", _QUALITY_RE.sub(" ", title or "")).strip()


def parse_episode_ref(value: str) -> Optional[tuple[int, int]]:
    """Parse ``S01E04``, ``s1e4`` or ``1x4`` into ``(1, 4)``."""
    match = _EPISODE_RE.match(value or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def episode_code(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def parse_channel_ref(value: str) -> Optional[int | str]:
    """Accept ``-1001234``, ``@channelname`` or a bare channel name.

    Returns an int chat id, a ``@username``, or ``None`` when unusable.
    """
    value = (value or "").strip()
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    if value.startswith("@"):
        return value if re.fullmatch(r"@[A-Za-z0-9_]{4,}", value) else None
    return None


def is_safe_filename(name: str) -> bool:
    """Reject path traversal and separators in user supplied filenames."""
    name = (name or "").strip()
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    return True


__all__ = [
    "normalize_title",
    "strip_quality_tokens",
    "parse_episode_ref",
    "episode_code",
    "parse_channel_ref",
    "is_safe_filename",
]
