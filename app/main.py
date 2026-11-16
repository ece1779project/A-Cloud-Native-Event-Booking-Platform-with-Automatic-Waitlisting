"""API process entry point: ``uvicorn app.main:app``.

Builds the FastAPI application and owns its lifecycle (database engine,
Redis client, logging and metrics setup). Routes live in ``app.api``; no
route or business rule is defined here.

Design: TECHNICAL_DESIGN.md Sections 4.3 and 13.3.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.redis_client import create_redis
from app.db.engine import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = create_redis(settings.redis_url)

    yield

    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Event Booking API", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(v1_router)
    return app


app = create_app()
