"""Metadata parsing and caching - no network in unit tests."""

import asyncio

import pytest

from app.services.anime_metadata import MediaResult, MetadataError, TTLCache, parse_media
from app.services.movie_metadata import parse_tmdb_movie, parse_tmdb_tv

ANILIST_NODE = {
    "id": 151807,
    "title": {
        "romaji": "Solo Leveling",
        "english": "Solo Leveling",
        "native": "나 혼자만 레벨업",
    },
    "synonyms": ["Only I Level Up"],
    "description": "An underdog hunter rises.<br><i>Season 2</i>",
    "format": "TV",
    "status": "RELEASING",
    "episodes": 12,
    "genres": ["Action", "Fantasy"],
    "averageScore": 83,
    "startDate": {"year": 2024},
    "seasonYear": 2024,
    "coverImage": {"large": "https://img.example/l.jpg", "extraLarge": "https://img.example/x.jpg"},
    "siteUrl": "https://anilist.co/anime/151807",
}


def test_parse_media_prefers_english_title():
    media = parse_media(ANILIST_NODE)
    assert media.title == "Solo Leveling"
    assert media.provider == "anilist"
    assert media.media_id == "151807"
    assert media.year == 2024
    assert media.episodes == 12
    assert media.score == pytest.approx(8.3)
    assert media.genres == ["Action", "Fantasy"]
    assert media.poster_url.endswith("l.jpg")
    assert "나 혼자만 레벨업" in media.alt_titles
    assert "Only I Level Up" in media.alt_titles


def test_parse_media_cleans_html_description():
    media = parse_media(ANILIST_NODE)
    assert "<br>" not in media.synopsis
    assert "<i>" not in media.synopsis
    assert "Season 2" in media.synopsis


def test_parse_media_handles_missing_fields():
    media = parse_media({"id": 1, "title": {"romaji": "X"}})
    assert media.title == "X"
    assert media.synopsis is None
    assert media.poster_url is None
    assert media.score is None


def test_parse_media_falls_back_when_all_titles_missing():
    media = parse_media({"id": 2, "title": {}})
    assert media.title == "Unknown title"


def test_parse_tmdb_tv():
    node = {
        "id": 12345,
        "name": "The Glory",
        "original_name": "더 글로리",
        "overview": "  A story of revenge.  ",
        "first_air_date": "2022-12-30",
        "poster_path": "/abc.jpg",
        "genre_ids": [18],
        "status": "Ended",
        "number_of_episodes": 16,
    }
    media = parse_tmdb_tv(node)
    assert media.media_type == "drama"
    assert media.title == "The Glory"
    assert media.year == 2022
    assert media.poster_url.startswith("https://image.tmdb.org")
    assert media.synopsis == "A story of revenge."


def test_parse_tmdb_movie():
    node = {
        "id": 99,
        "title": "Parasite",
        "original_title": "기생충",
        "release_date": "2019-05-30",
        "overview": "Greed and class discrimination.",
        "poster_path": None,
    }
    media = parse_tmdb_movie(node)
    assert media.media_type == "movie"
    assert media.year == 2019
    assert media.poster_url is None


@pytest.mark.asyncio
async def test_ttl_cache_roundtrip():
    cache = TTLCache(ttl=60)
    await cache.set("k", [1, 2])
    assert await cache.get("k") == [1, 2]
    assert await cache.get("missing") is None


@pytest.mark.asyncio
async def test_ttl_cache_disabled_when_ttl_zero():
    cache = TTLCache(ttl=0)
    await cache.set("k", "v")
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_ttl_cache_expires():
    cache = TTLCache(ttl=1)
    await cache.set("k", "v")
    cache._data["k"] = (cache._data["k"][0] - 10, "v")  # force expiry
    assert await cache.get("k") is None


@pytest.mark.asyncio
async def test_ttl_cache_evicts_when_full():
    cache = TTLCache(ttl=600, maxsize=4)
    for i in range(10):
        await cache.set(f"k{i}", i)
    assert len(cache._data) <= 4


def test_metadata_error_is_friendly():
    assert issubclass(MetadataError, RuntimeError)
