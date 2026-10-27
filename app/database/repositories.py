"""Data-access helpers. Handlers never talk to raw SQL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Administrator,
    ArchiveChannel,
    ArchivedMedia,
    Series,
    User,
    utcnow,
)
from app.utils.validation import normalize_title


# ----------------------------------------------------------------- users
async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.last_seen_at = utcnow()
    await session.flush()
    return user


async def count_users(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(User.id))) or 0)


async def list_user_ids(session: AsyncSession) -> Sequence[int]:
    return (await session.scalars(select(User.telegram_id))).all()


# ------------------------------------------------------------ admin list
async def get_admin_ids(session: AsyncSession) -> set[int]:
    rows = (await session.scalars(select(Administrator.telegram_id))).all()
    return set(rows)


async def add_admin(session: AsyncSession, telegram_id: int, note: str = "") -> bool:
    existing = await session.scalar(
        select(Administrator).where(Administrator.telegram_id == telegram_id)
    )
    if existing:
        return False
    session.add(Administrator(telegram_id=telegram_id, note=note or None))
    await session.flush()
    return True


# ---------------------------------------------------------------- series
async def get_series_by_title(session: AsyncSession, title: str) -> Optional[Series]:
    normalized = normalize_title(title)
    return await session.scalar(select(Series).where(Series.normalized_title == normalized))


async def get_series_by_id(session: AsyncSession, series_id: int) -> Optional[Series]:
    return await session.get(Series, series_id)


async def find_series(session: AsyncSession, query: str, limit: int = 20) -> Sequence[Series]:
    pattern = f"%{normalize_title(query)}%"
    return (
        await session.scalars(
            select(Series)
            .where(
                or_(
                    Series.normalized_title.like(pattern),
                    Series.title.ilike(f"%{query}%"),
                )
            )
            .order_by(Series.title)
            .limit(limit)
        )
    ).all()


async def create_series(
    session: AsyncSession,
    title: str,
    series_type: str = "anime",
    provider: str = "manual",
    external_id: Optional[str] = None,
    poster_url: Optional[str] = None,
    synopsis: Optional[str] = None,
    release_year: Optional[int] = None,
    alt_titles: Optional[list[str]] = None,
    archive_channel_id: Optional[int] = None,
) -> Series:
    series = Series(
        title=title.strip(),
        normalized_title=normalize_title(title),
        series_type=series_type,
        provider=provider,
        external_id=external_id,
        poster_url=poster_url,
        synopsis=synopsis,
        release_year=release_year,
        alt_titles=alt_titles or [],
        archive_channel_id=archive_channel_id,
    )
    session.add(series)
    await session.flush()
    return series


async def list_series(session: AsyncSession, limit: int = 100) -> Sequence[Series]:
    return (await session.scalars(select(Series).order_by(Series.title).limit(limit))).all()


async def series_without_channel(session: AsyncSession) -> Sequence[Series]:
    return (
        await session.scalars(
            select(Series)
            .where(Series.archive_channel_id.is_(None))
            .order_by(Series.title)
        )
    ).all()


async def delete_series(session: AsyncSession, title: str) -> Optional[Series]:
    """Delete a series row. The Telegram channel and its messages are untouched."""
    series = await get_series_by_title(session, title)
    if series is None:
        return None
    await session.delete(series)
    await session.flush()
    return series


async def attach_archive_channel(
    session: AsyncSession, title: str, channel: ArchiveChannel
) -> Optional[Series]:
    series = await get_series_by_title(session, title)
    if series is None:
        return None
    series.archive_channel = channel
    series.updated_at = utcnow()
    await session.flush()
    return series


# -------------------------------------------------------------- channels
async def get_or_create_channel(
    session: AsyncSession,
    chat_id: int,
    title: Optional[str] = None,
    username: Optional[str] = None,
) -> ArchiveChannel:
    channel = await session.scalar(
        select(ArchiveChannel).where(ArchiveChannel.telegram_chat_id == chat_id)
    )
    if channel is None:
        channel = ArchiveChannel(
            telegram_chat_id=chat_id, title=title, username=username
        )
        session.add(channel)
    else:
        if title:
            channel.title = title
        if username:
            channel.username = username
        channel.status = "active"
        channel.verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.flush()
    return channel


async def get_channel_by_id(session: AsyncSession, channel_id: int) -> Optional[ArchiveChannel]:
    return await session.get(ArchiveChannel, channel_id)


async def list_channels(session: AsyncSession) -> Sequence[ArchiveChannel]:
    return (await session.scalars(select(ArchiveChannel).order_by(ArchiveChannel.title))).all()


# ----------------------------------------------------------- archive rows
async def add_archived_media(
    session: AsyncSession,
    series_id: int,
    season_number: int,
    episode_number: int,
    **fields,
) -> tuple[ArchivedMedia, bool]:
    """Insert an archive record. Returns ``(row, created)``.

    ``created=False`` means a duplicate (same series/season/episode/resolution/
    profile) already exists - the caller decides whether to override.
    """
    existing = await session.scalar(
        select(ArchivedMedia).where(
            ArchivedMedia.series_id == series_id,
            ArchivedMedia.season_number == season_number,
            ArchivedMedia.episode_number == episode_number,
            ArchivedMedia.resolution == fields.get("resolution", "unknown"),
            ArchivedMedia.encoding_profile == fields.get("encoding_profile", "original"),
        )
    )
    if existing is not None:
        return existing, False
    row = ArchivedMedia(
        series_id=series_id,
        season_number=season_number,
        episode_number=episode_number,
        **fields,
    )
    session.add(row)
    await session.flush()
    return row, True


async def search_archive(
    session: AsyncSession,
    query: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    limit: int = 50,
) -> Sequence[ArchivedMedia]:
    normalized = normalize_title(query)
    stmt = (
        select(ArchivedMedia)
        .join(Series, ArchivedMedia.series_id == Series.id)
        .where(
            or_(
                Series.normalized_title.like(f"%{normalized}%"),
                Series.title.ilike(f"%{query}%"),
            )
        )
        .order_by(
            Series.title, ArchivedMedia.season_number, ArchivedMedia.episode_number
        )
        .limit(limit)
    )
    if season is not None:
        stmt = stmt.where(ArchivedMedia.season_number == season)
    if episode is not None:
        stmt = stmt.where(ArchivedMedia.episode_number == episode)
    return (await session.scalars(stmt)).all()


async def list_archive_for_series(
    session: AsyncSession, series_id: int
) -> Sequence[ArchivedMedia]:
    return (
        await session.scalars(
            select(ArchivedMedia)
            .where(ArchivedMedia.series_id == series_id)
            .order_by(ArchivedMedia.season_number, ArchivedMedia.episode_number)
        )
    ).all()


async def count_archived(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(ArchivedMedia.id))) or 0)


async def clear_unmapped(session: AsyncSession) -> int:
    """Remove series rows that were created by search but never registered."""
    result = await session.execute(
        delete(Series).where(
            Series.archive_channel_id.is_(None),
            Series.provider == "manual",
        )
    )
    return int(result.rowcount or 0)


__all__ = [
    "upsert_user",
    "count_users",
    "list_user_ids",
    "get_admin_ids",
    "add_admin",
    "get_series_by_title",
    "get_series_by_id",
    "find_series",
    "create_series",
    "list_series",
    "series_without_channel",
    "delete_series",
    "attach_archive_channel",
    "get_or_create_channel",
    "get_channel_by_id",
    "list_channels",
    "add_archived_media",
    "search_archive",
    "list_archive_for_series",
    "count_archived",
    "clear_unmapped",
]
