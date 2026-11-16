"""Event-session routes: public detail with availability, and organizer edit,
cancel and roster.

Design: TECHNICAL_DESIGN.md Section 8.3.
"""

from fastapi import APIRouter

router = APIRouter(tags=["sessions"])
