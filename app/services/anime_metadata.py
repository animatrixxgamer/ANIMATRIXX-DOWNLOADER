"""Anime metadata via AniList's public GraphQL API (no key required).

Metadata providers never supply video files. When a title is found but no
authorised source or archive entry exists, the handlers say exactly that.
"""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

ANILIST_URL = "https://graphql.anilist.co"
USER_AGENT = "AnimatrixxAnimeBot/0.1 (+https://github.com/animatrixxgamer/ANIMATRIXX-DOWNLOADER)"

SEARCH_QUERY = """
query ($search: String, $page: Int, $perPage: Int, $type: MediaType) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage hasNextPage }
    media(search: $search, type: $type, sort: SEARCH_MATCH) {
      id idMal status episodes duration format season seasonYear
      averageScore genres description(asHtml: false)
      siteUrl startDate { year }
      title { romaji english native }
      synonyms
      coverImage { large extraLarge }
    }
  }
}
"""

LATEST_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage hasNextPage }
    media(type: ANIME, status: RELEASING, sort: START_DATE_DESC) {
      id idMal status episodes duration format season seasonYear
      averageScore genres description(asHtml: false)
      siteUrl startDate { year }
      title { romaji english native }
      synonyms
      coverImage { large extraLarge }
    }
  }
}
"""


class MetadataError(RuntimeError):
    """Friendly, non-technical failure surface for metadata lookups."""


@dataclass(slots=True)
class MediaResult:
    provider: str
    media_id: str
    title: str
    media_type: str = "anime"  # anime | drama | movie
    alt_titles: list[str] = field(default_factory=list)
    poster_url: Optional[str] = None
    synopsis: Optional[str] = None
    year: Optional[int] = None
    media_format: Optional[str] = None
    episodes: Optional[int] = None
    status: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    score: Optional[float] = None
    external_url: Optional[str] = None

    @property
    def external_id(self) -> str:
        return f"{self.provider}:{self.media_id}"


@dataclass(slots=True)
class SearchResult:
    items: list[MediaResult]
    page: int = 1
    has_next: bool = False
    provider: str = "anilist"


def _clean_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    # AniList returns plain text with occasional <br> / <i> tags.
    return (
        html.unescape(text.replace("<br>", "\n").replace("<br/>", "\n").replace("<i>", "").replace("</i>", ""))
        .strip()
    )


def parse_media(node: dict[str, Any], media_type: str = "anime") -> MediaResult:
    """Pure parser - unit tested without any network access."""
    title_block = node.get("title") or {}
    title = (
        title_block.get("english")
        or title_block.get("romaji")
        or title_block.get("native")
        or "Unknown title"
    )
    alts = [
        t
        for t in (
            title_block.get("romaji"),
            title_block.get("native"),
            *(node.get("synonyms") or []),
        )
        if t and t != title
    ]
    cover = node.get("coverImage") or {}
    poster = cover.get("large") or cover.get("extraLarge")
    start = node.get("startDate") or {}
    year = node.get("startDate", {}).get("year") or node.get("seasonYear")
    score = node.get("averageScore")
    return MediaResult(
        provider="anilist",
        media_id=str(node.get("id")),
        title=title,
        media_type=media_type,
        alt_titles=alts,
        poster_url=poster,
        synopsis=_clean_html(node.get("description")),
        year=year or (start.get("year") if start else None),
        media_format=node.get("format"),
        episodes=node.get("episodes"),
        status=node.get("status"),
        genres=list(node.get("genres") or []),
        score=(score / 10.0) if score else None,
        external_url=node.get("siteUrl"),
    )


class TTLCache:
    """Small async-safe TTL cache so we do not hammer provider APIs."""

    def __init__(self, ttl: int = 3600, maxsize: int = 256) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        async with self._lock:
            if len(self._data) >= self.maxsize:
                # drop the oldest handful of entries
                for old in sorted(self._data, key=lambda k: self._data[k][0])[: self.maxsize // 4]:
                    self._data.pop(old, None)
            self._data[key] = (time.monotonic() + self.ttl, value)


class AniListClient:
    """Thin async wrapper over AniList GraphQL with caching and timeouts."""

    def __init__(self, cache_ttl: int = 3600, timeout: float = 15.0) -> None:
        self.cache = TTLCache(ttl=cache_ttl)
        self._client = httpx.AsyncClient(
            base_url=ANILIST_URL,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "/", json={"query": query, "variables": variables}
            )
        except httpx.HTTPError as exc:
            raise MetadataError(
                "AniList is unreachable right now. Please try again shortly."
            ) from exc
        if response.status_code == 429:
            raise MetadataError("AniList rate limit reached. Try again in a minute.")
        if response.status_code >= 500:
            raise MetadataError("AniList is having server trouble. Try again later.")
        if response.status_code >= 400:
            raise MetadataError("AniList rejected the request. Try a different title.")
        payload = response.json()
        if payload.get("errors"):
            raise MetadataError("AniList returned an error for that query.")
        return payload.get("data") or {}

    async def _page(
        self,
        query: str,
        variables: dict[str, Any],
        cache_key: str,
        media_type: str = "anime",
    ) -> SearchResult:
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._post(query, variables)
        page = data.get("Page") or {}
        items = [parse_media(node, media_type) for node in (page.get("media") or [])]
        page_info = page.get("pageInfo") or {}
        result = SearchResult(
            items=items,
            page=int(page_info.get("currentPage") or variables.get("page") or 1),
            has_next=bool(page_info.get("hasNextPage")),
            provider="anilist",
        )
        await self.cache.set(cache_key, result)
        return result

    async def search(
        self, query: str, page: int = 1, per_page: int = 10, media_type: str = "anime"
    ) -> SearchResult:
        type_var = "ANIME" if media_type == "anime" else "TV"
        return await self._page(
            SEARCH_QUERY,
            {"search": query, "page": page, "perPage": per_page, "type": type_var},
            cache_key=f"search:{type_var}:{page}:{query.strip().lower()}",
            media_type=media_type,
        )

    async def latest(self, page: int = 1, per_page: int = 10) -> SearchResult:
        return await self._page(
            LATEST_QUERY,
            {"page": page, "perPage": per_page},
            cache_key=f"latest:{page}",
            media_type="anime",
        )


__all__ = [
    "AniListClient",
    "MediaResult",
    "MetadataError",
    "SearchResult",
    "TTLCache",
    "parse_media",
]
