from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    # decode_responses=True returns str instead of bytes.
    return Redis.from_url(redis_url, decode_responses=True)
