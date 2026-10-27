"""FFprobe inspection and FFmpeg encoding profiles.

Every FFmpeg call is an argument list (never a shell string), progress comes
from FFmpeg's own `-progress` stream, and a cancelled job terminates the
subprocess and removes the partial output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

PROFILES = ("original", "high", "balanced", "small", "target")
RESOLUTIONS = ("480p", "720p", "1080p")

_PROFILE_LABEL = {
    "original": "ORIGINAL",
    "high": "HIGH QUALITY",
    "balanced": "BALANCED",
    "small": "SMALL",
    "target": "TARGET SIZE",
}

# Containers/codecs that travel together without transcoding.
_MP4_VIDEO = {"h264", "avc1", "hevc", "h265", "av1", "mpeg4"}
_MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "mp4a"}
_AUDIO_OUT_MP4 = "aac"
_AUDIO_BITRATE = {"high": "192k", "balanced": "192k", "small": "128k"}
_CRF = {"original": 18, "high": 18, "balanced": 23, "small": 28}
_PRESET = {"original": "medium", "high": "medium", "balanced": "medium", "small": "slow"}


class EncodeError(Exception):
    def __init__(self, message: str, code: str = "encode_failed", permanent: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.permanent = permanent


class EncodeCancelled(Exception):
    pass


class ProbeError(Exception):
    def __init__(self, message: str, permanent: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.permanent = permanent


@dataclass
class MediaInfo:
    path: Path
    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    video_codec: Optional[str]
    audio_codecs: list[str] = field(default_factory=list)
    subtitle_codecs: list[str] = field(default_factory=list)
    container: Optional[str] = None
    size_bytes: Optional[int] = None
    has_audio: bool = False

    @property
    def resolution_label(self) -> Optional[str]:
        if not self.height:
            return None
        for label in ("480p", "720p", "1080p", "2160p"):
            if str(self.height) in label:
                return label
        return f"{self.height}p"

    @property
    def aspect_note(self) -> str:
        if not self.width or not self.height:
            return "unknown"
        return f"{self.width}x{self.height}"


# ------------------------------------------------------------------ probe
async def probe(path: Path | str, ffprobe: str = "ffprobe", timeout: float = 60.0) -> MediaInfo:
    """Inspect a media file. Raises ProbeError when FFprobe cannot parse it."""
    path = Path(path)
    if not path.exists():
        raise ProbeError("The file no longer exists.", permanent=False)
    if path.stat().st_size == 0:
        raise ProbeError("The file is empty.", permanent=True)

    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except FileNotFoundError as exc:
        raise ProbeError(
            "FFprobe is not installed. Install ffmpeg (which provides ffprobe).",
            permanent=True,
        ) from exc
    except asyncio.TimeoutError as exc:
        raise ProbeError("FFprobe timed out while inspecting the file.", permanent=False) from exc

    if process.returncode != 0:
        detail = (stderr or b"").decode(errors="replace").strip().splitlines()
        logger.warning("ffprobe failed for %s: %s", path.name, detail[-1] if detail else "unknown")
        raise ProbeError("The file could not be inspected - it may be corrupt.", permanent=True)

    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError as exc:  # pragma: no cover - ffprobe contract
        raise ProbeError("FFprobe returned an unreadable report.", permanent=False) from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    fps = None
    if video and video.get("avg_frame_rate") and video["avg_frame_rate"] != "0/0":
        try:
            num, den = video["avg_frame_rate"].split("/")
            if float(den) > 0:
                fps = round(float(num) / float(den), 3)
        except (ValueError, ZeroDivisionError):
            fps = None

    duration = None
    if fmt.get("duration"):
        try:
            duration = float(fmt["duration"])
        except ValueError:
            duration = None
    if duration is None and video and video.get("duration"):
        try:
            duration = float(video["duration"])
        except ValueError:
            duration = None

    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=fps,
        video_codec=(video or {}).get("codec_name"),
        audio_codecs=[s.get("codec_name") for s in audio if s.get("codec_name")],
        subtitle_codecs=[s.get("codec_name") for s in subs if s.get("codec_name")],
        container=fmt.get("format_name"),
        size_bytes=int(fmt["size"]) if fmt.get("size", "").isdigit() else path.stat().st_size,
        has_audio=bool(audio),
    )


# ------------------------------------------------------------------ plan
def target_bitrate_kbps(target_size_mb: int, duration: Optional[float], audio_kbps: int = 128) -> int:
    """Two-pass bitrate maths. We state honestly that the size is a target."""
    if not duration or duration <= 0:
        return 800
    total_bits = target_size_mb * 8 * 1024 * 1024
    video_bits = max(total_bits - duration * audio_kbps * 1000, 1_000_000)
    return max(150, int(video_bits / duration / 1000))


def scale_filter(resolution: str | None, info: MediaInfo) -> Optional[str]:
    """Fit inside the target height - never upscale, never stretch."""
    if not resolution:
        return None
    if resolution not in RESOLUTIONS:
        raise EncodeError(f"Unknown resolution {resolution}", permanent=True)
    height = int(resolution.rstrip("p"))
    if info.height and info.height <= height:
        return None  # already small enough; upscaling is refused by default
    return f"scale=-2:min(ih\\,{height})"


def build_encode_args(
    src: Path,
    dst: Path,
    *,
    profile: str,
    info: MediaInfo,
    out_format: str = "mp4",
    resolution: str | None = None,
    target_size_mb: int | None = None,
    ffmpeg: str = "ffmpeg",
    two_pass: bool = False,
    pass_number: int = 1,
) -> list[str]:
    """Pure builder for the FFmpeg argv (unit tested without FFmpeg)."""
    if profile not in PROFILES:
        raise EncodeError(f"Unknown profile {profile}", permanent=True)

    args: list[str] = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-y", "-i", str(src),
    ]
    vf = scale_filter(resolution, info)
    filters: list[str] = [vf] if vf else []

    if (profile == "original" or profile == "target") and not vf and target_size_mb is None:
        # Remux only: keep every track bit-for-bit, no transcoding at all.
        return args + ["-map", "0", "-c", "copy", str(dst)]
    # ORIGINAL with a requested resolution still needs a video transcode,
    # so it falls through to the normal encode path below.

    audio_codec = "copy"
    compatible = True
    if out_format == "mp4":
        compatible = info.video_codec in _MP4_VIDEO if info.video_codec else True
        if info.audio_codecs and not set(info.audio_codecs) <= _MP4_AUDIO:
            compatible = False
        if not compatible or profile in {"small", "target"}:
            audio_codec = _AUDIO_OUT_MP4
    if not info.has_audio:
        audio_codec = "none"  # handled below with -an

    if profile == "target" and target_size_mb:
        audio_kbps = 128 if out_format == "mp4" else 160
        bitrate = target_bitrate_kbps(target_size_mb, info.duration, audio_kbps)
        args += [
            "-c:v", "libx264", "-b:v", f"{bitrate}k", "-maxrate", f"{int(bitrate * 1.45)}k",
            "-bufsize", f"{bitrate}k", "-preset", "medium",
            "-pix_fmt", "yuv420p",
        ]
        if two_pass:
            args += ["-pass", str(pass_number), "-passlogfile", str(dst.with_suffix(""))]
        args += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info.has_audio else ["-an"]
    else:
        args += [
            "-c:v", "libx264",
            "-crf", str(_CRF[profile]),
            "-preset", _PRESET[profile],
            "-pix_fmt", "yuv420p",
        ]
        if audio_codec == "none":
            args += ["-an"]
        elif audio_codec == "copy":
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", audio_codec, "-b:a", _AUDIO_BITRATE.get(profile, "160k")]

    if filters:
        args += ["-vf", ",".join(filters)]
    if out_format == "mp4" and info.subtitle_codecs:
        # MP4 cannot carry ASS/SRT bitstreams as-is: drop them deliberately.
        args += ["-sn"]

    return args + ["-progress", "pipe:1", "-nostats", str(dst)]


def profile_label(profile: str) -> str:
    return _PROFILE_LABEL.get(profile, profile.upper())


# ------------------------------------------------------------------ encode
ProgressCallback = Callable[[Optional[float], str], Awaitable[None] | None]
CancelCheck = Callable[[], bool]


def parse_progress_line(line: str, duration: Optional[float]) -> Optional[float]:
    """`out_time_us=123000` + duration -> percent. Never invents a number."""
    if not duration or duration <= 0:
        return None
    key, _, value = line.strip().partition("=")
    if key not in {"out_time_us", "out_time_ms"}:
        return None
    try:
        micros = float(value)
    except ValueError:
        return None
    percent = (micros / 1_000_000) / duration * 100
    return max(0.0, min(100.0, percent))


async def encode(
    src: Path,
    dst: Path,
    *,
    profile: str,
    info: MediaInfo,
    out_format: str = "mp4",
    resolution: str | None = None,
    target_size_mb: int | None = None,
    ffmpeg: str = "ffmpeg",
    timeout: float = 3600.0,
    progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCheck] = None,
) -> Path:
    """Run one encode pass and validate the output. Raises EncodeError/EncodeCancelled."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = build_encode_args(
        src, dst, profile=profile, info=info, out_format=out_format,
        resolution=resolution, target_size_mb=target_size_mb, ffmpeg=ffmpeg,
    )
    two_pass = profile == "target" and bool(target_size_mb)
    logger.info("FFmpeg: %s", " ".join(shlex.quote(a) for a in args))

    passes = 2 if two_pass else 1
    for pass_number in range(1, passes + 1):
        if two_pass:
            args = build_encode_args(
                src, dst, profile=profile, info=info, out_format=out_format,
                resolution=resolution, target_size_mb=target_size_mb, ffmpeg=ffmpeg,
                two_pass=True, pass_number=pass_number,
            )
        await _run_ffmpeg(
            args, duration=info.duration, timeout=timeout,
            progress=progress, should_cancel=should_cancel, out_path=dst,
        )

    # remove FFmpeg's two-pass bitrate logs next to the output
    for leftover in dst.parent.glob(f"{dst.stem}-*.log*"):
        leftover.unlink(missing_ok=True)

    if not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise EncodeError("FFmpeg produced no output.", permanent=False)

    try:
        await probe(dst, ffprobe=ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg else "ffprobe")
    except ProbeError as exc:
        dst.unlink(missing_ok=True)
        raise EncodeError(f"The encoded file failed validation: {exc.message}", permanent=False) from exc
    return dst


async def _run_ffmpeg(
    args: list[str],
    *,
    duration: Optional[float],
    timeout: float,
    progress: Optional[ProgressCallback],
    should_cancel: Optional[CancelCheck],
    out_path: Path,
) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise EncodeError(
            "FFmpeg is not installed. Install ffmpeg or set FFMPEG_BINARY.", permanent=True
        ) from exc

    stderr_tail: list[str] = []

    async def _drain_stderr() -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                stderr_tail.append(text)
                del stderr_tail[:-20]

    stderr_task = asyncio.create_task(_drain_stderr())
    deadline = asyncio.get_event_loop().time() + timeout
    cancelled = False

    try:
        assert process.stdout is not None
        while True:
            if should_cancel and should_cancel():
                cancelled = True
                _terminate(process)
                break
            if asyncio.get_event_loop().time() > deadline:
                _terminate(process)
                raise EncodeError("Encoding timed out.", permanent=False)
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            parsed = parse_progress_line(line.decode(errors="replace"), duration)
            if parsed is not None and progress:
                result = progress(parsed, "encoding")
                if asyncio.iscoroutine(result):
                    await result

        await asyncio.wait_for(process.wait(), timeout=30)
    finally:
        stderr_task.cancel()
        if process.returncode in (None, -15, -9):  # pragma: no cover - timing dependent
            _terminate(process, force=True)

    if cancelled:
        out_path.unlink(missing_ok=True)
        raise EncodeCancelled()

    if process.returncode != 0:
        detail = stderr_tail[-1] if stderr_tail else "unknown FFmpeg error"
        out_path.unlink(missing_ok=True)
        raise EncodeError(f"FFmpeg failed: {detail}", permanent=False)


def _terminate(process: asyncio.subprocess.Process, force: bool = False) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:  # pragma: no cover
        return
    if force:
        try:
            process.kill()
        except ProcessLookupError:  # pragma: no cover
            pass


__all__ = [
    "MediaInfo",
    "PROFILES",
    "RESOLUTIONS",
    "EncodeError",
    "EncodeCancelled",
    "ProbeError",
    "probe",
    "build_encode_args",
    "scale_filter",
    "target_bitrate_kbps",
    "parse_progress_line",
    "encode",
    "profile_label",
]
