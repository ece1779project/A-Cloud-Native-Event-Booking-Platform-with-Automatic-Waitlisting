"""Values fixed by the design and deliberately not configurable: database lock
and statement timeouts, the limit of 20 offers per transaction, WebSocket
ticket lifetime, heartbeat and revalidation intervals, subscription limits,
the refresh grace window and list page sizes.

Changing a value here is a design change.

Design: TECHNICAL_DESIGN.md Sections 7.1, 10.2 and 13.5.
"""
