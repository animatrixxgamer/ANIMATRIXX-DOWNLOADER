"""Async engine / session management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database.models import Base


def build_engine(database_url: str) -> AsyncEngine:
    """Create an async engine, creating the SQLite parent directory if needed."""
    connect_args: dict = {}
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in database_url:
            # A single shared connection keeps in-memory test databases alive.
            kwargs["poolclass"] = StaticPool
        else:
            path = database_url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(database_url, connect_args=connect_args, **kwargs)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine: AsyncEngine) -> None:
    """Create tables if they do not exist (Alembic migrations land later)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transactional session: commits on success, rolls back on error."""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = ["build_engine", "build_session_factory", "init_db", "session_scope"]
