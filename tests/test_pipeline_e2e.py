"""End-to-end pipeline test: download -> encode -> archive -> cleanup.

Runs the real JobManager dispatcher against a local HTTP server and a fake
Telegram bot, so stage wiring and persistence are exercised without needing
network access or real credentials. FFmpeg/FFprobe are required for the
encode stage; the test skips itself when they are unavailable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.database import job_repositories as jobs
from app.database import repositories as repo
from app.database.session import build_engine, build_session_factory, init_db, session_scope
from app.workers.job_manager import JobManager

MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + os.urandom(32 * 1024)
PAYLOAD = MP4_BYTES  # replaced with a real encoded sample when FFmpeg is present

VIDEO_PATH = "/pipeline/source.mp4"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != VIDEO_PATH:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


class FakeMessage:
    def __init__(self, message_id: int = 1):
        self.id = message_id
        self.document = SimpleNamespace(file_id="fake-file-id", file_unique_id="fake-unique")
        self.text = ""

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self


class FakeBot:
    id = 424242
    username = "AnimatrixxTestBot"

    def __init__(self):
        self._next = 100
        self.sent_captions: list[str] = []
        self.uploaded: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        return FakeMessage()

    async def edit_message_text(self, text, **kwargs):
        return FakeMessage()

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        # read the payload exactly like the real bot would
        self.uploaded.append(document.read().decode("latin-1")[:16])
        self.sent_captions.append(caption or "")
        return FakeMessage(message_id=self._next)


def _generate_sample() -> bytes:
    """Encode a real 3s MP4 so the pipeline's FFprobe/FFmpeg stage is genuine."""
    if not FFMPEG:
        return MP4_BYTES
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sample.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                "-c:a", "aac", "-shortest", str(out),
            ],
            check=True,
        )
        return out.read_bytes()


@pytest.fixture(scope="module")
def server():
    global PAYLOAD
    PAYLOAD = _generate_sample()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port
    httpd.shutdown()
    thread.join(timeout=5)


def _binary(kind: str) -> str | None:
    override = os.environ.get(f"{kind.upper()}_BINARY")
    if override and Path(override).exists():
        return override
    found = shutil.which(kind)
    if found:
        return found
    candidate = Path.home() / "tools" / kind
    return str(candidate) if candidate.exists() else None


FFMPEG = _binary("ffmpeg")
FFPROBE = _binary("ffprobe")

needs_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and FFPROBE),
    reason="FFmpeg/FFprobe not available (set FFMPEG_BINARY and FFPROBE_BINARY)",
)


@pytest.fixture
async def pipeline(server, tmp_path):
    port = server
    settings = Settings(
        _env_file=None,
        bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg",
        temp_dir=tmp_path / "temp",
        log_dir=tmp_path / "logs",
        allow_private_downloads=True,
        allowed_download_ports=f"{port},80,443",
        max_download_size_mb=50,
        min_free_disk_mb=1,
        max_concurrent_downloads=1,
        max_concurrent_encodings=1,
        max_concurrent_uploads=1,
        poll_interval_seconds=0.2,
        progress_update_interval=0.1,
        autodel_enabled=True,
        ffmpeg_binary=FFMPEG or "ffmpeg",
        ffprobe_binary=FFPROBE or "ffprobe",
        metadata_cache_ttl=60,
    )
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = build_session_factory(engine)
    fake_bot = FakeBot()

    class StubApp:
        pass

    app = StubApp()
    app.bot_data = {"settings": settings, "session_factory": factory}
    app.bot = fake_bot
    manager = JobManager(app)
    yield SimpleNamespace(
        settings=settings, factory=factory, bot=fake_bot, manager=manager, port=port
    )
    await manager.stop()
    await engine.dispose()


async def wait_for_state(factory, job_id: str | int, target: str, timeout: float = 60.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    last = "?"
    while asyncio.get_running_loop().time() < deadline:
        async with session_scope(factory) as session:
            job = await jobs.get_job(session, int(job_id))
            last = job.state if job else "missing"
        if last == target:
            return last
        if last in {"failed", "cancelled"}:
            return last
        await asyncio.sleep(0.2)
    return last


@needs_ffmpeg
@pytest.mark.asyncio
async def test_full_pipeline_download_encode_archive_cleanup(pipeline):
    # 1. an admin registers a series mapped to a private archive channel
    async with session_scope(pipeline.factory) as session:
        channel = await repo.get_or_create_channel(session, -100999, title="Pipeline Archive")
        series = await repo.create_series(
            session, "Pipeline Test", series_type="anime", archive_channel_id=channel.id
        )
        series_id = series.id

    # 2. a job is queued against an authorized local source
    url = f"http://127.0.0.1:{pipeline.port}{VIDEO_PATH}"
    async with session_scope(pipeline.factory) as session:
        job = await jobs.create_job(
            session,
            source_type="url",
            source_url=url,
            owner_telegram_id=1,
            series_id=series_id,
            episode_number=1,
            profile="balanced",
            output_format="mp4",
        )
        await jobs.transition(session, job, "queued", "queued by administrator")
        job_id = job.id

    # 3. the dispatcher runs every stage
    await pipeline.manager.start()
    final = await wait_for_state(pipeline.factory, job_id, "completed", timeout=90)
    assert final == "completed", f"pipeline ended in state {final}"

    # 4. the archive record was persisted before temp files were cleaned
    async with session_scope(pipeline.factory) as session:
        job = await jobs.get_job(session, job_id)
        assert job.archive_chat_id == -100999
        assert job.archive_message_id is not None
        assert job.archive_file_id == "fake-file-id"
        assert job.work_path is None and job.output_path is None
        assert job.attempts == 0
        events = await jobs.event_messages(session, job_id)

    assert pipeline.bot.sent_captions, "nothing was uploaded"
    assert "Pipeline Test" in pipeline.bot.sent_captions[0]
    assert "S01E01" in pipeline.bot.sent_captions[0]

    # temp dir really is clean (AUTODEL_ENABLED=true)
    temp = Path(pipeline.settings.temp_dir)
    leftovers = [p for p in temp.rglob("*") if p.is_file()]
    assert leftovers == [], f"temp files left behind: {leftovers}"

    # 5. the state trail shows every stage ran
    joined = " | ".join(events)
    for expected in ("job created", "downloading stage started", "encoding stage started",
                     "uploading stage started", "cleanup stage completed"):
        assert expected in joined, f"missing event: {expected}\n{joined}"


@needs_ffmpeg
@pytest.mark.asyncio
async def test_pipeline_fails_permanently_on_bad_source(pipeline):
    async with session_scope(pipeline.factory) as session:
        job = await jobs.create_job(
            session,
            source_type="url",
            source_url=f"http://127.0.0.1:{pipeline.port}/nope.mp4",
            owner_telegram_id=1,
            profile="original",
        )
        await jobs.transition(session, job, "queued")
        job_id = job.id

    await pipeline.manager.start()
    final = await wait_for_state(pipeline.factory, job_id, "failed", timeout=30)
    assert final == "failed"

    async with session_scope(pipeline.factory) as session:
        job = await jobs.get_job(session, job_id)
        assert job.attempts == 1  # a 404 is permanent: one attempt, no backoff
        assert job.next_attempt_at is None
        assert job.last_error
        assert job.state == "failed"
