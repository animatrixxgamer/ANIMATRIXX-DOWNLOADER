"""Crash recovery: what the bot does with jobs that were interrupted."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.database import job_repositories as jobs
from app.database.session import build_engine, build_session_factory, init_db, session_scope
from app.workers.job_manager import JobManager


class StubApp:
    def __init__(self, settings, factory):
        self.bot_data = {"settings": settings, "session_factory": factory}
        self.bot = None


@pytest.fixture
async def env(tmp_path):
    settings = Settings(
        _env_file=None,
        bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg",
        temp_dir=tmp_path / "temp",
        log_dir=tmp_path / "logs",
    )
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = build_session_factory(engine)
    manager = JobManager(StubApp(settings, factory))
    yield settings, factory, manager, tmp_path
    await engine.dispose()


async def make_job(factory, tmp_path, **overrides):
    force_state = overrides.pop("force_state")
    work = overrides.pop("work_file", None)
    output = overrides.pop("output_file", None)
    async with session_scope(factory) as session:
        job = await jobs.create_job(
            session,
            owner_telegram_id=1,
            source_type=overrides.pop("source_type", "url"),
            source_url=overrides.pop("source_url", "https://example.com/a.mp4"),
            filename="a.mp4",
            profile=overrides.pop("profile", "balanced"),
            **overrides,
        )
        if work is not None:
            path = tmp_path / "temp" / work
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0" * 64)
            job.work_path = str(path)
        if output is not None:
            path = tmp_path / "temp" / "encoded" / output
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"1" * 64)
            job.output_path = str(path)
        job.state = force_state
        await session.flush()
        return job.id


async def state_of(factory, job_id) -> str:
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        return job.state


async def events_of(factory, job_id) -> list[str]:
    from sqlalchemy import select

    from app.database.models import JobEvent

    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(JobEvent.message).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
        return list(rows.all())


@pytest.mark.asyncio
async def test_interrupted_download_is_requeued(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(factory, tmp_path, force_state="downloading", work_file="a.mp4.part")
    await manager.recover()
    assert await state_of(factory, job_id) == "queued"
    events = await events_of(factory, job_id)
    assert any("resume or restart" in e for e in events)


@pytest.mark.asyncio
async def test_interrupted_encode_discards_partial_output(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(
        factory, tmp_path, force_state="encoding",
        work_file="a.mp4", output_file="job1_a.mp4",
    )
    async with session_scope(factory) as session:
        job = await jobs.get_job(session, job_id)
        partial = Path(job.output_path)
    assert partial.exists()

    await manager.recover()

    assert await state_of(factory, job_id) == "downloaded"
    assert not partial.exists()
    events = await events_of(factory, job_id)
    assert any("re-encoding" in e for e in events)


@pytest.mark.asyncio
async def test_interrupted_encode_without_source_fails(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(factory, tmp_path, force_state="encoding")
    await manager.recover()
    assert await state_of(factory, job_id) == "failed"


@pytest.mark.asyncio
async def test_interrupted_upload_recovers_from_recorded_message(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(
        factory, tmp_path, force_state="uploading", work_file="a.mp4", archive_message_id=555
    )
    await manager.recover()
    assert await state_of(factory, job_id) == "archived"
    events = await events_of(factory, job_id)
    assert any("already succeeded" in e for e in events)


@pytest.mark.asyncio
async def test_interrupted_upload_with_file_is_retried(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(
        factory, tmp_path, force_state="uploading", work_file="a.mp4"
    )
    await manager.recover()
    assert await state_of(factory, job_id) == "encoded"


@pytest.mark.asyncio
async def test_interrupted_upload_without_file_fails(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(factory, tmp_path, force_state="uploading")
    await manager.recover()
    assert await state_of(factory, job_id) == "failed"
    events = await events_of(factory, job_id)
    assert any("lost" in e for e in events)


@pytest.mark.asyncio
async def test_cancel_requested_job_becomes_cancelled(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(factory, tmp_path, force_state="queued", cancel_requested=True)
    await manager.recover()
    assert await state_of(factory, job_id) == "cancelled"


@pytest.mark.asyncio
async def test_queued_job_survives_restart(env, tmp_path):
    _, factory, manager, _ = env
    job_id = await make_job(factory, tmp_path, force_state="queued")
    await manager.recover()
    assert await state_of(factory, job_id) == "queued"
    events = await events_of(factory, job_id)
    assert any("restored after restart" in e for e in events)


@pytest.mark.asyncio
async def test_terminal_jobs_are_untouched(env, tmp_path):
    _, factory, manager, _ = env
    for state in ("completed", "failed", "cancelled", "paused"):
        job_id = await make_job(factory, tmp_path, force_state=state)
        await manager.recover()
        assert await state_of(factory, job_id) == state
