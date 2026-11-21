"""Prometheus metric definitions shared by the API, the workers and the
gateway. Labels use bounded values only; IDs belong in logs. Includes
``worker_last_scan_timestamp_seconds``, one series per scan loop.

Design: TECHNICAL_DESIGN.md Section 14.
"""

from prometheus_client import Counter, Gauge, Histogram

# API HTTP transport. ``route`` is the route template, never the raw path.

HTTP_REQUESTS = Counter(
    "http_requests",
    "HTTP requests completed by method, route template, and status.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration by method and route template.",
    ["method", "route"],
)

# Shared dependencies.
DB_POOL_CONNECTIONS = Gauge(
    "db_pool_connections",
    "Database pool connections by state.",
    ["state"],
)
REDIS_OPERATION_FAILURES = Counter(
    "redis_operation_failures",
    "Redis operations that failed by bounded operation name.",
    ["operation"],
)

# Workers. Label ``scan`` is expiry, closure, promotion or invariant.

WORKER_SCAN_DURATION = Histogram(
    "worker_scan_duration_seconds",
    "Duration of one scan pass, candidate query and processing included.",
    ["scan"],
)
WORKER_CANDIDATES = Counter(
    "worker_candidates",  # exported as worker_candidates_total
    "Candidates processed, by outcome: changed, noop, busy or error.",
    ["scan", "outcome"],
)
WORKER_OVERDUE_ITEMS = Gauge(
    "worker_overdue_items", "HELD and OFFERED rows at or past their deadline."
)
WORKER_OLDEST_OVERDUE = Gauge(
    "worker_oldest_overdue_seconds",
    "Age of the oldest row still past its deadline; 0 when none.",
)
WORKER_LAST_SCAN = Gauge(
    "worker_last_scan_timestamp_seconds",
    "Unix time of the last pass that finished without a dependency failure.",
    ["scan"],
)
PROMOTION_LATENCY = Histogram(
    "waitlist_promotion_latency_seconds",
    "Time from a freed seat (hold or offer deadline) to the committed offer.",
    ["cause"],
    buckets=(0.25, 0.5, 1, 2, 3, 5, 10, 30, 60),
)
CAPACITY_VIOLATIONS = Gauge(
    "capacity_violations", "Sessions whose occupied seats exceed capacity."
)

# Real-time gateway and post-commit publication.
REALTIME_PUBLISH_FAILURES = Counter(
    "realtime_publish_failures",
    "Redis Pub/Sub messages that could not be published after commit.",
    ["message_type"],
)
WEBSOCKET_CONNECTIONS = Gauge(
    "websocket_connections", "WebSocket connections currently open."
)
WEBSOCKET_MESSAGES = Counter(
    "websocket_messages",
    "WebSocket messages handled by direction and bounded message type.",
    ["direction", "message_type"],
)
WEBSOCKET_RECONNECTS = Counter(
    "websocket_reconnects",
    "Redis Pub/Sub reconnections made by a WebSocket gateway.",
)
