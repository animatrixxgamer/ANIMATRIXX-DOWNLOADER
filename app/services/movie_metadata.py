"""K-drama / movie metadata via TMDB (requires the admin's own TMDB_API_KEY).

TMDB only supplies metadata - never video files. Without a key the handlers
explain how to add one instead of failing silently.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.services.anime_metadata import MediaResult, MetadataError, SearchResult, TTLCache

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBNotConfigured(MetadataError):
    """Raised when the admin has not supplied a TMDB key."""


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return " ".join(text.split())


def parse_tmdb_tv(node: dict[str, Any]) -> MediaResult:
    poster = node.get("poster_path")
    air = node.get("first_air_date") or ""
    return MediaResult(
        provider="tmdb",
        media_id=str(node.get("id")),
        title=node.get("name") or node.get("original_name") or "Unknown title",
        media_type="drama",
        alt_titles=[node.get("original_name")] if node.get("original_name") else [],
        poster_url=(IMAGE_BASE + poster) if poster else None,
        synopsis=_clean(node.get("overview")),
        year=int(air[:4]) if air[:4].isdigit() else None,
        media_format="TV series",
        episodes=node.get("number_of_episodes"),
        status=node.get("status"),
        genres=[str(g) for g in (node.get("genre_ids") or [])],
        external_url=f"https://www.themoviedb.org/tv/{node.get('id')}",
    )


def parse_tmdb_movie(node: dict[str, Any]) -> MediaResult:
    poster = node.get("poster_path")
    release = node.get("release_date") or ""
    return MediaResult(
        provider="tmdb",
        media_id=str(node.get("id")),
        title=node.get("title") or node.get("original_title") or "Unknown title",
        media_type="movie",
        alt_titles=[node.get("original_title")] if node.get("original_title") else [],
        poster_url=(IMAGE_BASE + poster) if poster else None,
        synopsis=_clean(node.get("overview")),
        year=int(release[:4]) if release[:4].isdigit() else None,
        media_format="Movie",
        external_url=f"https://www.themoviedb.org/movie/{node.get('id')}",
    )


class TMDBClient:
    def __init__(self, api_key: str, timeout: float = 15.0, cache_ttl: int = 3600) -> None:
        self.api_key = (api_key or "").strip()
        self.cache = TTLCache(ttl=cache_ttl)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _require_key(self) -> None:
        if not self.configured:
            raise TMDBNotConfigured(
                "TMDB is not configured. Set TMDB_API_KEY in the bot's .env "
                "(free key from themoviedb.org) to enable K-drama and movie search."
            )

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_key()
        try:
            # absolute URL: a relative path would drop the "/3" API version
            response = await self._client.get(
                f"{TMDB_BASE}{path}",
                params={**params, "api_key": self.api_key, "language": "en-US"},
            )
        except httpx.HTTPError as exc:
            raise MetadataError(
                "TMDB is unreachable right now. Please try again shortly."
            ) from exc
        if response.status_code in (401, 404):
            raise MetadataError("TMDB rejected the API key. Check TMDB_API_KEY.")
        if response.status_code == 429:
            raise MetadataError("TMDB rate limit reached. Try again in a minute.")
        if response.status_code >= 400:
            raise MetadataError("TMDB request failed. Try a different query.")
        return response.json()

    async def search_tv(self, query: str, page: int = 1) -> SearchResult:
        cache_key = f"tv:{page}:{query.strip().lower()}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._get("/search/tv", {"query": query, "page": page})
        items = [parse_tmdb_tv(node) for node in data.get("results", [])[:10]]
        result = SearchResult(
            items=items,
            page=page,
            has_next=page < int(data.get("total_pages") or 1),
            provider="tmdb",
        )
        await self.cache.set(cache_key, result)
        return result

    async def search_movie(self, query: str, page: int = 1) -> SearchResult:
        cache_key = f"movie:{page}:{query.strip().lower()}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._get("/search/movie", {"query": query, "page": page})
        items = [parse_tmdb_movie(node) for node in data.get("results", [])[:10]]
        result = SearchResult(
            items=items,
            page=page,
            has_next=page < int(data.get("total_pages") or 1),
            provider="tmdb",
        )
        await self.cache.set(cache_key, result)
        return result


__all__ = ["TMDBClient", "TMDBNotConfigured", "parse_tmdb_tv", "parse_tmdb_movie"]
