"""``POST /realtime/tickets``: issues a single-use WebSocket connection ticket.

The WebSocket endpoint itself is served by the gateway process in
``app.realtime.gateway``, not by this API.

Design: TECHNICAL_DESIGN.md Section 9.4.
"""

from fastapi import APIRouter

router = APIRouter(tags=["realtime"])
