"""Health endpoints, mounted outside ``/api/v1``.

``/health`` is the scaffold endpoint used by the Compose health check. It
will be split into ``/health/live`` (no dependency calls) and
``/health/ready`` (dependency checks), and ``/metrics`` will be added.

Design: TECHNICAL_DESIGN.md Section 13.3.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
