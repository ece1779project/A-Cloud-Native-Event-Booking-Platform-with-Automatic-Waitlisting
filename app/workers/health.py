"""Worker-only liveness, PostgreSQL readiness, and Prometheus metrics server.

Redis and scan freshness deliberately do not affect readiness. Redis
publication is best effort, while scan freshness is reported by metrics and
alerts rather than by restarting a healthy process.

Design: TECHNICAL_DESIGN.md Section 13.3.
"""

import asyncio

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def create_health_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI(title="Event Booking Worker Health")

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(response: Response) -> dict[str, str]:
        try:
            async with asyncio.timeout(2):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - readiness reports all DB failures
            response.status_code = 503
            return {
                "status": "unavailable",
                "database": f"error: {type(exc).__name__}",
            }
        return {"status": "ok", "database": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
