"""rate_limiter.py — Origin-Aware Rate Limiter (GCRA) for Dosh-Browser.

Implements the Generic Cell Rate Algorithm (GCRA) / Leaky Bucket per hostname.
Protects against 429 Too Many Requests and IP bans by enforcing an emission gap
and maximum burst allowance for outgoing HTTP requests.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, interval_ms: float = 50.0, burst: int = 5) -> None:
        self.interval = interval_ms / 1000.0
        self.burst = max(1, burst)
        self.tau = self.interval * (self.burst - 1)
        self._hosts: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._last_sweep = time.monotonic()
        self._sweep_interval = 60.0

    def _sweep(self, now: float) -> None:
        max_age = self.tau + 30.0
        dead = [h for h, tat in self._hosts.items() if now > tat and (now - tat) > max_age]
        for h in dead:
            self._hosts.pop(h, None)
        self._last_sweep = now

    async def check(self, host: str) -> float | None:
        """Check if request to host is allowed.

        Returns None if permitted immediately, or required wait time in seconds if throttled.
        """
        async with self._lock:
            now = time.monotonic()
            if now - self._last_sweep >= self._sweep_interval:
                self._sweep(now)

            tat = self._hosts.get(host, now)
            if tat > now + self.tau:
                wait_time = (tat - self.tau) - now
                return wait_time

            new_tat = max(now, tat) + self.interval
            self._hosts[host] = new_tat
            return None

    async def acquire(self, host: str) -> float:
        """Acquire clearance to send a request to host.

        Sleeps if necessary to respect rate limits, returning seconds delayed.
        """
        waited_total = 0.0
        while True:
            wait_time = await self.check(host)
            if wait_time is None or wait_time <= 0:
                return waited_total
            await asyncio.sleep(wait_time)
            waited_total += wait_time

