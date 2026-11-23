"""Fixed one-minute-window rate limiter backed by Redis, with the in-process
fallback each API replica uses while Redis is unavailable.

The counter key embeds the window number (Unix seconds // 60). The API
increments it, sets a 120-second expiry when the counter is new, and rejects
the request when the count exceeds the limit. ``Retry-After`` is the number of
seconds left in the window. A fixed window can admit up to twice the limit
across a boundary; the limit protects capacity and is not a correctness rule.

Design: TECHNICAL_DESIGN.md Sections 9.5 and 10.5.
"""

import logging
import math
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import AppError, ErrorCode

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60
KEY_TTL_SECONDS = 120


class RateLimiter:
    """One instance per API process. Holds only the fallback counters."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._local: dict[str, int] = {}
        self._local_window: int | None = None
        self.degraded = False  # True while the last check fell back to local counters

    async def check(
        self, redis: Redis | None, key_for: Callable[[int], str], limit: int
    ) -> None:
        """Count one request; raise ``RATE_LIMITED`` (429) when over ``limit``.

        ``key_for(window)`` builds the Redis key (see ``app.core.redis_keys``).
        """
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        key = key_for(window)
        count = await self._incr_redis(redis, key)
        if count is None:
            count = self._incr_local(key, window)
        if count > limit:
            retry_after = max(1, math.ceil((window + 1) * WINDOW_SECONDS - now))
            log.info("rate limited", extra={"limit": limit, "degraded": self.degraded})
            raise AppError(
                ErrorCode.RATE_LIMITED, headers={"Retry-After": str(retry_after)}
            )

    async def _incr_redis(self, redis: Redis | None, key: str) -> int | None:
        if redis is None:
            self._set_degraded(True)
            return None
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, KEY_TTL_SECONDS, nx=True)
                count, _ = await pipe.execute()
        except RedisError, OSError:
            self._set_degraded(True)
            return None
        self._set_degraded(False)
        return int(count)

    def _incr_local(self, key: str, window: int) -> int:
        if window != self._local_window:
            self._local.clear()
            self._local_window = window
        self._local[key] = self._local.get(key, 0) + 1
        return self._local[key]

    def _set_degraded(self, value: bool) -> None:
        if value and not self.degraded:
            log.warning("rate limiter using in-process fallback; accuracy is degraded")
        self.degraded = value
