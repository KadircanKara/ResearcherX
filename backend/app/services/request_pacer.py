"""Spread request starts evenly so no second holds more than a fixed rate.

Jev's allowed rate on OpenRouter is 80 requests per second. One rerank call
sends ~100 requests at `jev_concurrency` in flight, and two calls at once (two
chat turns, or an eval running beside the server) went over that: 71 of 150
calls in one eval run failed open on 429. `jev_concurrency` bounds requests in
FLIGHT, not requests per SECOND, so it cannot hold the line by itself.

Spacing, not a token bucket: each start takes the next free slot one interval
after the last, and an idle pacer banks nothing, so no burst can exceed the
rate however long the process sat quiet.

Holds no asyncio primitive, so it is safe to share across event loops (the
module-level semaphore trap). A slot is reserved with no await between reading
and writing `_next`, which is what makes concurrent waiters queue rather than
collide. The limit is per process: uvicorn runs one worker, and an eval run is
a second process with its own pacer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.core.config import settings


class RequestPacer:
    def __init__(
        self,
        rate_per_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_s <= 0:
            raise ValueError(f"rate_per_s must be positive, got {rate_per_s}")
        self.rate_per_s = rate_per_s
        self._interval = 1.0 / rate_per_s
        self._clock = clock
        self._sleep = sleep
        self._next = float("-inf")

    async def wait(self) -> None:
        """Return when this caller may start its request."""
        now = self._clock()
        slot = max(now, self._next)
        self._next = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)


_jev_pacer: RequestPacer | None = None


def jev_pacer() -> RequestPacer:
    """The one pacer every Jev request in this process waits on."""
    global _jev_pacer
    if _jev_pacer is None:
        _jev_pacer = RequestPacer(settings.jev_rate_limit_per_s)
    return _jev_pacer
