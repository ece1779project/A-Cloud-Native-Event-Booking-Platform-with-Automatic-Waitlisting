"""API process entry point: ``uvicorn app.main:app``.

Design: TECHNICAL_DESIGN.md Sections 4.3 and 13.3.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, metrics
from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import HttpObservabilityMiddleware
from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimiter
from app.core.redis_client import create_redis
from app.db.engine import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.require_jwt_keys()
    configure_logging(
        service="api",
        environment=settings.environment,
        level=settings.log_level,
    )
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = create_redis(settings.redis_url)

    yield

    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Event Booking API", lifespan=lifespan)
    app.state.rate_limiter = RateLimiter()
    app.add_middleware(HttpObservabilityMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(v1_router)
    return app


app = create_app()
