"""Administrator routes: ``/admin/accounts`` and
``POST /admin/participations/{participation_id}/cancel``.

Design: TECHNICAL_DESIGN.md Sections 8.2 and 8.4.
"""

from fastapi import APIRouter

router = APIRouter(tags=["admin"])
