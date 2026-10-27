"""/download /encode /queue /progress /cancel /retry - pipeline commands."""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.database import job_repositories as jobs
from app.database import repositories as repo
from app.database.session import session_scope
from app.services.encoder import PROFILES, RESOLUTIONS, profile_label
from app.services.progress import StatusReporter, render_job
from app.services.validation_flags import ParsedFlags, parse_flags
from app.services.downloader import sanitize_filename
from app.services.permissions import admin_only
from app.workers.job_manager import record_status_message
from app.utils.formatting import box, esc, footer, kv
from app.utils.validation import parse_episode_ref

logger = logging.getLogger(__name__)

MEDIA_EXTS = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".flv", ".ts", ".mpg", ".mpeg"}


def _manager(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["job_manager"]


def _flag(value: str | None, allowed: set[str], name: str) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value not in allowed:
        raise ValueError(f"--{name} must be one of {sorted(allowed)}")
    return value


async def _limit_ok(context, owner_id: int) -> tuple[bool, int]:
    settings = context.bot_data["settings"]
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        from sqlalchemy import func, select

        from app.database.models import MediaJob
        from app.workers.states import ACTIVE

        count = await session.scalar(
            select(func.count(MediaJob.id)).where(
                MediaJob.owner_telegram_id == owner_id,
                MediaJob.state.in_([s.value for s in ACTIVE]),
            )
        )
    return int(count or 0) < settings.max_jobs_per_user, int(count or 0)


@admin_only
async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/download <url> [--series title] [--ep S01E04] [--profile p] [--res 720p]

    Also accepts a reply to a video/document you own: the bot archives that
    upload instead of fetching a URL.
    """
    message = update.effective_message
    try:
        flags = parse_flags(context.args)
    except ValueError as exc:
        await message.reply_text(f"⚠️ {esc(exc)}")
        return

    settings = context.bot_data["settings"]
    factory = context.bot_data["session_factory"]
    user = update.effective_user

    ok, active = await _limit_ok(context, user.id)
    if not ok:
        await message.reply_text(
            f"⛔ You already have {active} active job(s) "
            f"(limit {settings.max_jobs_per_user}). Wait or /cancel one."
        )
        return

    source_type = None
    source_url = None
    source_file_id = None
    filename = None

    replied = message.reply_to_message
    attached = replied.video or replied.document if replied else None
    if attached and not flags.positional:
        ext = Path(attached.file_name or "").suffix.lower()
        if ext and ext not in MEDIA_EXTS:
            await message.reply_text(
                f"⛔ That looks like a <b>{esc(ext)}</b> file. Only video/audio files are accepted.",
                parse_mode=ParseMode.HTML,
            )
            return
        source_type = "telegram_file"
        source_file_id = attached.file_id
        filename = sanitize_filename(attached.file_name or f"upload_{message.id}{ext or '.mp4'}")
    elif flags.positional:
        url = flags.positional[0]
        if not url.lower().startswith(("http://", "https://")):
            await message.reply_text(
                "⛔ Give me an http/https link, or reply to a video file with /download."
            )
            return
        source_type = "url"
        source_url = url
        filename = sanitize_filename(Path(url.split("?", 1)[0]).name or "download.mp4")
    else:
        await message.reply_text(
            "Usage:\n"
            "  <code>/download https://host/file.mp4</code>\n"
            "  or reply to a video with <code>/download</code>\n\n"
            "Flags: <code>--series Title</code> <code>--ep S01E04</code> "
            "<code>--profile balanced</code> <code>--res 720p</code> "
            "<code>--out mp4|mkv</code> <code>--size 150</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # optional series mapping + episode
    series_id = None
    season, episode = 1, None
    if flags.opts.get("series"):
        async with session_scope(factory) as session:
            series = await repo.get_series_by_title(session, flags.opts["series"])
            if series is None:
                found = await repo.find_series(session, flags.opts["series"], limit=1)
                series = found[0] if found else None
            if series is None:
                await message.reply_text(
                    f"⛔ No series named <b>{esc(flags.opts['series'])}</b>. "
                    "Register it first with /addseries.",
                    parse_mode=ParseMode.HTML,
                )
                return
            series_id = series.id
    if flags.opts.get("ep"):
        parsed = parse_episode_ref(flags.opts["ep"])
        if parsed is None:
            await message.reply_text("⛔ --ep must look like S01E04.")
            return
        season, episode = parsed

    try:
        profile = _flag(flags.opts.get("profile"), set(PROFILES), "profile")
        resolution = _flag(flags.opts.get("res"), set(RESOLUTIONS), "res")
        out_format = _flag(flags.opts.get("out"), {"mp4", "mkv"}, "out")
        target_size = int(flags.opts["size"]) if flags.opts.get("size") else None
        if target_size is not None and target_size < 1:
            raise ValueError("--size must be a positive number of MiB")
    except ValueError as exc:
        await message.reply_text(f"⚠️ {esc(exc)}")
        return

    async with session_scope(factory) as session:
        job = await jobs.create_job(
            session,
            source_type=source_type,
            source_url=source_url,
            source_file_id=source_file_id,
            filename=filename,
            owner_telegram_id=user.id,
            series_id=series_id,
            season_number=season,
            episode_number=episode,
            profile=profile or settings.default_encoding_profile,
            resolution=resolution,
            output_format=out_format or settings.default_output_format,
            target_size_mb=target_size,
        )
        await jobs.transition(session, job, "queued", "queued by administrator")
        job_id = job.id
        snapshot_text = render_job(job)

    reporter = StatusReporter(
        context.bot, message.chat_id, interval=settings.progress_update_interval
    )
    await reporter.send(snapshot_text, force=True)
    await record_status_message(factory, job_id, message.chat_id, reporter.message_id)

    await message.reply_text(
        f"{box('JOB CREATED')}\n\n  🔢 <b>#{job_id}</b> queued.\n"
        f"  Commands: <code>/progress {job_id}</code> • <code>/cancel {job_id}</code>\n"
        f"{footer}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_addlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addlink <url> - alias of /download for authorized source URLs."""
    if not context.args:
        await update.effective_message.reply_text("Usage: <code>/addlink &lt;url&gt;</code>", parse_mode=ParseMode.HTML)
        return
    await cmd_download(update, context)


@admin_only
async def cmd_encode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/encode <job_id> <profile> [--res 720p] [--out mkv] [--size 120]"""
    message = update.effective_message
    try:
        flags = parse_flags(context.args)
    except ValueError as exc:
        await message.reply_text(f"⚠️ {esc(exc)}")
        return
    if len(flags.positional) < 2:
        await message.reply_text(
            "Usage: <code>/encode &lt;job_id&gt; &lt;profile&gt;</code>\n"
            f"Profiles: {', '.join(PROFILES)} • resolutions: {', '.join(RESOLUTIONS)}",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        job_id = int(flags.positional[0])
        profile = flags.positional[1].lower()
    except ValueError:
        await message.reply_text("⛔ The job id must be a number.")
        return
    if profile not in PROFILES:
        await message.reply_text(f"⛔ Unknown profile. Choose from: {', '.join(PROFILES)}")
        return

    try:
        resolution = _flag(flags.opts.get("res"), set(RESOLUTIONS), "res")
        out_format = _flag(flags.opts.get("out"), {"mp4", "mkv"}, "out")
        target_size = int(flags.opts["size"]) if flags.opts.get("size") else None
    except ValueError as exc:
        await message.reply_text(f"⚠️ {esc(exc)}")
        return

    factory = context.bot_data["session_factory"]
    editable_states = {"pending", "queued", "downloaded", "failed", "cancelled", "paused"}
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        if job is None:
            await message.reply_text(f"⛔ No job #{job_id}.")
            return
        if job.state not in editable_states:
            await message.reply_text(
                f"⛔ Job #{job_id} is <b>{esc(job.state)}</b>; its encoding profile can only "
                "be changed before encoding starts.",
                parse_mode=ParseMode.HTML,
            )
            return
        job.profile = profile
        if resolution:
            job.resolution = resolution
        if out_format:
            job.output_format = out_format
        if target_size:
            job.target_size_mb = target_size
        job.output_path = None
        job.last_error = None
        job.error_code = None
        job.next_attempt_at = None
        if job.state in {"failed", "cancelled", "paused"}:
            await jobs.reset_for_retry(session, job, clear_error=True)
        else:
            await jobs.add_event(
                session, job_id, "info", f"profile set to {profile}", state=job.state
            )
        text = render_job(job)

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.bot_data["settings"]
    lines = [
        box("ANIMATRIXX PROFILES"),
        "",
        f"  <b>{esc('original')}</b>  keep the file, remux only",
        f"  <b>{esc('high')}</b>     CRF 18, quality first",
        f"  <b>{esc('balanced')}</b> CRF 23, the default",
        f"  <b>{esc('small')}</b>    CRF 28, smallest file",
        f"  <b>{esc('target')}</b>   two-pass, aims at --size MB",
        "",
        f"  Resolutions: {', '.join(RESOLUTIONS)} (never upscales)",
        f"  Default: {settings.default_encoding_profile} • {settings.default_output_format}",
        f"  Upload limit: {settings.upload_limit_mb} MiB"
        + (" (Local Bot API)" if settings.local_bot_api_enabled else " (hosted Bot API)"),
        footer,
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    manager = _manager(context)
    async with session_scope(factory) as session:
        counts = await jobs.state_counts(session)
        running = await jobs.jobs_by_states(
            session, ["downloading", "encoding", "uploading", "queued"], limit=15
        )
    lines = [box("ANIMATRIXX QUEUE"), ""]
    if counts:
        for state, count in sorted(counts.items()):
            lines.append(f"  • {esc(state):<16} {count}")
    else:
        lines.append("  No jobs yet. Create one with /download.")
    if running:
        lines += ["", "  <b>Active</b>"]
        for job in running:
            percent = f" {job.percent:.0f}%" if job.percent is not None else ""
            lines.append(f"    #{job.id} [{esc(job.state)}]{percent} {esc(job.filename or '')}")
    if manager.paused:
        lines += ["", "  ⏸ The dispatcher is PAUSED (/resume to continue)"]
    lines.append(footer)
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    job_id = None
    if context.args:
        try:
            job_id = int(context.args[0].lstrip("#"))
        except ValueError:
            await update.effective_message.reply_text("⛔ Usage: /progress [job_id]")
            return
    async with session_scope(factory) as session:
        if job_id is not None:
            job = await jobs.get_job(session, job_id)
        else:
            rows = await jobs.list_jobs(session, limit=1)
            job = rows[0] if rows else None
    if job is None:
        await update.effective_message.reply_text("No job found. Use /queue to list jobs.")
        return
    await update.effective_message.reply_text(render_job(job), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not context.args:
        await message.reply_text("Usage: <code>/cancel &lt;job_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    try:
        job_id = int(context.args[0].lstrip("#"))
    except ValueError:
        await message.reply_text("⛔ The job id must be a number.")
        return

    factory = context.bot_data["session_factory"]
    manager = _manager(context)
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        if job is None:
            await message.reply_text(f"⛔ No job #{job_id}.")
            return
        if job.state in {"completed", "cancelled"}:
            await message.reply_text(f"Job #{job_id} is already {job.state}.")
            return
        job.cancel_requested = True
        from app.workers.states import can_transition

        if can_transition(job.state, "cancelled"):
            await jobs.transition(session, job, "cancelled", "cancelled by administrator")
            result = f"Job #{job_id} cancelled."
        else:
            result = (
                f"Job #{job_id} already finished its active stage; "
                "no further work will run for it."
            )
    manager.request_cancel(job_id)
    await message.reply_text(f"🛑 {result}")


@admin_only
async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not context.args:
        await message.reply_text("Usage: <code>/retry &lt;job_id&gt;</code>", parse_mode=ParseMode.HTML)
        return
    try:
        job_id = int(context.args[0].lstrip("#"))
    except ValueError:
        await message.reply_text("⛔ The job id must be a number.")
        return

    factory = context.bot_data["session_factory"]
    manager = _manager(context)
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        if job is None:
            await message.reply_text(f"⛔ No job #{job_id}.")
            return
        if job.state in {"queued", "downloading", "encoding", "uploading"}:
            await message.reply_text(f"Job #{job_id} is already {job.state}.")
            return
        if job.state == "completed":
            await message.reply_text(f"Job #{job_id} is already completed.")
            return
        job.cancel_requested = False
        await jobs.reset_for_retry(session, job, clear_error=True)
        text = render_job(job)
    manager._cancel.discard(job_id)
    await message.reply_text(f"🔁 Re-queued.\n{text}", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        failed = await jobs.jobs_by_states(session, ["failed"], limit=15)
    lines = [box("ANIMATRIXX FAILED JOBS"), ""]
    if not failed:
        lines.append("  ✅ No failed jobs.")
    for job in failed:
        lines.append(
            f"  ❌ #{job.id} [{esc(job.state)}] {esc((job.last_error or '')[:90])}"
        )
        lines.append(f"     <code>/retry {job.id}</code> • <code>/progress {job.id}</code>")
    lines.append(footer)
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _manager(context).paused = True
    await update.effective_message.reply_text(
        "⏸ Dispatcher paused: queued jobs will not start.\n"
        "Stages already running finish first (FFmpeg cannot be paused mid-frame).\n"
        "Use /resume to continue."
    )


@admin_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _manager(context).paused = False
    await update.effective_message.reply_text("▶️ Dispatcher resumed.")


__all__ = [
    "cmd_download",
    "cmd_addlink",
    "cmd_encode",
    "cmd_profiles",
    "cmd_queue",
    "cmd_progress",
    "cmd_cancel",
    "cmd_retry",
    "cmd_jobs",
    "cmd_pause",
    "cmd_resume",
]
