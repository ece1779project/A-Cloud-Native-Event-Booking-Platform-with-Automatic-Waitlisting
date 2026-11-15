from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings
from app.core.redis import create_redis
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


app = FastAPI(title="Event Booking Platform", lifespan=lifespan)
app.include_router(health.router)
