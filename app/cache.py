from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


_STALE_FALLBACK_TTL = 3600  # 1 hour — retry live crawl soon after a snapshot fallback


@dataclass(slots=True)
class CacheItem:
    value: dict[str, Any]
    fetched_at: float


class InMemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, CacheItem] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        key: str,
        refresh_seconds: int,
        fetcher: Callable[[], Awaitable[dict[str, Any]]],
        allow_stale_on_error: bool = False,
    ) -> dict[str, Any]:
        now = time.time()

        stale_item: CacheItem | None = None
        async with self._lock:
            item = self._items.get(key)
            if item:
                # Stale fallback results use a short TTL so a retry happens soon;
                # fresh results use the full configured TTL.
                item_ttl = _STALE_FALLBACK_TTL if item.value.get("stale") else refresh_seconds
                if (now - item.fetched_at) < item_ttl:
                    return _with_last_fresh_crawl(item.value, item.fetched_at)
            stale_item = item
        try:
            value = await fetcher()
        except Exception:
            if allow_stale_on_error and stale_item is not None:
                return _with_last_fresh_crawl(stale_item.value, stale_item.fetched_at, stale=True)
            raise
        fetched_at = time.time()

        async with self._lock:
            self._items[key] = CacheItem(value=value, fetched_at=fetched_at)

        return _with_last_fresh_crawl(value, fetched_at)

    async def clear(self) -> int:
        async with self._lock:
            removed = len(self._items)
            self._items.clear()
            return removed

    async def size(self) -> int:
        async with self._lock:
            return len(self._items)


def _with_last_fresh_crawl(
    value: dict[str, Any],
    fetched_at: float,
    stale: bool = False,
) -> dict[str, Any]:
    payload = dict(value)
    payload.setdefault(
        "last_fresh_crawl",
        datetime.fromtimestamp(
            fetched_at,
            tz=timezone.utc,
        ).isoformat(),
    )
    payload["stale"] = bool(payload.get("stale", False) or stale)
    return payload
