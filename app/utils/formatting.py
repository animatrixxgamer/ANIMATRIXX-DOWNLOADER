"""Message formatting helpers - HTML parse mode everywhere."""

from __future__ import annotations

import html
from datetime import datetime, timedelta

BOX_WIDTH = 30


def esc(value: object) -> str:
    """HTML-escape anything that came from a user or a metadata provider."""
    return html.escape(str(value if value is not None else ""), quote=False)


def box(title: str) -> str:
    """``╭━━━「 TITLE 」━━━╮`` style heading."""
    inner = f"「 {title} 」"
    pad = max(3, BOX_WIDTH - len(inner))
    left = (pad // 2) * "━"
    right = (pad - pad // 2) * "━"
    return f"╭{left}{inner}{right}╮"


# A constant, not a function: messages interpolate it and pass it straight
# into "\n".join(...), where a callable would blow up at send time.
footer: str = "╰" + "━" * (BOX_WIDTH + 4) + "╯"


def progress_bar(percent: float, width: int = 10) -> str:
    """Honest bar - never fabricated, clamped to 0..100."""
    if percent is None or percent != percent:  # NaN guard
        return "[" + "·" * width + "]"
    filled = max(0, min(width, round(percent / 100 * width)))
    return "[" + "■" * filled + "□" * (width - filled) + "]"


def format_size(size: int | None) -> str:
    """Human readable byte size (KB / MB / GB)."""
    value = size
    if not value:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    value = float(value)
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f} {units[idx]}"


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "unknown"
    td = timedelta(seconds=int(seconds))
    hours, rem = divmod(td.seconds + td.days * 86400, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def format_dt(value: datetime | None) -> str:
    if not value:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def kv(label: str, value: object) -> str:
    return f"  • <b>{esc(label)}:</b> {esc(value)}"


__all__ = [
    "esc",
    "box",
    "footer",
    "progress_bar",
    "format_size",
    "format_duration",
    "format_dt",
    "kv",
]
