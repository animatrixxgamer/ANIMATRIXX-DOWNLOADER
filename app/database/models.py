"""SQLAlchemy models for the Animatrixx library / archive."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every model."""


class User(Base):
    """Anyone who has started the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Administrator(Base):
    """Persisted administrator list (in addition to OWNER_ID / ADMIN_IDS)."""

    __tablename__ = "administrators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArchiveChannel(Base):
    """A private Telegram channel used as an archive destination."""

    __tablename__ = "archive_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | missing | denied
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    series: Mapped[list["Series"]] = relationship(back_populates="archive_channel")


class Series(Base):
    """A series (anime or K-drama) tracked in the library."""

    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("normalized_title", name="uq_series_normalized_title"),
        Index("ix_series_provider_external", "provider", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    alt_titles: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="manual")  # anilist | tmdb | manual
    external_id: Mapped[Optional[str]] = mapped_column(String(64))
    poster_url: Mapped[Optional[str]] = mapped_column(Text)
    synopsis: Mapped[Optional[str]] = mapped_column(Text)
    release_year: Mapped[Optional[int]] = mapped_column(Integer)
    series_type: Mapped[str] = mapped_column(String(32), default="anime")  # anime | kdrama | movie | other
    archive_channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_channels.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    archive_channel: Mapped[Optional[ArchiveChannel]] = relationship(back_populates="series")
    episodes: Mapped[list["ArchivedMedia"]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class ArchivedMedia(Base):
    """One episode / video stored in a Telegram archive channel."""

    __tablename__ = "archived_media"
    __table_args__ = (
        UniqueConstraint(
            "series_id",
            "season_number",
            "episode_number",
            "resolution",
            "encoding_profile",
            name="uq_archived_episode",
        ),
        Index("ix_archived_lookup", "series_id", "season_number", "episode_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, default=1)
    episode_number: Mapped[int] = mapped_column(Integer, default=0)
    episode_title: Mapped[Optional[str]] = mapped_column(String(512))

    original_filename: Mapped[Optional[str]] = mapped_column(String(512))
    archived_filename: Mapped[Optional[str]] = mapped_column(String(512))
    extension: Mapped[Optional[str]] = mapped_column(String(16))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration: Mapped[Optional[float]] = mapped_column(Float)
    video_codec: Mapped[Optional[str]] = mapped_column(String(64))
    resolution: Mapped[str] = mapped_column(String(16), default="unknown")
    encoding_profile: Mapped[str] = mapped_column(String(32), default="original")

    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    file_id: Mapped[Optional[str]] = mapped_column(Text)
    unique_file_id: Mapped[Optional[str]] = mapped_column(Text)

    checksum_original: Mapped[Optional[str]] = mapped_column(String(64))
    checksum_processed: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="archived")  # archived | missing | broken
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    series: Mapped[Series] = relationship(back_populates="episodes")


__all__ = [
    "Base",
    "User",
    "Administrator",
    "ArchiveChannel",
    "Series",
    "ArchivedMedia",
    "utcnow",
]
