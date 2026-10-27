"""In-memory store for search results so inline buttons can reference them.

Callback data is limited to 64 bytes, so results live server-side behind a
short random token instead of being stuffed into the button payload.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.anime_metadata import MediaResult


@dataclass
class StoredResults:
    query: str
    kind: str  # anime | drama | movie | latest
    provider: str
    items: list[MediaResult] = field(default_factory=list)
    page: int = 1
    has_next: bool = False
    expires_at: float = 0.0


class ResultStore:
    """Token -> search results, with a TTL so memory cannot grow forever."""

    def __init__(self, ttl: int = 1800, maxsize: int = 64) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[str, StoredResults] = {}

    def put(
        self,
        query: str,
        kind: str,
        provider: str,
        items: list[MediaResult],
        page: int = 1,
        has_next: bool = False,
    ) -> str:
        self._purge()
        token = secrets.token_hex(4)
        while token in self._data:  # pragma: no cover - astronomically unlikely
            token = secrets.token_hex(4)
        self._data[token] = StoredResults(
            query=query,
            kind=kind,
            provider=provider,
            items=items,
            page=page,
            has_next=has_next,
            expires_at=time.time() + self.ttl,
        )
        return token

    def get(self, token: str) -> Optional[StoredResults]:
        entry = self._data.get(token)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            self._data.pop(token, None)
            return None
        return entry

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, v in self._data.items() if now > v.expires_at]:
            self._data.pop(key, None)
        while len(self._data) >= self.maxsize:
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            self._data.pop(oldest, None)


__all__ = ["ResultStore", "StoredResults"]
