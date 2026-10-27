"""Application factory: wiring, startup and shutdown."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from app.config import Settings
from app.database import repositories as repo
from app.database.session import build_engine, build_session_factory, init_db
from app.handlers import build_handlers, on_error
from app.services.anime_metadata import AniListClient
from app.services.movie_metadata import TMDBClient
from app.services.result_store import ResultStore

logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    """Create schema, cache admin ids, resolve our own username."""
    settings: Settings = app.bot_data["settings"]
    engine = app.bot_data["engine"]
    factory = app.bot_data["session_factory"]

    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    await init_db(engine)
    try:
        async with factory() as session:
            db_admins = await repo.get_admin_ids(session)
    except Exception:  # pragma: no cover
        logger.exception("Could not load administrator list")
        db_admins = set()
    app.bot_data["db_admin_ids"] = frozenset(db_admins)

    try:
        me = await app.bot.get_me()
        app.bot_data["settings"].bot_username = me.username or ""
        logger.info("Bot online as @%s (admins: %s)", me.username, sorted(settings.administrators | db_admins))
    except Exception:
        logger.exception(
            "Could not reach Telegram - check BOT_TOKEN and network connectivity"
        )
        raise


async def _post_shutdown(app: Application) -> None:
    await app.bot_data["anilist"].aclose()
    await app.bot_data["tmdb"].aclose()
    engine = app.bot_data.get("engine")
    if engine is not None:
        await engine.dispose()
    logger.info("Shutdown complete")


def build_application(settings: Settings) -> Application:
    """Build a fully wired Application (no polling yet)."""
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)

    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["engine"] = engine
    app.bot_data["session_factory"] = factory
    app.bot_data["anilist"] = AniListClient(cache_ttl=settings.metadata_cache_ttl)
    app.bot_data["tmdb"] = TMDBClient(settings.tmdb_api_key, cache_ttl=settings.metadata_cache_ttl)
    app.bot_data["results"] = ResultStore()
    app.bot_data["db_admin_ids"] = frozenset(settings.administrators)

    for handler in build_handlers():
        app.add_handler(handler)
    app.add_error_handler(on_error)

    if not app.bot_data["tmdb"].configured:
        logger.warning(
            "TMDB_API_KEY is not set - /drama and /movie will ask for a key instead of searching"
        )
    return app


__all__ = ["build_application"]
