"""Job repository behaviour: creation, transitions, claiming, retry scheduling."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import job_repositories as jobs
from app.database.session import build_engine, build_session_factory, init_db, session_scope
from app.workers.states import InvalidTransition, JobState


@pytest.fixture
async def db(tmp_path):
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield build_session_factory(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_job_starts_pending_with_an_event(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=7, source_url="https://x/a.mp4")
        assert job.state == JobState.PENDING.value
        assert job.attempts == 0
        assert job.id > 0
    async with session_scope(db) as session:
        from app.database.models import JobEvent

        from sqlalchemy import select

        rows = await session.scalars(select(JobEvent.message).where(JobEvent.job_id == job.id))
        assert any("job created" in m for m in rows.all())


@pytest.mark.asyncio
async def test_transition_records_event_and_start_time(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=7)
        await jobs.transition(session, job, JobState.QUEUED, "queued")
        await jobs.transition(session, job, JobState.DOWNLOADING, "start")
        assert job.state == "downloading"
        assert job.started_at is not None
    async with session_scope(db) as session:
        stored = await jobs.get_job(session, job.id)
        assert stored.state == "downloading"


@pytest.mark.asyncio
async def test_invalid_transition_raises(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=7)
        job.state = JobState.COMPLETED.value
        await session.flush()
        with pytest.raises(InvalidTransition):
            await jobs.transition(session, job, JobState.QUEUED)


@pytest.mark.asyncio
async def test_pickable_only_returns_ready_jobs(db):
    async with session_scope(db) as session:
        queued = await jobs.create_job(session, owner_telegram_id=1)
        queued.state = JobState.QUEUED.value
        running = await jobs.create_job(session, owner_telegram_id=1)
        running.state = JobState.DOWNLOADING.value
        cancelled_job = await jobs.create_job(session, owner_telegram_id=1)
        cancelled_job.state = JobState.QUEUED.value
        cancelled_job.cancel_requested = True
        backoff = await jobs.create_job(session, owner_telegram_id=1)
        backoff.state = JobState.QUEUED.value
        backoff.next_attempt_at = datetime.utcnow() + timedelta(hours=1)
        await session.flush()

    async with session_scope(db) as session:
        ids = [j.id for j in await jobs.pickable(session)]
    assert queued.id in ids
    assert running.id not in ids
    assert cancelled_job.id not in ids
    assert backoff.id not in ids


@pytest.mark.asyncio
async def test_due_retry_is_discoverable(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=1)
        job.state = JobState.FAILED.value
        job.attempts = 1
        job.next_attempt_at = datetime.utcnow() - timedelta(seconds=5)
        await session.flush()

    async with session_scope(db) as session:
        due = await jobs.next_attempt_job(session)
        assert due is not None and due.id == job.id

    async with session_scope(db) as session:
        stored = await jobs.get_job(session, job.id)
        assert stored is not None and stored.id == job.id
        await jobs.reset_for_retry(session, stored)
        assert stored.state == JobState.QUEUED.value
        assert stored.next_attempt_at is None

    async with session_scope(db) as session:
        assert await jobs.next_attempt_job(session) is None


@pytest.mark.asyncio
async def test_reset_for_retry_clears_error(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=1)
        job.state = JobState.FAILED.value
        job.last_error = "boom"
        job.error_code = "network"
        await session.flush()
        await jobs.reset_for_retry(session, job)
        assert job.last_error is None
        assert job.error_code is None


@pytest.mark.asyncio
async def test_state_counts_and_list_filters(db):
    async with session_scope(db) as session:
        a = await jobs.create_job(session, owner_telegram_id=11)
        a.state = JobState.QUEUED.value
        b = await jobs.create_job(session, owner_telegram_id=22)
        b.state = JobState.FAILED.value
        await session.flush()

    async with session_scope(db) as session:
        counts = await jobs.state_counts(session)
        assert counts["queued"] == 1
        assert counts["failed"] == 1
        failed = await jobs.list_jobs(session, states=["failed"])
        assert [j.id for j in failed] == [b.id]
        mine = await jobs.list_jobs(session, owner_id=11)
        assert [j.id for j in mine] == [a.id]


@pytest.mark.asyncio
async def test_progress_updates_only_given_fields(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=1)
        await jobs.update_progress(session, job, done_bytes=500, total_bytes=1000, percent=50)
        assert (job.done_bytes, job.total_bytes, job.percent) == (500, 1000, 50.0)
        await jobs.update_progress(session, job, percent=75)
        assert job.done_bytes == 500
        assert job.percent == 75.0


@pytest.mark.asyncio
async def test_active_job_paths_lists_files_in_flight(db):
    async with session_scope(db) as session:
        job = await jobs.create_job(session, owner_telegram_id=1, work_path="/tmp/a.mp4")
        job.state = JobState.DOWNLOADING.value
        job.output_path = "/tmp/out.mp4"
        done = await jobs.create_job(session, owner_telegram_id=1, work_path="/tmp/b.mp4")
        done.state = JobState.COMPLETED.value
        await session.flush()

    async with session_scope(db) as session:
        paths = await jobs.active_job_paths(session)
    assert "/tmp/a.mp4" in paths
    assert "/tmp/out.mp4" in paths
    assert "/tmp/b.mp4" not in paths
