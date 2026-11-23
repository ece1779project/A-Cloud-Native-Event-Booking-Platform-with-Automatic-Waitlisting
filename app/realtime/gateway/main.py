"""Gateway process entry point: ``uvicorn app.realtime.gateway.main:app``.

Serves ``/ws/v1`` and the gateway's own health and metrics endpoints.

Design: TECHNICAL_DESIGN.md Sections 10.2 and 13.3.
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, WebSocket
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import health
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis_client import create_redis
from app.db.engine import create_engine, create_session_factory
from app.realtime.gateway.connection import (
    ConnectionRegistry,
    handle_connection,
)
from app.realtime.gateway.fanout import run_fanout


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(
        service="websocket",
        environment=settings.environment,
        level=settings.log_level,
    )
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = create_redis(settings.redis_url)
    app.state.registry = ConnectionRegistry()
    fanout_task = asyncio.create_task(
        run_fanout(
            app.state.redis,
            app.state.registry,
            app.state.session_factory,
        )
    )

    yield

    fanout_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await fanout_task
    for connection in await app.state.registry.all():
        with contextlib.suppress(Exception):
            await connection.websocket.close(code=1012)
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    gateway = FastAPI(title="Event Booking WebSocket Gateway", lifespan=lifespan)
    gateway.include_router(health.router)

    @gateway.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @gateway.websocket("/ws/v1")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await handle_connection(websocket)

    return gateway


app = create_app()
