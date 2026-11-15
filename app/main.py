"""API process entry point: ``uvicorn app.main:app``.

Builds the FastAPI application and owns its lifecycle (database engine,
Redis client, logging and metrics setup). Routes live in ``app.api``; no
route or business rule is defined here.

Design: TECHNICAL_DESIGN.md Sections 4.3 and 13.3.
"""

from fastapi import FastAPI

from app.api import health
from app.api.v1.router import router as v1_router


def create_app() -> FastAPI:
    app = FastAPI(title="Event Booking API")
    app.include_router(health.router)
    app.include_router(v1_router)
    return app


app = create_app()
