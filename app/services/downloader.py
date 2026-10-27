"""Chunked, resumable, size-capped HTTP downloads.

Design rules from the spec:
- validate every hop (scheme, host, DNS, redirect) before connecting;
- stream to disk, never load a file into memory;
- enforce the configured size cap while streaming, not just from headers;
- verify the actual file type from magic bytes before accepting it;
- write only inside the configured temp directory;
- never execute a downloaded file;
- report real progress (total only when the server provides it).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.services.urls import URLValidationError, redact_url, validate_target

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_NAME = 180
USER_AGENT = "AnimatrixxAnimeBot/0.1 (authorized personal library)"

# Magic-byte signatures -> media type we accept.
_MAGIC: list[tuple[bytes, str, int]] = [
    (b"\x1a\x45\xdf\xa3", "matroska", 0),  # mkv / webm
    (b"RIFF", "avi", 0),
    (b"FLV", "flv", 0),
    (b"\x47", "mpeg-ts", 0),  # transport stream sync byte
    (b"OggS", "ogg", 0),
    (b"ID3", "mp3", 0),
    (b"\x1f\x8b", "gzip", 0),  # explicitly rejected below
]
_BLOCKED_MAGIC = [
    (b"<!DOCTYPE", "html"),
    (b"<html", "html"),
    (b"PK\x03\x04", "zip"),
    (b"%PDF", "pdf"),
    (b"MZ", "exe"),
    (b"\x7fELF", "elf"),
    (b"\x23\x21", "script"),
    (b"\xca\xfe\xba\xbe", "java-class"),
]
_ALLOWED_EXT = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".mov", ".ts", ".mpg",
    ".mpeg", ".ogv", ".ogg", ".mp3", ".m4a", ".aac", ".wav", ".srt", ".ass",
}


class DownloadError(Exception):
    def __init__(self, message: str, code: str = "download_failed", status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.permanent = status in {400, 401, 403, 404, 410, 451} or code in {
            "file_type_rejected",
            "size_limit",
            "unsupported_scheme",
            "blocked_destination",
            "invalid_url",
        }


class DownloadCancelled(Exception):
    """Raised when the user cancelled the job."""


@dataclass
class DownloadResult:
    path: Path
    bytes_written: int
    total_bytes: Optional[int]
    content_type: Optional[str]
    filename: str
    etag: Optional[str]
    resumed: bool


ProgressCallback = Callable[[int, Optional[int], float], Awaitable[None] | None]
CancelCheck = Callable[[], bool]


def sanitize_filename(name: str, fallback: str = "download.bin") -> str:
    """Strip directories and anything that could escape TEMP_DIR."""
    name = unquote(name or "").strip().replace("\x00", "")
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name, flags=re.UNICODE)
    name = name.strip(" .")
    if not name or name in {".", ".."}:
        return fallback
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            name = stem[: MAX_NAME - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_NAME]
    return name


def sniff_media(header: bytes) -> Optional[str]:
    """Identify the real file type from its first bytes."""
    if not header:
        return None
    lower = header.lower()
    for magic, kind in _BLOCKED_MAGIC:
        if lower.startswith(magic) or header.startswith(magic):
            return kind
    # MP4/MOV family: bytes 4..8 hold the box type (`ftyp`, `moov`, ...)
    if len(header) >= 8 and header[4:8] in {b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip"}:
        return "mp4"
    for magic, kind, _offset in _MAGIC:
        if header.startswith(magic):
            return kind
    return None


def accept_file(header: bytes, content_type: str | None, filename: str) -> None:
    """Raise DownloadError when the payload is not media we may archive."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in {"text/html", "application/xhtml+xml", "application/zip", "application/pdf"}:
        raise DownloadError(
            "That link returned a web page or archive instead of a video file.",
            code="file_type_rejected",
            status=200,
        )
    kind = sniff_media(header)
    if kind in {"html", "zip", "pdf", "exe", "elf", "script", "java-class", "gzip"}:
        raise DownloadError(
            f"The downloaded data is a {kind} file, not media.",
            code="file_type_rejected",
            status=200,
        )
    suffix = Path(filename).suffix.lower()
    looks_media = kind is not None
    extension_ok = suffix in _ALLOWED_EXT
    if not looks_media and not extension_ok:
        raise DownloadError(
            "The file type could not be verified as video/audio, so it was rejected.",
            code="file_type_rejected",
            status=200,
        )


def _content_length(headers: httpx.Headers) -> Optional[int]:
    raw = headers.get("content-length")
    if raw and raw.isdigit():
        return int(raw)
    return None


def _content_range_total(headers: httpx.Headers) -> Optional[int]:
    """`bytes 100-999/12345` -> 12345"""
    value = headers.get("content-range") or ""
    match = re.search(r"/(\d+)\s*$", value)
    if match and match.group(1).isdigit():
        return int(match.group(1))
    return None


def _filename_from(url: str, headers: httpx.Headers) -> str:
    disposition = headers.get("content-disposition") or ""
    match = re.search(r"filename\*?=(?:UTF-8''| \")?([^\";]+)", disposition, re.IGNORECASE)
    if match:
        return sanitize_filename(match.group(1).strip('"'))
    path = unquote(urlsplit(url).path)
    name = sanitize_filename(path.rsplit("/", 1)[-1] if path else "")
    return name if name != "download.bin" or path else name


async def download(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int,
    allow_private: bool = False,
    allowed_ports: frozenset[int] = frozenset({80, 443}),
    connect_timeout: float = 15.0,
    read_timeout: float = 60.0,
    max_redirects: int = 5,
    progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
    filename_hint: Optional[str] = None,
) -> DownloadResult:
    """Stream one file to ``dest_dir``. Raises DownloadError/DownloadCancelled."""
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(connect_timeout, read=read_timeout, write=read_timeout, pool=10.0)
    headers = {"User-Agent": USER_AGENT, "Accept": "video/*, video/x-matroska, */*;q=0.5"}

    current = url
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers)
    try:
        # --- redirect loop with per-hop validation -------------------
        for hop in range(max_redirects + 1):
            try:
                current = await validate_target(
                    current, allow_private=allow_private, allowed_ports=allowed_ports
                )
            except URLValidationError as exc:
                raise DownloadError(exc.message, code=exc.code) from exc

            request = client.build_request("GET", current)
            try:
                response = await client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise DownloadError("The source timed out.", code="timeout", status=None) from exc
            except httpx.HTTPError as exc:
                raise DownloadError("The source could not be reached.", code="network") from exc

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise DownloadError("The source returned a broken redirect.", code="network", status=301)
                if hop == max_redirects:
                    raise DownloadError("Too many redirects.", code="too_many_redirects", status=301)
                current = urljoin(current, location)
                continue
            break
        else:  # pragma: no cover - loop always breaks or raises
            raise DownloadError("Too many redirects.", code="too_many_redirects")

        status = response.status_code
        if status in {429} or 500 <= status < 600:
            await response.aclose()
            raise DownloadError(f"The source is unavailable (HTTP {status}).", code="transient", status=status)
        if status >= 400:
            await response.aclose()
            raise DownloadError(
                f"The source refused the request (HTTP {status}).", code=f"http_{status}", status=status
            )
        if status not in {200, 206}:
            await response.aclose()
            raise DownloadError(f"Unexpected response (HTTP {status}).", code="network", status=status)

        headers_out = response.headers
        total = _content_length(headers_out)
        if status == 206:
            total = _content_range_total(headers_out) or total
        content_type = headers_out.get("content-type")
        etag = headers_out.get("etag")

        if total is not None and total > max_bytes:
            await response.aclose()
            raise DownloadError(
                f"The file is {total // 2**20} MiB, above the {max_bytes // 2**20} MiB limit.",
                code="size_limit",
                status=status,
            )

        filename = filename_hint or _filename_from(current, headers_out)
        suffix = Path(filename).suffix.lower()
        if suffix not in _ALLOWED_EXT:
            filename = filename + ".bin" if not suffix else filename

        target = (dest_dir / filename).resolve()
        if target.parent != dest_dir:  # pragma: no cover - defense in depth
            raise DownloadError("Refusing to write outside the temp directory.", code="invalid_url")

        # --- resumable partial file ---------------------------------
        partial = target.with_name(target.name + ".part")
        resumed = False
        written = 0
        if partial.exists():
            written = partial.stat().st_size
            if written >= (total or 0) > 0:
                partial.unlink(missing_ok=True)
                written = 0
            elif written:
                resumed = True

        if resumed and status != 206:
            written = 0  # server ignored the range - restart cleanly
            resumed = False

        mode = "ab" if resumed else "wb"
        started = time.monotonic()
        window_start = started
        window_bytes = 0
        speed = 0.0
        first_chunk = b""
        rejected = False

        try:
            with partial.open(mode) as handle:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    if should_cancel and should_cancel():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    if not first_chunk:
                        first_chunk = chunk[:64]
                        try:
                            accept_file(first_chunk, content_type, filename)
                        except DownloadError:
                            rejected = True
                            raise
                    written += len(chunk)
                    if written > max_bytes:
                        raise DownloadError(
                            f"The download passed the {max_bytes // 2**20} MiB limit.",
                            code="size_limit",
                            status=status,
                        )
                    handle.write(chunk)
                    window_bytes += len(chunk)
                    now = time.monotonic()
                    if now - window_start >= 1.0:
                        speed = window_bytes / (now - window_start)
                        window_start, window_bytes = now, 0
                    if progress:
                        result = progress(written, total, speed)
                        if asyncio.iscoroutine(result):
                            await result
                handle.flush()
        except DownloadCancelled:
            raise
        except DownloadError:
            # rejected payload (wrong type / size) - keep nothing behind
            partial.unlink(missing_ok=True)
            raise
        finally:
            await response.aclose()

        if rejected:
            partial.unlink(missing_ok=True)
            raise DownloadError(
                "The link did not return a video file.", code="file_type_rejected", status=status
            )

        # Server may have sent more than advertised; re-check the cap.
        if written > max_bytes:
            partial.unlink(missing_ok=True)
            raise DownloadError(
                f"The download passed the {max_bytes // 2**20} MiB limit.",
                code="size_limit",
                status=status,
            )

        if total is not None and written < total:
            raise DownloadError(
                f"The transfer stopped early ({written} of {total} bytes).", code="network", status=status
            )
        if written == 0:
            partial.unlink(missing_ok=True)
            raise DownloadError("The source returned an empty file.", code="network", status=status)

        partial.replace(target)
        logger.info(
            "Downloaded %s bytes from %s -> %s", written, redact_url(current), target.name
        )
        return DownloadResult(
            path=target,
            bytes_written=written,
            total_bytes=total,
            content_type=content_type,
            filename=target.name,
            etag=etag,
            resumed=resumed,
        )
    except httpx.HTTPError:  # pragma: no cover - defensive
        raise DownloadError("The source connection failed.", code="network")
    finally:
        await client.aclose()


__all__ = [
    "DownloadError",
    "DownloadCancelled",
    "DownloadResult",
    "download",
    "sanitize_filename",
    "sniff_media",
    "accept_file",
    "CHUNK_SIZE",
]
