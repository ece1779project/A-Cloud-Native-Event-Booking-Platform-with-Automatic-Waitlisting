import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.errors import AppError, ErrorCode
from app.core.rate_limit import RateLimiter


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def key(window: int) -> str:
    return f"k:{window}"


async def test_local_fallback_fixed_window():
    clock = Clock(600.0)  # window 10 starts
    limiter = RateLimiter(clock)
    for _ in range(2):
        await limiter.check(None, key, 2)
    clock.t = 645.0
    with pytest.raises(AppError) as exc:
        await limiter.check(None, key, 2)
    assert exc.value.code == ErrorCode.RATE_LIMITED
    assert exc.value.headers["Retry-After"] == "15"
    assert limiter.degraded
    clock.t = 660.0
    await limiter.check(None, key, 2)


async def test_keys_are_counted_separately():
    limiter = RateLimiter(Clock(0.0))
    await limiter.check(None, lambda w: f"a:{w}", 1)
    await limiter.check(None, lambda w: f"b:{w}", 1)


class _BrokenPipe:
    async def __aenter__(self):
        raise RedisConnectionError("down")

    async def __aexit__(self, *a):
        return False


class _BrokenRedis:
    def pipeline(self, transaction=True):
        return _BrokenPipe()


async def test_redis_error_falls_back_to_local():
    limiter = RateLimiter(Clock(0.0))
    await limiter.check(_BrokenRedis(), key, 1)
    with pytest.raises(AppError):
        await limiter.check(_BrokenRedis(), key, 1)
    assert limiter.degraded
