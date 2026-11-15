"""Account routes: ``POST /accounts`` and ``GET /me``.

Design: TECHNICAL_DESIGN.md Section 8.2.
"""

from fastapi import APIRouter

router = APIRouter(tags=["accounts"])
