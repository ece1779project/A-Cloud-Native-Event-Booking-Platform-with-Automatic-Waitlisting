"""Prometheus metrics endpoint for the API process.

The route is mounted outside ``/api/v1`` and is intended to remain internal
in deployed environments.

Design: TECHNICAL_DESIGN.md Sections 13.3 and 14.
"""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
