from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


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
    ) -> dict[str, Any]:
        now = time.time()

        async with self._lock:
            item = self._items.get(key)
            if item and (now - item.fetched_at) < refresh_seconds:
                return item.value

        value = await fetcher()

        async with self._lock:
            self._items[key] = CacheItem(value=value, fetched_at=time.time())

        return value

    async def clear(self) -> int:
        async with self._lock:
            removed = len(self._items)
            self._items.clear()
            return removed

    async def size(self) -> int:
        async with self._lock:
            return len(self._items)
