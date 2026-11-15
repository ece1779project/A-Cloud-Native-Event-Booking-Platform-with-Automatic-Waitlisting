"""Attendee command and query routes: book, join the waitlist, confirm, cancel,
decline, leave, ``my-participation``, ``/me/participations`` and
``GET /requests/{request_id}``.

Design: TECHNICAL_DESIGN.md Section 8.4.
"""

from fastapi import APIRouter

router = APIRouter(tags=["participations"])
