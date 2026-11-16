"""Event routes: public list and detail, and organizer create, edit, publish,
cancel and ``POST /events/{event_id}/sessions``.

Design: TECHNICAL_DESIGN.md Section 8.3.
"""

from fastapi import APIRouter

router = APIRouter(tags=["events"])
