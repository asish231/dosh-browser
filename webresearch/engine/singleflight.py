"""singleflight.py — Async Request Coalescing and Deduplication.

When multiple tasks or subresources attempt to fetch the identical URL concurrently,
SingleFlight collapses them into a single in-flight network transfer.
All callers await the single future and receive the result, eliminating duplicate
bandwidth and CPU consumption.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self.total_calls: int = 0
        self.coalesced_calls: int = 0

    async def execute(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute `fn` if no task for `key` is in flight.

        If another task is already executing for `key`, await its result and return it.
        """
        async with self._lock:
            self.total_calls += 1
            if key in self._in_flight:
                self.coalesced_calls += 1
                fut = self._in_flight[key]
                # Await outside lock
                is_driver = False
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                is_driver = True

        if not is_driver:
            return await fut

        # Primary driver executes fn
        try:
            result = await fn()
            fut.set_result(result)
            return result
        except BaseException as exc:
            fut.set_exception(exc)
            raise
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)
