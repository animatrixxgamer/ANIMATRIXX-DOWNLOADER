"""Persistence helpers for pipeline jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import JobEvent, MediaJob, Series
from app.workers.states import (
    ACTIVE,
    PICKABLE,
    JobState,
    ensure_transition,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_job(session: AsyncSession, **fields) -> MediaJob:
    job = MediaJob(state=JobState.PENDING.value, **fields)
    session.add(job)
    await session.flush()
    await add_event(session, job.id, "info", "job created", state=job.state)
    return job


async def get_job(session: AsyncSession, job_id: int) -> Optional[MediaJob]:
    return await session.get(MediaJob, job_id)


async def add_event(
    session: AsyncSession,
    job_id: int,
    level: str,
    message: str,
    state: str | None = None,
) -> None:
    session.add(JobEvent(job_id=job_id, level=level, message=message[:2000], state=state))
    await session.flush()


async def transition(
    session: AsyncSession,
    job: MediaJob,
    target: JobState | str,
    message: str | None = None,
    *,
    allow_same: bool = False,
    force: bool = False,
) -> MediaJob:
    """Move a job to a new state, recording the reason as an event.

    ``force=True`` is reserved for crash recovery, which rolls an interrupted
    job back to its last stable state - a move the normal flow never makes.
    """
    target = JobState.parse(target)
    current = JobState.parse(job.state)
    if current != target:
        if not force:
            ensure_transition(current, target)
    elif not allow_same:
        return job
    job.state = target.value
    job.updated_at = _now()
    if target in {JobState.DOWNLOADING, JobState.ENCODING, JobState.UPLOADING} and job.started_at is None:
        job.started_at = _now()
    if target in {JobState.COMPLETED, JobState.CANCELLED}:
        job.finished_at = _now()
    if message:
        await add_event(session, job.id, "info", message, state=target.value)
    return job


async def pickable(
    session: AsyncSession,
    *,
    limit: int = 20,
    exclude_ids: Sequence[int] = (),
) -> Sequence[MediaJob]:
    """Jobs the dispatcher may claim right now."""
    states = [s.value for s in PICKABLE]
    now = _now()
    stmt = (
        select(MediaJob)
        .where(MediaJob.state.in_(states))
        .where(MediaJob.cancel_requested.is_(False))
        .where((MediaJob.next_attempt_at.is_(None)) | (MediaJob.next_attempt_at <= now))
        .order_by(MediaJob.id)
        .limit(limit)
    )
    if exclude_ids:
        stmt = stmt.where(MediaJob.id.notin_(list(exclude_ids)))
    return (await session.scalars(stmt)).all()


async def active_jobs(session: AsyncSession) -> Sequence[MediaJob]:
    states = [s.value for s in ACTIVE]
    return (
        await session.scalars(
            select(MediaJob).where(MediaJob.state.in_(states)).order_by(MediaJob.id)
        )
    ).all()


async def jobs_by_states(
    session: AsyncSession, states: Sequence[JobState | str], limit: int = 50
) -> Sequence[MediaJob]:
    values = [JobState.parse(s).value for s in states]
    return (
        await session.scalars(
            select(MediaJob)
            .where(MediaJob.state.in_(values))
            .order_by(MediaJob.updated_at.desc())
            .limit(limit)
        )
    ).all()


async def owned_job(session: AsyncSession, job_id: int, owner_id: int | None = None) -> Optional[MediaJob]:
    job = await session.get(MediaJob, job_id)
    if job is None:
        return None
    if owner_id is not None and job.owner_telegram_id not in (owner_id,):
        return None
    return job


async def state_counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(MediaJob.state, func.count(MediaJob.id)).group_by(MediaJob.state)
    )
    return {state: int(count) for state, count in rows.all()}


async def list_jobs(
    session: AsyncSession,
    *,
    states: Sequence[JobState | str] | None = None,
    owner_id: int | None = None,
    limit: int = 20,
) -> Sequence[MediaJob]:
    stmt = select(MediaJob).order_by(MediaJob.updated_at.desc()).limit(limit)
    if states:
        stmt = stmt.where(MediaJob.state.in_([JobState.parse(s).value for s in states]))
    if owner_id is not None:
        stmt = stmt.where(MediaJob.owner_telegram_id == owner_id)
    return (await session.scalars(stmt)).all()


async def series_of(session: AsyncSession, job: MediaJob) -> Optional[Series]:
    if job.series_id is None:
        return None
    return await session.get(Series, job.series_id)


async def active_job_paths(session: AsyncSession) -> list[str]:
    """Files that must never be deleted by cleanup."""
    rows = await session.scalars(
        select(MediaJob.work_path)
        .where(MediaJob.state.in_([s.value for s in ACTIVE]))
        .where(MediaJob.work_path.is_not(None))
    )
    paths = [p for p in rows.all() if p]
    rows = await session.scalars(
        select(MediaJob.output_path)
        .where(MediaJob.state.in_([s.value for s in ACTIVE]))
        .where(MediaJob.output_path.is_not(None))
    )
    return paths + [p for p in rows.all() if p]


async def next_attempt_job(session: AsyncSession) -> Optional[MediaJob]:
    """A failed job whose backoff window has elapsed."""
    return await session.scalar(
        select(MediaJob)
        .where(MediaJob.state == JobState.FAILED.value)
        .where(MediaJob.cancel_requested.is_(False))
        .where((MediaJob.next_attempt_at.is_not(None)) & (MediaJob.next_attempt_at <= _now()))
        .order_by(MediaJob.id)
        .limit(1)
    )


async def reset_for_retry(
    session: AsyncSession, job: MediaJob, *, clear_error: bool = True
) -> MediaJob:
    if clear_error:
        job.last_error = None
        job.error_code = None
    job.next_attempt_at = None
    job.percent = None
    job.stage_detail = None
    return await transition(session, job, JobState.QUEUED, "re-queued for another attempt")


async def event_messages(session: AsyncSession, job_id: int) -> list[str]:
    """Chronological event log for a job (no lazy relationship access)."""
    from sqlalchemy import select

    from app.database.models import JobEvent

    rows = await session.scalars(
        select(JobEvent.message).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    )
    return list(rows.all())


async def update_progress(
    session: AsyncSession,
    job: MediaJob,
    *,
    done_bytes: int | None = None,
    total_bytes: int | None = None,
    speed_bps: float | None = None,
    percent: float | None = None,
    stage_detail: str | None = None,
) -> None:
    if done_bytes is not None:
        job.done_bytes = done_bytes
    if total_bytes is not None:
        job.total_bytes = total_bytes
    if speed_bps is not None:
        job.speed_bps = speed_bps
    if percent is not None:
        job.percent = round(percent, 2)
    if stage_detail is not None:
        job.stage_detail = stage_detail
    job.updated_at = _now()


__all__ = [
    "create_job",
    "get_job",
    "owned_job",
    "add_event",
    "transition",
    "pickable",
    "active_jobs",
    "jobs_by_states",
    "state_counts",
    "list_jobs",
    "series_of",
    "active_job_paths",
    "next_attempt_job",
    "reset_for_retry",
    "event_messages",
    "update_progress",
]
