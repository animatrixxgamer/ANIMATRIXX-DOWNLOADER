"""Persistent job queue: stage execution, retries, crash recovery.

One dispatcher task claims jobs from the database, runs the stage that is
actually needed (download / encode / upload / cleanup) under per-stage
concurrency limits, and records every transition as a JobEvent. Because all
state lives in SQLite, a restart can resume where it left off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telegram.ext import Application

from app.database import job_repositories as jobs
from app.database.models import MediaJob
from app.database.session import session_scope
from app.services import cleanup as cleanup_service
from app.services.downloader import DownloadCancelled, DownloadError, download, sanitize_filename
from app.services.encoder import (
    EncodeCancelled,
    EncodeError,
    MediaInfo,
    ProbeError,
    encode,
    probe,
    profile_label,
)
from app.services.progress import StatusReporter, render_job
from app.services.telegram_storage import (
    UploadCancelled,
    UploadError,
    caption_for,
    preflight,
    upload,
)
from app.workers.states import (
    JobState,
    Stage,
    backoff_seconds,
    is_permanent_error,
    next_attempt_time,
    next_stage,
    should_retry,
)

logger = logging.getLogger(__name__)

STAGE_STATE = {
    Stage.DOWNLOAD: JobState.DOWNLOADING,
    Stage.ENCODE: JobState.ENCODING,
    Stage.UPLOAD: JobState.UPLOADING,
    Stage.CLEANUP: JobState.CLEANUP_PENDING,
}
STAGE_DONE = {
    Stage.DOWNLOAD: JobState.DOWNLOADED,
    Stage.ENCODE: JobState.ENCODED,
    Stage.UPLOAD: JobState.ARCHIVED,
    Stage.CLEANUP: JobState.COMPLETED,
}
STAGE_LABEL = {
    Stage.DOWNLOAD: "DOWNLOADING",
    Stage.ENCODE: "ENCODING",
    Stage.UPLOAD: "UPLOADING",
    Stage.CLEANUP: "CLEANUP",
}


@dataclass
class JobSnapshot:
    """Plain copy of a job row so stages never hold a detached ORM object."""

    id: int
    state: str
    source_type: str
    source_url: Optional[str]
    source_file_id: Optional[str]
    filename: Optional[str]
    work_path: Optional[str]
    output_path: Optional[str]
    profile: str
    resolution: Optional[str]
    output_format: Optional[str]
    target_size_mb: Optional[int]
    series_id: Optional[int]
    season_number: int
    episode_number: Optional[int]
    episode_title: Optional[str]
    owner_telegram_id: int
    archive_chat_id: Optional[int]
    archive_message_id: Optional[int]
    attempts: int
    status_chat_id: Optional[int]
    status_message_id: Optional[int]
    percent: Optional[float]
    total_bytes: Optional[int]
    done_bytes: Optional[int]
    speed_bps: Optional[float]
    stage_detail: Optional[str]
    last_error: Optional[str]

    @classmethod
    def from_job(cls, job: MediaJob) -> "JobSnapshot":
        return cls(
            id=job.id,
            state=job.state,
            source_type=job.source_type,
            source_url=job.source_url,
            source_file_id=job.source_file_id,
            filename=job.filename,
            work_path=job.work_path,
            output_path=job.output_path,
            profile=job.profile,
            resolution=job.resolution,
            output_format=job.output_format,
            target_size_mb=job.target_size_mb,
            series_id=job.series_id,
            season_number=job.season_number or 1,
            episode_number=job.episode_number,
            episode_title=job.episode_title,
            owner_telegram_id=job.owner_telegram_id,
            archive_chat_id=job.archive_chat_id,
            archive_message_id=job.archive_message_id,
            attempts=job.attempts,
            status_chat_id=job.status_chat_id,
            status_message_id=job.status_message_id,
            percent=job.percent,
            total_bytes=job.total_bytes,
            done_bytes=job.done_bytes,
            speed_bps=job.speed_bps,
            stage_detail=job.stage_detail,
            last_error=job.last_error,
        )


class JobManager:
    def __init__(self, app: Application) -> None:
        self.app = app
        self.settings = app.bot_data["settings"]
        self.factory = app.bot_data["session_factory"]
        self.bot = app.bot
        self.temp_dir = Path(self.settings.temp_dir)
        self._running: dict[int, Stage] = {}
        self._cancel: set[int] = set()
        self._stopping = False
        self._task: asyncio.Task | None = None
        self._stage_tasks: set[asyncio.Task] = set()
        self.paused = False  # /pause stops new stages from starting

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.recover()
        self._task = asyncio.create_task(self._loop(), name="animatrixx-jobs")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._stage_tasks:
            logger.info("Waiting for %s running job stage(s) to finish", len(self._stage_tasks))
            await asyncio.gather(*list(self._stage_tasks), return_exceptions=True)

    def request_cancel(self, job_id: int) -> None:
        self._cancel.add(job_id)

    def is_running(self, job_id: int) -> bool:
        return job_id in self._running

    # ------------------------------------------------------------- recovery
    async def recover(self) -> None:
        """Reconcile jobs that were interrupted by a crash or restart."""
        recovered = 0
        async with session_scope(self.factory) as session:
            active = await jobs.active_jobs(session)
            for job in active:
                state = JobState.parse(job.state)

                if job.cancel_requested and state != JobState.CANCELLED:
                    if state in {JobState.QUEUED, JobState.DOWNLOADING, JobState.ENCODING, JobState.UPLOADING}:
                        await jobs.transition(session, job, JobState.CANCELLED, "cancelled during restart")
                        recovered += 1
                    continue

                if state == JobState.DOWNLOADING:
                    # Partial downloads stay on disk; the Range header resumes them.
                    await jobs.transition(
                        session, job, JobState.QUEUED,
                        "recovered after restart: download will resume or restart",
                        force=True,
                    )
                    recovered += 1
                elif state == JobState.ENCODING:
                    if job.output_path:
                        Path(job.output_path).unlink(missing_ok=True)
                        job.output_path = None
                    if job.work_path and Path(job.work_path).exists():
                        await jobs.transition(
                            session, job, JobState.DOWNLOADED,
                            "recovered after restart: partial encode discarded, re-encoding",
                            force=True,
                        )
                    else:
                        job.last_error = "The source file was lost during the restart."
                        job.error_code = "missing_file"
                        await jobs.transition(session, job, JobState.FAILED, job.last_error)
                    recovered += 1
                elif state == JobState.UPLOADING:
                    if job.archive_message_id:
                        await jobs.transition(
                            session, job, JobState.ARCHIVED,
                            "recovered after restart: upload had already succeeded",
                        )
                    elif job.output_path and Path(job.output_path).exists() or (
                        job.work_path and Path(job.work_path).exists()
                    ):
                        await jobs.transition(
                            session, job, JobState.ENCODED,
                            "recovered after restart: upload will be retried",
                            force=True,
                        )
                    else:
                        job.last_error = "The local file was lost before the upload finished."
                        job.error_code = "missing_file"
                        await jobs.transition(session, job, JobState.FAILED, job.last_error)
                    recovered += 1
                elif state in {JobState.QUEUED, JobState.DOWNLOADED, JobState.ENCODED, JobState.ARCHIVED}:
                    if state in {JobState.QUEUED, JobState.DOWNLOADED, JobState.ENCODED} and job.attempts == 0:
                        await jobs.add_event(session, job.id, "info", "job restored after restart", state=job.state)
                    recovered += 1

        if recovered:
            logger.info("Recovered %s interrupted job(s)", recovered)

    # ------------------------------------------------------------- dispatch
    async def _loop(self) -> None:
        interval = self.settings.poll_interval_seconds
        while not self._stopping:
            try:
                await self._retry_due()
                await self._dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - the loop must survive anything
                logger.exception("Job dispatcher iteration failed")
            await asyncio.sleep(interval)

    async def _retry_due(self) -> None:
        async with session_scope(self.factory) as session:
            job = await jobs.next_attempt_job(session)
            while job is not None:
                if job.attempts >= self.settings.max_job_attempts:
                    job.next_attempt_at = None
                else:
                    await jobs.reset_for_retry(session, job)
                job = await jobs.next_attempt_job(session)

    async def _dispatch_once(self) -> None:
        if self.paused:
            return
        limits = {
            Stage.DOWNLOAD: self.settings.max_concurrent_downloads,
            Stage.ENCODE: self.settings.max_concurrent_encodings,
            Stage.UPLOAD: self.settings.max_concurrent_uploads,
            Stage.CLEANUP: 1,
        }
        counts: dict[Stage, int] = {stage: 0 for stage in Stage}
        for stage in self._running.values():
            counts[stage] = counts.get(stage, 0) + 1

        async with session_scope(self.factory) as session:
            candidates = await jobs.pickable(session, exclude_ids=list(self._running))
            snapshots = [JobSnapshot.from_job(job) for job in candidates]

        for snap in snapshots:
            if snap.id in self._cancel:
                await self._mark_cancelled(snap.id)
                continue
            stage = await self._plan(snap)
            if stage is None:
                continue
            if counts.get(stage, 0) >= limits[stage]:
                continue
            counts[stage] = counts.get(stage, 0) + 1
            self._running[snap.id] = stage
            task = asyncio.create_task(self._run_stage(snap, stage), name=f"job-{snap.id}")
            self._stage_tasks.add(task)
            task.add_done_callback(self._stage_tasks.discard)

    async def _plan(self, snap: JobSnapshot) -> Optional[Stage]:
        file_present = bool(snap.work_path and Path(snap.work_path).exists())
        output_present = bool(snap.output_path and Path(snap.output_path).exists())
        needs_download = snap.source_type in {"url", "telegram_file"}
        plan = next_stage(
            snap.state,
            needs_download=needs_download,
            file_present=file_present,
            output_present=output_present,
            profile=snap.profile,
            archived=bool(snap.archive_message_id),
        )
        if plan.stage is None and not file_present and not output_present and needs_download:
            # Nothing on disk and the source is gone: fail loudly instead of looping.
            await self._fail(snap.id, "The working file is missing and cannot be recreated.", "missing_file", permanent=True)
        return plan.stage

    # ------------------------------------------------------------- execution
    async def _run_stage(self, snap: JobSnapshot, stage: Stage) -> None:
        name = f"job-{snap.id}"
        task = asyncio.current_task()
        if task:
            task.set_name(name)
        reporter = await self._reporter(snap)
        try:
            active_state = STAGE_STATE[stage]
            async with session_scope(self.factory) as session:
                job = await jobs.get_job(session, snap.id)
                if job is None:
                    return
                if snap.id in self._cancel or job.cancel_requested:
                    await jobs.transition(session, job, JobState.CANCELLED, "cancelled before start")
                    await reporter.fail("Job cancelled.")
                    return
                await jobs.transition(
                    session, job, active_state,
                    f"{STAGE_LABEL[stage].lower()} stage started",
                    allow_same=True,
                )
                job.stage_detail = STAGE_LABEL[stage]

            updates = await self._execute(snap, stage, reporter)

            async with session_scope(self.factory) as session:
                job = await jobs.get_job(session, snap.id)
                if job is None:
                    return
                for key, value in updates.items():
                    setattr(job, key, value)
                if job.cancel_requested:
                    await jobs.transition(session, job, JobState.CANCELLED, "cancelled during processing")
                else:
                    await jobs.transition(
                        session, job, STAGE_DONE[stage],
                        f"{STAGE_LABEL[stage].lower()} stage completed",
                    )
                    job.percent = None
                    job.stage_detail = None
            if stage in {Stage.DOWNLOAD, Stage.ENCODE, Stage.UPLOAD}:
                await reporter.send(render_job(await self._reload(snap.id)), force=True)

        except (DownloadCancelled, EncodeCancelled, UploadCancelled):
            await self._mark_cancelled(snap.id, reporter)
        except DownloadError as exc:
            await self._fail(snap.id, exc.message, exc.code, permanent=exc.permanent, reporter=reporter, snap=snap)
        except (EncodeError, UploadError) as exc:
            await self._fail(snap.id, exc.message, exc.code, permanent=getattr(exc, "permanent", False), reporter=reporter, snap=snap)
        except ProbeError as exc:
            await self._fail(
                snap.id, exc.message, "probe_failed", permanent=exc.permanent,
                reporter=reporter, snap=snap,
            )
        except Exception as exc:  # pragma: no cover - unexpected but must not kill the loop
            logger.exception("Job %s stage %s crashed", snap.id, stage.value)
            await self._fail(snap.id, f"Unexpected error: {exc}", "internal", permanent=False, reporter=reporter, snap=snap)
        finally:
            self._running.pop(snap.id, None)

    async def _execute(self, snap: JobSnapshot, stage: Stage, reporter: StatusReporter) -> dict:
        if stage is Stage.DOWNLOAD:
            return await self._do_download(snap, reporter)
        if stage is Stage.ENCODE:
            return await self._do_encode(snap, reporter)
        if stage is Stage.UPLOAD:
            return await self._do_upload(snap, reporter)
        return await self._do_cleanup(snap)

    # ------------------------------------------------------------ download
    async def _do_download(self, snap: JobSnapshot, reporter: StatusReporter) -> dict:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        await reporter.send(render_job(await self._reload(snap.id)), force=True)

        if snap.source_type == "telegram_file":
            return await self._download_telegram_file(snap)

        message = await cleanup_service.ensure_free_space(
            self.temp_dir, 0, self.settings.min_free_disk_mb
        )
        if message:
            raise DownloadError(message, code="disk_space", status=None)

        flush = _ThrottledFlush(self, snap.id, reporter)
        result = await download(
            snap.source_url or "",
            self.temp_dir,
            max_bytes=self.settings.max_download_size_mb * 2**20,
            allow_private=self.settings.allow_private_downloads,
            allowed_ports=self.settings.allowed_ports,
            connect_timeout=self.settings.download_connect_timeout,
            read_timeout=self.settings.download_read_timeout,
            max_redirects=self.settings.max_redirects,
            progress=flush,
            should_cancel=lambda: snap.id in self._cancel,
            filename_hint=snap.filename,
        )
        await flush.flush(final=True)
        return {
            "work_path": str(result.path),
            "filename": result.filename,
            "total_bytes": result.bytes_written,
            "done_bytes": result.bytes_written,
            "source_etag": result.etag,
            "percent": None,
            "stage_detail": None,
        }

    async def _download_telegram_file(self, snap: JobSnapshot) -> dict:
        from telegram.error import TelegramError

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            tg_file = await self.bot.get_file(snap.source_file_id)
        except TelegramError as exc:
            raise DownloadError(f"Telegram could not open that file: {exc}", code="telegram_error") from exc

        size = getattr(tg_file, "file_size", None) or 0
        if size > self.settings.max_download_size_mb * 2**20:
            raise DownloadError(
                f"The file is {size // 2**20} MiB, above the configured limit.",
                code="size_limit",
            )
        name = sanitize_filename(snap.filename or f"telegram_{snap.id}.mp4")
        target = (self.temp_dir / name).resolve()
        if target.parent != self.temp_dir.resolve():  # pragma: no cover
            raise DownloadError("Refusing to write outside the temp directory.", code="invalid_url")
        try:
            await tg_file.download_to_drive(custom_path=str(target))
        except TelegramError as exc:
            raise DownloadError(f"Telegram download failed: {exc}", code="telegram_error") from exc
        return {
            "work_path": str(target),
            "filename": target.name,
            "total_bytes": target.stat().st_size,
            "done_bytes": target.stat().st_size,
            "percent": None,
        }

    # -------------------------------------------------------------- encode
    async def _do_encode(self, snap: JobSnapshot, reporter: StatusReporter) -> dict:
        src = Path(snap.work_path or "")
        if not src.exists():
            raise EncodeError("The source file for encoding is missing.", permanent=False)

        info = await probe(src, ffprobe=self.settings.ffprobe_binary)
        fmt = (snap.output_format or self.settings.default_output_format).lower()
        out_dir = self.temp_dir / "encoded"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"job{snap.id}_{src.stem}.{fmt}"

        await reporter.send(
            f"{render_job(await self._reload(snap.id))}\n\n"
            f"  ▶ Profile: {profile_label(snap.profile)}"
            + (f" • {snap.resolution}" if snap.resolution else "")
            + f" • source {info.aspect_note}"
            + (f" @ {info.fps:.3g}fps" if info.fps else ""),
            force=True,
        )

        flush = _EncodeFlush(self, snap.id, reporter, info)
        await encode(
            src,
            dst,
            profile=snap.profile,
            info=info,
            out_format=fmt,
            resolution=snap.resolution,
            target_size_mb=snap.target_size_mb,
            ffmpeg=self.settings.ffmpeg_binary,
            timeout=self.settings.encode_timeout_seconds,
            progress=flush,
            should_cancel=lambda: snap.id in self._cancel,
        )
        return {
            "output_path": str(dst),
            "total_bytes": dst.stat().st_size,
            "done_bytes": dst.stat().st_size,
            "resolution": info.resolution_label or snap.resolution,
            "percent": None,
            "stage_detail": None,
        }

    # -------------------------------------------------------------- upload
    async def _do_upload(self, snap: JobSnapshot, reporter: StatusReporter) -> dict:
        if snap.archive_message_id:
            return {}  # already archived - idempotent, no second post

        path = self._file_for_upload(snap)
        chat_id = snap.archive_chat_id
        series_title = None
        async with session_scope(self.factory) as session:
            job = await jobs.get_job(session, snap.id)
            series = await jobs.series_of(session, job) if job else None
            if series is not None:
                from app.database import repositories as series_repo

                series_title = series.title
                if chat_id is None:
                    channel = await series_repo.get_series_channel(session, series)
                    if channel is not None:
                        chat_id = channel.telegram_chat_id

        check = await preflight(
            bot=self.bot,
            chat_id=chat_id,
            path=path,
            upload_limit_mb=self.settings.upload_limit_mb,
            temp_dir=self.temp_dir,
            min_free_mb=self.settings.min_free_disk_mb,
            cancelled=snap.id in self._cancel,
        )
        if not check.ok:
            permanent = check.code in {"too_large", "no_channel", "empty_file"}
            raise UploadError(check.reason, code=check.code, permanent=permanent)

        caption = caption_for(
            series_title=series_title,
            season=snap.season_number,
            episode=snap.episode_number,
            resolution=snap.resolution,
            profile=snap.profile,
            filename=path.name,
        )
        await reporter.send(
            f"{render_job(await self._reload(snap.id))}\n\n  ⬆️ Uploading to the archive channel…",
            force=True,
        )
        outcome = await upload(
            self.bot,
            chat_id=chat_id,
            path=path,
            caption=caption,
            should_cancel=lambda: snap.id in self._cancel,
        )
        return {
            "archive_chat_id": outcome.chat_id,
            "archive_message_id": outcome.message_id,
            "archive_file_id": outcome.file_id,
            "stage_detail": None,
        }

    def _file_for_upload(self, snap: JobSnapshot) -> Path:
        output = Path(snap.output_path) if snap.output_path else None
        work = Path(snap.work_path) if snap.work_path else None
        chosen = output if output and output.exists() else work
        if chosen is None or not chosen.exists():
            raise UploadError(
                "There is no file left to upload. The job needs to re-acquire the media.",
                code="missing_file",
                permanent=False,
            )
        return chosen

    # -------------------------------------------------------------- cleanup
    async def _do_cleanup(self, snap: JobSnapshot) -> dict:
        if not self.settings.autodel_enabled:
            async with session_scope(self.factory) as session:
                await jobs.add_event(
                    session, snap.id, "info", "cleanup skipped: AUTODEL_ENABLED=false"
                )
            return {}
        paths = [p for p in (snap.work_path, snap.output_path) if p]
        removed = cleanup_service.delete_files([Path(p) for p in paths], self.temp_dir)
        logger.info("Job %s: removed %s temp file(s)", snap.id, removed)
        return {"work_path": None, "output_path": None}

    # ---------------------------------------------------------- failure/cancel
    async def _fail(
        self,
        job_id: int,
        message: str,
        code: str,
        *,
        permanent: bool,
        reporter: Optional[StatusReporter] = None,
        snap: Optional[JobSnapshot] = None,
    ) -> None:
        max_attempts = self.settings.max_job_attempts
        async with session_scope(self.factory) as session:
            job = await jobs.get_job(session, job_id)
            if job is None:
                return
            job.attempts += 1
            job.last_error = message[:1500]
            job.error_code = code
            retry = should_retry(job.attempts, max_attempts, permanent)
            if retry:
                job.next_attempt_at = next_attempt_time(
                    job.attempts, self.settings.retry_backoff_seconds
                )
            else:
                job.next_attempt_at = None
            await jobs.add_event(
                session,
                job_id,
                "error",
                f"attempt {job.attempts}/{max_attempts}: {message}",
                state=job.state,
            )
            try:
                await jobs.transition(session, job, JobState.FAILED)
            except Exception:  # already terminal
                logger.debug("Could not mark job %s failed", job_id, exc_info=True)
            job.percent = None
            job.stage_detail = None
            retry_note = (
                f"Retrying in {backoff_seconds(job.attempts, self.settings.retry_backoff_seconds)}s "
                f"(attempt {job.attempts}/{max_attempts})."
                if retry
                else f"Gave up after {job.attempts} attempt(s). Fix the cause, then use /retry {job_id}."
            )
        logger.warning("Job %s failed [%s]: %s", job_id, code, message)
        if reporter:
            await reporter.fail(f"{message}\n\n{retry_note}")

    async def _mark_cancelled(self, job_id: int, reporter: Optional[StatusReporter] = None) -> None:
        async with session_scope(self.factory) as session:
            job = await jobs.get_job(session, job_id)
            if job is None:
                return
            if job.state != JobState.CANCELLED.value:
                try:
                    await jobs.transition(session, job, JobState.CANCELLED, "cancelled by administrator")
                except Exception:
                    job.cancel_requested = True
            job.percent = None
            job.stage_detail = None
        self._cancel.discard(job_id)
        if reporter:
            await reporter.fail("Job cancelled.")

    # -------------------------------------------------------------- helpers
    async def _reload(self, job_id: int) -> Optional[MediaJob]:
        async with session_scope(self.factory) as session:
            return await jobs.get_job(session, job_id)

    async def _reporter(self, snap: JobSnapshot) -> StatusReporter:
        return StatusReporter(
            self.bot,
            snap.status_chat_id or snap.owner_telegram_id,
            interval=self.settings.progress_update_interval,
            message_id=snap.status_message_id,
        )

    async def attach_status_message(self, job_id: int, chat_id: int, message_id: int) -> None:
        """Record the progress message so later stages edit the same bubble."""
        await record_status_message(self.factory, job_id, chat_id, message_id)


class _ThrottledFlush:
    """Download progress -> DB + Telegram, at most every 2 seconds."""

    def __init__(self, manager: JobManager, job_id: int, reporter: StatusReporter) -> None:
        self.manager = manager
        self.job_id = job_id
        self.reporter = reporter
        self._last = 0.0

    async def __call__(self, done: int, total: Optional[int], speed: float) -> None:
        now = time.monotonic()
        if now - self._last < self.manager.settings.progress_update_interval:
            return
        self._last = now
        percent = (done / total * 100) if total else None
        async with session_scope(self.manager.factory) as session:
            job = await jobs.get_job(session, self.job_id)
            if job is None:
                return
            await jobs.update_progress(
                session,
                job,
                done_bytes=done,
                total_bytes=total,
                speed_bps=speed,
                percent=percent,
                stage_detail="downloading",
            )
            snapshot = JobSnapshot.from_job(job)
        await self.reporter.send(render_job(snapshot))

    async def flush(self, final: bool = False) -> None:
        if not final:
            return
        async with session_scope(self.manager.factory) as session:
            job = await jobs.get_job(session, self.job_id)
            if job is not None:
                await jobs.update_progress(session, job, stage_detail=None, percent=None)


class _EncodeFlush:
    """FFmpeg progress -> DB + Telegram, throttled."""

    def __init__(
        self, manager: JobManager, job_id: int, reporter: StatusReporter, info: MediaInfo
    ) -> None:
        self.manager = manager
        self.job_id = job_id
        self.reporter = reporter
        self.info = info
        self._last = 0.0

    async def __call__(self, percent: Optional[float], stage: str) -> None:
        now = time.monotonic()
        if now - self._last < self.manager.settings.progress_update_interval:
            return
        self._last = now
        async with session_scope(self.manager.factory) as session:
            job = await jobs.get_job(session, self.job_id)
            if job is None:
                return
            await jobs.update_progress(
                session, job, percent=percent, stage_detail="encoding", total_bytes=None
            )
            snapshot = JobSnapshot.from_job(job)
        await self.reporter.send(render_job(snapshot))


async def record_status_message(
    factory, job_id: int, chat_id: int, message_id: int
) -> None:
    """Remember the progress message so later stages edit the same bubble."""
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        if job is not None:
            job.status_chat_id = chat_id
            job.status_message_id = message_id


__all__ = ["JobManager", "JobSnapshot", "record_status_message", "STAGE_LABEL"]
