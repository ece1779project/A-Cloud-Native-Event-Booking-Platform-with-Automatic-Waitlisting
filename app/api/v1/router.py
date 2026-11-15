"""Aggregates every v1 route module under ``/api/v1``.

A new route module is registered here and nowhere else.
"""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    auth,
    events,
    participations,
    realtime,
    sessions,
)

router = APIRouter(prefix="/api/v1")
router.include_router(accounts.router)
router.include_router(auth.router)
router.include_router(events.router)
router.include_router(sessions.router)
router.include_router(participations.router)
router.include_router(admin.router)
router.include_router(realtime.router)
