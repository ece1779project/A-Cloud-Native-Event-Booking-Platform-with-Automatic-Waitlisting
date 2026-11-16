"""Values fixed by the design and deliberately not configurable: database lock
and statement timeouts, the limit of 20 offers per transaction, WebSocket
ticket lifetime, heartbeat and revalidation intervals, subscription limits,
the refresh grace window and list page sizes.

Changing a value here is a design change.

Design: TECHNICAL_DESIGN.md Sections 7.1, 10.2 and 13.5.
"""

# PostgreSQL connection settings (TECHNICAL_DESIGN 7.1).
DB_LOCK_TIMEOUT = "5s"
DB_STATEMENT_TIMEOUT = "10s"
DB_IDLE_IN_TRANSACTION_TIMEOUT = "15s"

# Promotion (TECHNICAL_DESIGN 7.4).
MAX_OFFERS_PER_TRANSACTION = 20

# Lists (PRD Section 8).
EVENT_LIST_PAGE_SIZE = 20

# Real-time and authentication (TECHNICAL_DESIGN 9.4, 13.5, 6.2).
WS_TICKET_TTL_SECONDS = 30
WS_HEARTBEAT_SECONDS = 25
REFRESH_ROTATION_GRACE_SECONDS = 5
