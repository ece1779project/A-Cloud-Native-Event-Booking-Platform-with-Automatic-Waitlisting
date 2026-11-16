"""Gateway process entry point: ``uvicorn app.realtime.gateway.main:app``.

Serves ``/ws/v1`` and the gateway's own health and metrics endpoints.

Design: TECHNICAL_DESIGN.md Sections 10.2 and 13.3.
"""
