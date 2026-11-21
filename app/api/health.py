"""Health endpoints, mounted outside ``/api/v1``.

``/health/live`` checks only that the event loop responds and calls no
dependency. ``/health/ready`` checks PostgreSQL and Redis and returns 503 when
either fails. Metrics are served separately by ``app.api.metrics``.

Design: TECHNICAL_DESIGN.md Section 13.3.
"""

import asyncio

from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.metrics import REDIS_OPERATION_FAILURES

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    checks: dict[str, str] = {}
    for name, check in (("database", _check_db), ("redis", _check_redis)):
        try:
            await asyncio.wait_for(check(request), timeout=2)
            checks[name] = "ok"
        except Exception as e:  # noqa: BLE001 - report any dependency failure
            checks[name] = f"error: {type(e).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = 503
    return {"status": "ok" if healthy else "unavailable", "checks": checks}


async def _check_db(request: Request) -> None:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis(request: Request) -> None:
    try:
        await request.app.state.redis.ping()
    except RedisError:
        REDIS_OPERATION_FAILURES.labels("health_ping").inc()
        raise
