"""Repository behaviour against a real (in-memory) SQLite database."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import repositories as repo
from app.database.session import build_engine, build_session_factory, init_db, session_scope


@pytest.fixture
async def db():
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = build_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_user_is_idempotent(db):
    async with session_scope(db) as session:
        first = await repo.upsert_user(session, 1, username="a", first_name="A")
        second = await repo.upsert_user(session, 1, username="b", first_name="B")
        assert first.id == second.id
        assert second.username == "b"
    async with session_scope(db) as session:
        assert await repo.count_users(session) == 1


@pytest.mark.asyncio
async def test_create_and_lookup_series(db):
    async with session_scope(db) as session:
        created = await repo.create_series(session, "Solo Leveling", series_type="anime")
    async with session_scope(db) as session:
        found = await repo.get_series_by_title(session, "solo leveling!")
        assert found is not None
        assert found.id == created.id
        assert found.normalized_title == "solo leveling"


@pytest.mark.asyncio
async def test_duplicate_series_title_rejected(db):
    async with session_scope(db) as session:
        await repo.create_series(session, "Solo Leveling")
    with pytest.raises(IntegrityError):
        async with session_scope(db) as session:
            await repo.create_series(session, "  SOLO   LEVELING!! ")


@pytest.mark.asyncio
async def test_channel_mapping_and_unmapped_list(db):
    async with session_scope(db) as session:
        channel = await repo.get_or_create_channel(session, -1001, title="Archive A")
        await repo.create_series(session, "Alpha", archive_channel_id=channel.id)
        await repo.create_series(session, "Beta")

    async with session_scope(db) as session:
        unmapped = await repo.series_without_channel(session)
        assert [s.title for s in unmapped] == ["Beta"]

    async with session_scope(db) as session:
        series = await repo.get_series_by_title(session, "Beta")
        channel = await repo.get_or_create_channel(session, -1002, title="Archive B")
        await repo.attach_archive_channel(session, series.title, channel)

    async with session_scope(db) as session:
        assert await repo.series_without_channel(session) == []


@pytest.mark.asyncio
async def test_duplicate_archived_episode_detection(db):
    async with session_scope(db) as session:
        series = await repo.create_series(session, "Alpha")
        first, created = await repo.add_archived_media(
            session, series.id, 1, 4, resolution="720p", encoding_profile="balanced"
        )
        assert created is True
        again, created_again = await repo.add_archived_media(
            session, series.id, 1, 4, resolution="720p", encoding_profile="balanced"
        )
        assert created_again is False
        assert again.id == first.id
        # a different resolution is not a duplicate
        _, other = await repo.add_archived_media(
            session, series.id, 1, 4, resolution="480p", encoding_profile="balanced"
        )
        assert other is True


@pytest.mark.asyncio
async def test_search_archive_filters_by_episode(db):
    async with session_scope(db) as session:
        series = await repo.create_series(session, "The Glory")
        for ep in (1, 2, 3):
            await repo.add_archived_media(session, series.id, 1, ep, resolution="1080p")
    async with session_scope(db) as session:
        rows = await repo.search_archive(session, "glory", season=1, episode=2)
        assert len(rows) == 1
        assert rows[0].episode_number == 2
        assert len(await repo.search_archive(session, "glory")) == 3
        assert await repo.search_archive(session, "no such title") == []


@pytest.mark.asyncio
async def test_clear_unmapped_only_removes_manual_rows(db):
    async with session_scope(db) as session:
        channel = await repo.get_or_create_channel(session, -1005, title="C")
        await repo.create_series(session, "Mapped", archive_channel_id=channel.id)
        await repo.create_series(session, "Orphan A")
        await repo.create_series(session, "Orphan B", provider="tmdb", external_id="1")
    async with session_scope(db) as session:
        removed = await repo.clear_unmapped(session)
        assert removed == 1  # the manual orphan only; provider rows are kept
        remaining = {s.title for s in await repo.list_series(session)}
        assert remaining == {"Mapped", "Orphan B"}


@pytest.mark.asyncio
async def test_delete_series_keeps_channel(db):
    async with session_scope(db) as session:
        channel = await repo.get_or_create_channel(session, -1009, title="Keep me")
        await repo.create_series(session, "Doomed", archive_channel_id=channel.id)
    async with session_scope(db) as session:
        deleted = await repo.delete_series(session, "doomed")
        assert deleted is not None
    async with session_scope(db) as session:
        channels = await repo.list_channels(session)
        assert len(channels) == 1
        assert channels[0].title == "Keep me"
        assert await repo.get_series_by_title(session, "doomed") is None
