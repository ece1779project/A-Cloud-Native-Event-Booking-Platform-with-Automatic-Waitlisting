"""Redis connection factory with the short operation timeout used for
best-effort calls.

Design: TECHNICAL_DESIGN.md Sections 10.3 and 12.
"""

from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    # decode_responses=True returns str instead of bytes.
    # TODO(realtime): add the short operation timeout once its value is fixed.
    return Redis.from_url(redis_url, decode_responses=True)
